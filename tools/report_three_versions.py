"""Aggregate recorded provider usage and SSE outcomes, without inventing quality scores."""
import csv
import hashlib
import html
import json
import math
import statistics
from collections import Counter
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'evidence/optimization_20261021'
NAMES = {'original': '기존 과제', 'improved': '기존 개선본', 'optimized': '최적화 개선본'}
esc = lambda value: html.escape(str(value))


def p95(values):
    ordered = sorted(values)
    return ordered[max(0, math.ceil(.95 * len(ordered)) - 1)] if ordered else None


def provider_group(row):
    if row['endpoint'] == 'embeddings': return 'embedding'
    messages = row.get('messages') or []
    system = messages[0]['content'] if messages else ''
    return 'relevance' if system.startswith('운영 런북 검색 후보') else 'core'


def summarize(rows, telemetry):
    incidents = [r for r in rows if r['expected_targets']]
    incident_ids = {r['request_id'] for r in incidents}
    ids = {r['request_id'] for r in rows}
    providers = [t for t in telemetry if t['kind'] == 'provider' and t['request_id'] in ids]
    core = [t for t in providers if provider_group(t) == 'core']
    incident_core = [t for t in core if t['request_id'] in incident_ids]
    monitoring = [t for t in telemetry if t['kind'] == 'monitoring' and t['request_id'] in ids]
    times = [r['total_s'] for r in rows]
    result = {'requests': len(rows), 'completed': sum(r['status'] == 'ok' for r in rows),
        'mean_s': statistics.mean(times), 'median_s': statistics.median(times), 'p95_s': p95(times),
        'incident_requests': len(incidents), 'target_exact': sum(r['target_set_match'] for r in incidents),
        'missing_targets': sum(len(set(r['expected_targets']) - set(r['actual_targets'])) for r in incidents),
        'outside_scope_targets': sum(len(set(r['actual_targets']) - set(r['expected_targets'])) for r in incidents),
        'monitoring_calls': len(monitoring), 'draft_exposed_requests': sum(r['draft_exposed'] for r in incidents),
        'rewrite_requests': sum(r['validation_attempts'] > 1 for r in incidents),
        'core_model_calls_all': len(core), 'core_tokens_all': sum(t['usage'].get('total_tokens', 0) for t in core),
        'core_model_calls_incident': len(incident_core),
        'core_input_tokens_incident': sum(t['usage'].get('prompt_tokens', 0) for t in incident_core),
        'core_output_tokens_incident': sum(t['usage'].get('completion_tokens', 0) for t in incident_core),
        'core_tokens_incident': sum(t['usage'].get('total_tokens', 0) for t in incident_core),
        'name_recall_passed': sum(r.get('name_present') is True for r in rows),
        'name_recall_requests': sum(r['case_id'] == 'Q10B' for r in rows),
        'rejections': dict(Counter(err['code'] for r in rows for item in r['events']
            if item['event']['type'] == 'Validation' for err in item['event'].get('errors', []))),
        'provider_model_names': dict(Counter(t.get('model') for t in providers))}
    for group in ['relevance', 'embedding']:
        items = [t for t in providers if provider_group(t) == group]
        result[group + '_calls'] = len(items)
        result[group + '_tokens'] = sum(t['usage'].get('total_tokens', 0) for t in items)
    searches = [item['event'] for r in rows for item in r['events'] if item['event']['type'] == 'RunbookSearch']
    result['relevance_cache_hits'] = sum(e.get('relevance_usage', {}).get('cache_hits', 0) for e in searches)
    return result


def build():
    rows = json.loads((OUT / 'all_results.json').read_text())
    manifest = json.loads((OUT / 'manifest.json').read_text())
    assert len(rows) == 99, 'Incomplete comparison; do not silently drop failed requests'
    variants = {name: [r for r in rows if r['variant'] == name] for name in NAMES}
    keys = {name: {(r['case_id'], r['round'], r['question']) for r in group} for name,group in variants.items()}
    assert all(len(group) == 33 for group in variants.values())
    assert keys['original'] == keys['improved'] == keys['optimized']
    telemetry = {name: [json.loads(line) for line in (OUT/'telemetry'/(name+'.jsonl')).read_text().splitlines()]
                 for name in NAMES}
    summary = {name: summarize(variants[name], telemetry[name]) for name in NAMES}
    source_differences = {}
    fixture_errors = []
    fixture = json.loads((ROOT/'data/monitoring.json').read_text())
    for name in NAMES:
        source = OUT/'stages'/name
        source_differences[name] = [file for file,digest in manifest['sources'][name].items()
            if hashlib.sha256((source/file).read_bytes()).hexdigest() != digest]
        for event in telemetry[name]:
            if event['kind'] != 'monitoring': continue
            data = event['result']; expected = fixture[data['system_name']]
            if (data['cpu_percent'] != expected['cpu_base'] or data['memory_percent'] != expected['memory_base']
                    or data['alerts'] != expected['alerts'] or datetime.fromisoformat(data['last_checked']) != datetime.fromisoformat('2026-10-21T09:00:00+09:00')):
                fixture_errors.append({'variant': name, 'request_id': event['request_id']})
    assert not fixture_errors, fixture_errors
    assert not any(source_differences.values()), source_differences
    comparison = []
    for case in sorted({r['case_id'] for r in rows}):
        record = {'case_id': case, 'question': next(r['question'] for r in rows if r['case_id'] == case)}
        for name in NAMES:
            group = [r for r in variants[name] if r['case_id'] == case]
            record[name+'_median_s'] = statistics.median(r['total_s'] for r in group)
            record[name+'_completed'] = sum(r['status'] == 'ok' for r in group)
        record['optimized_vs_improved_pct'] = (record['optimized_median_s']/record['improved_median_s']-1)*100
        comparison.append(record)
    measurements = []
    for name in NAMES:
        for r in variants[name]:
            calls = [t for t in telemetry[name] if t['kind'] == 'provider' and t['request_id'] == r['request_id']]
            measurements.append({k:r[k] for k in ['request_id','variant','case_id','round','status','total_s',
                'actual_targets','actual_runbooks','target_set_match','validation_attempts','draft_exposed']} | {
                'core_calls':sum(provider_group(t)=='core' for t in calls),
                'core_tokens':sum(t['usage'].get('total_tokens',0) for t in calls if provider_group(t)=='core')})
    for filename,data in [('per_question.csv',comparison),('measurements.csv',measurements)]:
        with (OUT/filename).open('w',encoding='utf-8-sig',newline='') as stream:
            writer=csv.DictWriter(stream,fieldnames=list(data[0]),lineterminator='\n');writer.writeheader();writer.writerows(data)
    payload={'captured_at':datetime.now().astimezone().isoformat(),'sample':'controlled class sample; warm RAG cache',
        'results':summary,'source_differences':source_differences,'fixture_errors':fixture_errors,
        'independent_semantic_review':'pending','actual_invoice':'not_measured'}
    (OUT/'summary.json').write_text(json.dumps(payload,ensure_ascii=False,indent=2))
    metrics=[('완료 요청 / 33','completed'),('평균 응답시간(초)','mean_s'),('응답시간 중앙값(초)','median_s'),
        ('p95 응답시간(초)','p95_s'),('조회 대상 일치 / 24','target_exact'),('요청 범위 밖 대상 수','outside_scope_targets'),
        ('누락 대상 수','missing_targets'),('모니터링 호출','monitoring_calls'),('검수 전 장애 초안 노출 / 24','draft_exposed_requests'),
        ('재작성 요청 / 24','rewrite_requests'),('핵심 모델 호출(장애 24건)','core_model_calls_incident'),
        ('입력 토큰(장애 24건)','core_input_tokens_incident'),('출력 토큰(장애 24건)','core_output_tokens_incident'),
        ('핵심 모델 총 토큰(장애 24건)','core_tokens_incident'),('핵심 모델 총 토큰(전체 33건)','core_tokens_all'),
        ('별도 관련성 모델 호출','relevance_calls'),('별도 임베딩 호출','embedding_calls'),('관련성 캐시 재사용','relevance_cache_hits'),
        ('이름 회상 / 3','name_recall_passed')]
    csv_summary=[{'metric':title,**{name:summary[name][key] for name in NAMES}} for title,key in metrics]
    with (OUT/'comparison.csv').open('w',encoding='utf-8-sig',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=['metric',*NAMES],lineterminator='\n');writer.writeheader();writer.writerows(csv_summary)
    style='body{font:16px/1.75 Pretendard,-apple-system,"Apple SD Gothic Neo",sans-serif;color:#24334a;background:#f5f7fb;max-width:1160px;margin:32px auto;padding:24px}h1{font-size:28px}h2{font-size:22px;margin-top:30px}.card,details{padding:18px;background:white;border:1px solid #dce4ef;border-radius:12px;margin:14px 0}table{border-collapse:collapse;width:100%;background:white;font-size:14px}th,td{padding:10px;border-bottom:1px solid #dce4ef;text-align:left;vertical-align:top}th{background:#eaf0fa}pre{white-space:pre-wrap;word-break:break-word;font:13px/1.65 monospace}a{color:#2457a5}.note{background:#fff1d7;padding:18px;border-radius:12px}'
    body=[f'<!doctype html><html lang="ko"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>세 버전 실측 비교</title><style>{style}</style>',
        '<h1>기존 과제 · 기존 개선본 · 최적화 개선본</h1><p>2026-10-21 / 버전별 33건, 총 99건 / 실제 OpenAI + FastAPI/SSE + MCP / 고정 수업 샘플</p>',
        '<div class="note">별도 평가자의 의미·권고 적절성 검수는 미완료입니다. 처리 완료율과 자동 검수 통과는 답변 정답률이 아닙니다. Q08의 auth-service 대체 정책을 유지했습니다. 실서비스 장애·실제 조치 실행·실제 청구액을 입증하는 실험이 아닙니다.</div>',
        '<h2>측정 조건</h2><div class="card">'+''.join('<p>'+esc(note)+'</p>' for note in manifest['conditions'])+'</div>',
        '<h2>실측 결과</h2><table><tr><th>항목</th>'+''.join('<th>'+name+'</th>' for name in NAMES.values())+'</tr>']
    for title,key in metrics:
        values=[summary[name][key] for name in NAMES]
        body.append('<tr><td>'+esc(title)+'</td>'+''.join('<td>'+esc(f'{v:.3f}' if isinstance(v,float) else f'{v:,}')+'</td>' for v in values)+'</tr>')
    body.append('</table><p>지연시간은 완성 응답까지의 시간입니다. p95는 nearest-rank 방식입니다. 각 질문은 버전마다 3회 실행했습니다. 토큰은 제공자의 usage이며 관련성·임베딩은 핵심 모델과 분리합니다. 캐시 구축 비용은 이 측정 구간 밖입니다.</p>')
    old,new=summary['improved'],summary['optimized']
    body.append('<div class="card">기존 개선본 대비 최적화본: 평균 시간 '+f"{(new['mean_s']/old['mean_s']-1)*100:+.1f}%"+' · 장애 질문 핵심 토큰 '+f"{(new['core_tokens_incident']/old['core_tokens_incident']-1)*100:+.1f}%"+'</div>')
    body.append('<h2>질문별 시간 중앙값</h2><table><tr><th>질문</th>'+''.join('<th>'+name+'</th>' for name in NAMES.values())+'</tr>')
    for record in comparison:
        body.append('<tr><td>'+esc(record['case_id']+' '+record['question'])+'</td>'+''.join('<td>'+f"{record[name+'_median_s']:.3f}초"+'</td>' for name in NAMES)+'</tr>')
    body.append('</table><h2>검수 보존과 한계</h2><div class="card">모델은 짧은 절차·가설 번호만 선택합니다. 수치·출처·조건 유형은 코드가 구성한 뒤 기존 검수기로 검사합니다. CPU 지속 여부·배포 원인 미확인 조건을 유지합니다. 모르는 ID·임의 필드·완료 조치 표현은 거부합니다. 별도 모델 분석 요약은 코드의 현재값 비교로 대체했으며 인과관계 분석을 완료했다고 표현하지 않습니다.</div>')
    body.append('<p>반복 질문과 모델 출력의 변동, 네트워크 상태에 따라 결과가 달라질 수 있습니다. 한 번에 여러 최적화를 적용했으므로 각 변경의 기여율은 분리하지 않았습니다. 원본의 범위 밖 조회가 다시 발생하지 않을 수도 있으며 그 수치를 인위적으로 재현하지 않습니다.</p>')
    body.append('<h2>원문 응답 비교</h2>')
    for record in comparison:
        body.append('<details><summary>'+esc(record['case_id']+' '+record['question'])+'</summary>')
        for name,label in NAMES.items():
            r=next(r for r in variants[name] if r['case_id']==record['case_id'] and r['round']==1)
            body.append('<h3>'+label+'</h3><pre>'+esc(r.get('answer',str(r.get('failure'))))+'</pre><a href="runs/'+r['request_id']+'/response.json">전체 이벤트</a>')
        body.append('</details>')
    verification_file=OUT/'verification.json'
    if verification_file.exists():
        checked=json.loads(verification_file.read_text())
        body.append('<h2>검증 증빙</h2><div class="card">Python '+str(checked.get('python_tests_passed','미확인'))+
            '개 · 화면 처리 '+str(checked.get('ui_tests_passed','미확인'))+'개 테스트. LangSmith 그래프 '+
            str(checked.get('langsmith_graph_runs','미확인'))+'건 재조회. 원문·고정 지표·기존 검수 코드와 핵심 실행 소스의 보존을 확인했습니다.</div>')
    body.append('<p><a href="comparison.csv">집계 CSV</a> · <a href="per_question.csv">질문별 CSV</a> · <a href="measurements.csv">99건 실측</a> · <a href="manifest.json">실험 조건·소스 해시</a> · <a href="all_results.json">전체 응답</a> · <a href="verification.json">검증 기록</a> · <a href="green_tests.log">Python 테스트</a> · <a href="green_ui.log">화면 처리 테스트</a></p></html>')
    (OUT/'report.html').write_text(''.join(body))
    # Keep small aggregate evidence in Git; raw provider/SSE records remain local.
    (ROOT/'doc/three_way_comparison.csv').write_bytes((OUT/'comparison.csv').read_bytes())
    lines=['# 기존 과제 · 개선본 · 최적화본 실측 비교', '',
        '2026-10-21에 같은 질문을 버전마다 33건씩 실제 API로 실행한 고정 수업 샘플 비교입니다.',
        '기존 개선본은 f369c8f, 최적화본은 '+manifest['optimized_commit']+'입니다.', '',
        '| 항목 | 기존 과제 | 기존 개선본 | 최적화본 |', '|---|---:|---:|---:|']
    for title,key in metrics:
        vals=[summary[name][key] for name in NAMES]
        lines.append('| '+title+' | '+' | '.join(f'{v:.3f}' if isinstance(v,float) else f'{v:,}' for v in vals)+' |')
    lines+=['', '기존 개선본 대비 평균 응답시간 변화: '+f"{(new['mean_s']/old['mean_s']-1)*100:+.1f}%"+'.',
        '기존 개선본 대비 장애 질문 핵심 토큰 변화: '+f"{(new['core_tokens_incident']/old['core_tokens_incident']-1)*100:+.1f}%"+'.', '',
        '## 조건과 해석', '',
        '- 같은 모델 버전, 고정 샘플 지표·시각, 최초 모의 타임아웃 해제, 회차별 실행 순서 교차입니다.',
        '- 개선본과 최적화본은 같은 검색 캐시의 독립 사본을 재사용했습니다. 캐시 구축 비용은 포함하지 않습니다.',
        '- 10월 20일의 무작위 지표·다른 캐시 조건 기록과 섞지 않았습니다. 이번 원본에서 발생한 범위 밖 조회는 별도 실측값입니다.',
        '- 원본의 요청 범위 밖 조회는 의존 서비스 조사가 필요 없다는 증명이 아닙니다. Q08 auth-service 대체 정책을 유지했습니다.',
        '- 완료율·조회 대상 일치·자동 검수 통과는 최종 답변 정답률이 아닙니다. 독립 평가자의 의미 검수와 실제 청구액 확인은 미완료입니다.',
        '- 여러 최적화를 함께 적용했으므로 각 변경의 기여율을 분리한 실험은 아닙니다.',
        '- 원시 요청·응답·제공자 usage·소스 해시·전체 HTML 보고서는 로컬 evidence/optimization_20261021/에 있습니다.', '',
        '## 질문별 응답시간 중앙값', '', '| 질문 | 기존 과제 | 기존 개선본 | 최적화본 |','|---|---:|---:|---:|']
    for record in comparison:
        lines.append('| '+record['case_id']+' '+record['question']+' | '+' | '.join(
            f"{record[name+'_median_s']:.3f}초" for name in NAMES)+' |')
    (ROOT/'doc/three_way_comparison.md').write_text('\n'.join(lines)+'\n')
    print(json.dumps(payload,ensure_ascii=False,indent=2))


if __name__=='__main__': build()
