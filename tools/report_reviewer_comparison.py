"""Audit and summarize all 66 observed requests, including Reviewer refusals."""
import argparse
import csv
import hashlib
import html
import json
import statistics
from collections import Counter
from datetime import datetime
from pathlib import Path

from dotenv import dotenv_values, load_dotenv
from report_three_versions import summarize

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'evidence/reviewer_comparison_20261021'
NAMES = {'original': '기존 과제', 'latest': '최신 Reviewer 버전'}
METRICS = [('완성 답변 / 33건', 'completed'), ('평균 처리시간(초)', 'mean_s'),
    ('중앙값(초)', 'median_s'), ('p95(초)', 'p95_s'),
    ('조회 대상 집합 일치 / 장애 24건', 'target_exact'), ('누락 대상 수', 'missing_targets'),
    ('요청 범위 밖 대상 수', 'outside_scope_targets'), ('모니터링 호출 수', 'monitoring_calls'),
    ('검수 전 장애 초안 노출 / 24건', 'draft_exposed_requests'),
    ('재작성 발생 요청 / 24건', 'rewrite_requests'),
    ('전체 모델 호출 수(Reviewer 포함)', 'core_model_calls_all'),
    ('전체 모델 토큰(Reviewer 포함)', 'core_tokens_all'),
    ('장애 24건 입력 토큰(Reviewer 포함)', 'core_input_tokens_incident'),
    ('장애 24건 출력 토큰(Reviewer 포함)', 'core_output_tokens_incident'),
    ('장애 24건 총 토큰(Reviewer 포함)', 'core_tokens_incident'),
    ('Reviewer 호출 수', 'reviewer_calls'), ('Reviewer 토큰', 'reviewer_tokens'),
    ('Reviewer 외 모델 토큰(전체 33건)', 'non_reviewer_tokens'),
    ('Reviewer 승인 요청 수', 'review_approved_requests'),
    ('Reviewer 생략 요청 수', 'review_skipped_requests'),
    ('Reviewer 거절·오류로 보류한 요청 수', 'review_failed_requests'),
    ('모니터링 재시도 수', 'monitoring_retries'), ('추가 검색 시도 수', 'extra_search_attempts'),
    ('이름 회상 성공 / 3건', 'name_recall_passed'),
    ('관련성 모델 신규 호출', 'relevance_calls'), ('임베딩 신규 호출', 'embedding_calls')]


def write_csv(path, rows):
    with path.open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]), lineterminator='\n')
        writer.writeheader()
        writer.writerows(rows)


def is_reviewer(record):
    messages = record.get('messages') or []
    return bool(messages and messages[0].get('content', '').startswith('당신은 독립적인 내용 검토 Reviewer Agent다.'))


def build(traces=False):
    rows = json.loads((OUT / 'all_results.json').read_text())
    manifest = json.loads((OUT / 'manifest.json').read_text())
    groups = {name: [r for r in rows if r['variant'] == name] for name in NAMES}
    assert len(rows) == 66 and all(len(group) == 33 for group in groups.values())
    keys = [{(r['case_id'], r['round'], r['question']) for r in group} for group in groups.values()]
    assert keys[0] == keys[1]
    telemetry = {name: [json.loads(line) for line in (OUT / 'telemetry' / (name + '.jsonl')).read_text().splitlines()]
                 for name in NAMES}
    summary = {name: summarize(groups[name], telemetry[name]) for name in NAMES}
    for name, group in groups.items():
        ids = {r['request_id'] for r in group}
        reviews = [t for t in telemetry[name] if t['kind'] == 'provider' and t['request_id'] in ids and is_reviewer(t)]
        events = [i['event'] for r in group for i in r['events']]
        count = lambda kind: sum(any(i['event']['type'] == kind for i in r['events']) for r in group)
        summary[name].update(reviewer_calls=len(reviews), reviewer_tokens=sum(t['usage']['total_tokens'] for t in reviews),
            non_reviewer_tokens=summary[name]['core_tokens_all']-sum(t['usage']['total_tokens'] for t in reviews),
            review_approved_requests=sum(any(i['event']['type']=='ReviewerCompleted' and i['event']['approved']
                for i in r['events']) for r in group), review_skipped_requests=count('ReviewerSkipped'),
            review_failed_requests=count('ReviewerFailed'),
            review_rejected_attempts=sum(e['type']=='ReviewerCompleted' and not e['approved'] for e in events),
            reviewer_issue_codes=dict(Counter(c for e in events if e['type']=='ReviewerCompleted' for c in e.get('issue_codes', []))),
            monitoring_retries=sum(e.get('retries', 0) for e in events if e['type']=='TaskCompleted' and e.get('source')=='monitoring'),
            extra_search_attempts=sum(max(0, sum(i['event']['type']=='RunbookSearch' for i in r['events'])-1) for r in group))
    fixture = json.loads((ROOT / 'data/monitoring.json').read_text())
    errors = {'source': [], 'fixture': [], 'usage': [], 'resource': []}
    for name in NAMES:
        stage = OUT / 'stages' / name
        for file, digest in manifest['sources'][name].items():
            if hashlib.sha256((stage / file).read_bytes()).hexdigest() != digest:
                errors['source'].append(name + ':' + file)
        book_hashes = {hashlib.sha256(p.read_bytes()).hexdigest() for p in (stage / 'data/runbooks').glob('*.md')}
        for t in telemetry[name]:
            if t['kind'] == 'provider' and (t['http_status'] != 200 or not t['usage'].get('total_tokens')):
                errors['usage'].append(t['request_id'])
            if t['kind'] == 'resource' and t['sha256'] not in book_hashes:
                errors['resource'].append(t['request_id'])
            if t['kind'] != 'monitoring':
                continue
            value = t['result']; expected = fixture[value['system_name']]
            if (value['cpu_percent'] != expected['cpu_base'] or value['memory_percent'] != expected['memory_base']
                    or value['alerts'] != expected['alerts'] or datetime.fromisoformat(value['last_checked'])
                    != datetime.fromisoformat('2026-10-21T09:00:00+09:00')):
                errors['fixture'].append(t['request_id'])
    assert not any(errors.values()), errors
    assert set(summary['original']['provider_model_names']) == set(summary['latest']['provider_model_names'])
    payload = {'latest_commit': manifest['latest_commit'], 'requests': len(rows), 'results': summary,
        'verification_errors': errors, 'human_semantic_accuracy': 'not_measured', 'actual_invoice': 'not_measured',
        'new_streaming_tests_added': False, 'failed_requests': [{k: r.get(k) for k in ('request_id', 'failure', 'collection_error')}
            for r in rows if r['status'] != 'ok']}
    secrets = [v for k,v in dotenv_values(ROOT / '.env').items() if v and len(v)>=8
               and any(word in k.upper() for word in ('API_KEY','SECRET','TOKEN','PASSWORD'))]
    if traces:
        from langsmith import Client
        load_dotenv(ROOT / '.env'); client = Client(); traced = []; leaked = False
        ids = {r['request_id'] for r in rows}
        for name in NAMES:
            for run in client.list_runs(project_name='capstone-reviewer-comparison-' + name + '-20261021',
                    filter='eq(name, "LangGraph")', limit=100):
                rid = (run.inputs or {}).get('request_id') or ((run.extra or {}).get('metadata') or {}).get('comparison_request_id')
                if rid not in ids:
                    continue
                raw = json.dumps({'inputs':run.inputs, 'outputs':run.outputs}, ensure_ascii=False, default=str)
                leaked |= any(secret in raw for secret in secrets)
                traced.append({'request_id':rid, 'run_id':str(run.id), 'end_time':str(run.end_time), 'error':run.error})
        missing = sorted(ids - {t['request_id'] for t in traced if t['end_time']!='None' and not t['error']})
        payload.update(langsmith_graph_runs=len(traced), missing_langsmith_requests=missing, remote_secret_detected=leaked)
        (OUT / 'langsmith_verified.json').write_text(json.dumps(traced, ensure_ascii=False, indent=2))
        assert not missing and not leaked
    per_question = []
    for case in sorted({r['case_id'] for r in rows}):
        data = {'case_id':case, 'question':next(r['question'] for r in rows if r['case_id']==case)}
        for name, group in groups.items():
            selected = [r for r in group if r['case_id']==case]
            data[name+'_median_s'] = statistics.median(r['total_s'] for r in selected)
            data[name+'_completed'] = sum(r['status']=='ok' for r in selected)
        per_question.append(data)
    aggregates = [{'metric':title, **{name:summary[name][key] for name in NAMES}} for title,key in METRICS]
    write_csv(OUT / 'comparison.csv', aggregates)
    write_csv(OUT / 'per_question.csv', per_question)
    write_csv(OUT / 'measurements.csv', [{k:r.get(k) for k in ('request_id','variant','case_id','round','status',
        'total_s','actual_targets','actual_runbooks','target_set_match','validation_attempts','draft_exposed','failure')} for r in rows])
    (OUT / 'summary.json').write_text(json.dumps(payload, ensure_ascii=False, indent=2))
    fmt = lambda v: f'{v:.3f}' if isinstance(v,float) else f'{v:,}'
    old,new = summary['original'],summary['latest']
    lines = ['# 기존 과제와 최신 Reviewer 버전 실측 비교','',
        '2026-10-21 / 버전별 33건, 총 66건 / 최신 제품 소스 `'+manifest['latest_commit']+'`','',
        '| 항목 | 기존 과제 | 최신 Reviewer 버전 |','|---|---:|---:|']
    lines += ['| '+title+' | '+' | '.join(fmt(summary[n][key]) for n in NAMES)+' |' for title,key in METRICS]
    lines += ['', f"평균 처리시간 변화: {(new['mean_s']/old['mean_s']-1)*100:+.1f}%. 전체 모델 토큰 변화: {(new['core_tokens_all']/old['core_tokens_all']-1)*100:+.1f}%.",
        '', '## 측정 조건과 해석','']
    notes = ['10개 시나리오 중 이름 등록/회상을 두 입력으로 분리한 11개 입력 × 3회입니다. 장애 24건과 일반 대화 9건입니다.',
        '이번에 원본과 최신 버전을 모두 새로 실행했습니다. 이전 최적화·스트리밍 실험 수치와 섞지 않았습니다.',
        '시간은 HTTP 요청 시작부터 스트림 종료까지입니다. 실패·보류도 분모와 시간 집계에 포함합니다. p95는 nearest-rank 방식입니다.',
        '수업용 샘플 지표·알림·시각을 고정하고 최초 모의 오류를 양쪽 모두 해제했습니다. 실제 운영 장애가 아닙니다.',
        'RAG는 준비된 캐시를 사용합니다. 캐시 구축·새 질문의 임베딩 및 관련성 판정 비용은 이 구간에 포함하지 않습니다.',
        '모델 토큰은 OpenAI usage의 입력+출력이며 Reviewer도 포함합니다. 실제 청구액은 미측정입니다.',
        '조회 일치는 기대 대상 집합과의 일치입니다. 요청 수와 도구 호출 수는 단위가 다르며 한 요청이 두 서비스를 조회할 수 있습니다.',
        'Q08 billing-service는 현재도 auth-service로 대체합니다. 이 정책과 일치한다는 뜻이며 billing-service 상태를 확인했다는 뜻이 아닙니다.',
        '자동 검수·Reviewer 승인·처리 완료는 답변 정답률이 아닙니다. 독립 평가자의 의미 정확도 채점은 미실시입니다.',
        '원본에는 없던 조건 검수와 Reviewer를 함께 추가했으므로 각 변경의 개별 성능 기여는 분리하지 않았습니다.',
        '모든 재시도를 합친 하나의 지표는 사용하지 않습니다. 모니터링 재시도·추가 검색·답변 재작성·Reviewer 재검토를 구분합니다.',
        '사람 승인(HITL)과 실제 재시작·롤백·증설 실행은 구현하지 않았습니다. AI Reviewer는 사람 승인이 아닙니다.']
    lines += ['- '+note for note in notes]
    explanations = [
        f"평균은 {old['mean_s']:.3f}초에서 {new['mean_s']:.3f}초로 {(1-new['mean_s']/old['mean_s'])*100:.1f}% 단축됐습니다. 짧은 ID 출력과 미사용 모델 분석 제거가 적용된 전체 버전의 결과이며, 개별 변경의 인과 효과를 분리한 수치는 아닙니다.",
        f"전체 토큰은 {old['core_tokens_all']:,}개에서 {new['core_tokens_all']:,}개로 늘었습니다. 최신 Reviewer의 {new['reviewer_tokens']:,}개가 포함되며, Reviewer 외에도 근거·조건·절차 입력을 사용하는 {new['non_reviewer_tokens']:,}개가 듭니다. 원본보다 토큰·실제 비용이 줄었다고 주장하지 않습니다.",
        '모니터링 호출 35→33의 분모는 요청 수가 아닙니다. 장애 24건 중 단일 서비스 15건과 두 서비스 9건이므로 기대 조회 대상은 15+18=33회입니다. 원본 Q04의 1·2회차가 요청에 없던 auth-service도 조회해 총 35회가 됐습니다. 의존 서비스 조사가 본질적으로 불필요하다는 판정은 아닙니다.',
        '이번 최신 실행의 계획은 이미 요청 범위와 일치해 PlanCoverageChecked의 추가·제거·중복 교정은 모두 0입니다. 모델 계획에 전체 요청 목록을 전달하고 코드로 범위를 강제하는 정책의 합산 결과이며, 두 건이 gate의 삭제 분기에서 직접 교정됐다고 말하지 않습니다.',
        f"최신 장애 24건 중 Reviewer 승인 {new['review_approved_requests']}건·생략 {new['review_skipped_requests']}건이며 일반 대화 9건은 이 단계를 지나지 않습니다. 실제 반려 {new['review_rejected_attempts']}회·재작성 {new['rewrite_requests']}건이므로 이번 66건으로 반려 후 개선 효과를 입증하지 않습니다. 해당 분기는 별도의 통제된 테스트 증빙으로 구분합니다.",
        '조회 대상 일치는 원본 22/24→최신 24/24이고 누락은 양쪽 0입니다. 이번 실행에서 차이를 만든 것은 원본의 범위 밖 추가 조회 두 건입니다. 과거 기록의 조회 누락 사례를 이번 측정에서 다시 발생한 것처럼 설명하지 않습니다.'
    ]
    lines += ['', '## 수치가 이렇게 달라진 이유',''] + ['- '+s for s in explanations]
    lines += ['', '## 질문별 중앙값과 완료 건수','', '| 질문 | 기존 초 / 완료 | 최신 초 / 완료 |','|---|---:|---:|']
    lines += ['| '+r['case_id']+' '+r['question']+' | '+' | '.join(f"{r[n+'_median_s']:.3f} / {r[n+'_completed']}/3" for n in NAMES)+' |' for r in per_question]
    lines += ['', '## 증빙','', '- 원시 요청·응답·usage·소스 해시: `evidence/reviewer_comparison_20261021/`',
        '- 설명 자료: [변경 전후 코드 해설](reviewer_code_walkthrough.md)',
        '- 실제 응답: [각 버전 첫 실행 22건](reviewer_comparison_examples.json)',
        '- 집계 CSV: [reviewer_comparison.csv](reviewer_comparison.csv)']
    (ROOT / 'doc/reviewer_comparison.md').write_text('\n'.join(lines)+'\n')
    (ROOT / 'doc/reviewer_comparison.csv').write_bytes((OUT / 'comparison.csv').read_bytes())
    esc = lambda v:html.escape(str(v))
    body = ['<!doctype html><html lang="ko"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">',
        '<title>기존 과제와 최신 Reviewer 비교</title><style>body{max-width:1100px;margin:32px auto;padding:24px;background:#f5f7fb;color:#24334a;font:16px/1.8 Pretendard,-apple-system,sans-serif}table{width:100%;border-collapse:collapse;background:white}td,th{padding:10px;border:1px solid #dbe2ed;text-align:left}th{background:#eaf0fa}pre{white-space:pre-wrap;word-break:break-word;font:14px/1.7 monospace}details{background:white;margin:14px 0;padding:18px;border-radius:10px}a{color:#2457a5}</style>',
        '<h1>기존 과제와 최신 Reviewer 버전</h1><p>2026-10-21 · 실제 API 66건 · 준비된 검색 캐시 · 고정 수업 샘플</p>',
        '<p><a href="../../doc/reviewer_code_walkthrough.html">변경 코드와 발표 설명 보기</a></p>',
        '<table><tr><th>지표</th><th>기존 과제</th><th>최신 버전</th></tr>']
    body += ['<tr><td>'+esc(title)+'</td>'+''.join('<td>'+fmt(summary[n][key])+'</td>' for n in NAMES)+'</tr>' for title,key in METRICS]
    body += ['</table><h2>읽을 때 주의할 점</h2><ul>'+''.join('<li>'+esc(n)+'</li>' for n in notes)+'</ul>',
        '<h2>수치가 달라진 이유</h2><ul>'+''.join('<li>'+esc(n)+'</li>' for n in explanations)+'</ul>',
        '<h2>동일 질문의 실제 답변 — 각 첫 번째 실행</h2>']
    for case in per_question:
        body.append('<details><summary>'+esc(case['case_id']+' '+case['question'])+'</summary>')
        for name,label in NAMES.items():
            r=next(r for r in groups[name] if r['case_id']==case['case_id'] and r['round']==1)
            body.append('<h3>'+label+'</h3><pre>'+esc(r.get('answer') or json.dumps(r.get('failure'),ensure_ascii=False))+'</pre><a href="runs/'+r['request_id']+'/response.json">전체 이벤트 원문</a>')
        body.append('</details>')
    body += ['<p>'+ ' · '.join('<a href="'+f+'">'+title+'</a>' for f,title in [('comparison.csv','집계 CSV'),
        ('measurements.csv','66건 측정표'),('per_question.csv','질문별 수치'),('all_results.json','전체 응답'),
        ('manifest.json','조건·소스 해시'),('summary.json','검증 및 집계'),('langsmith_verified.json','LangSmith 재조회')])+'</p></html>']
    (OUT / 'report.html').write_text(''.join(body))
    leaked = [str(p.relative_to(OUT)) for p in OUT.rglob('*') if p.is_file() and p.suffix in ('.json','.jsonl','.html','.csv','.log','.txt','.sse')
              and any(secret in p.read_text(errors='replace') for secret in secrets)]
    assert not leaked, 'Credential detected in evidence'
    print(json.dumps(payload,ensure_ascii=False,indent=2))


if __name__ == '__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--traces',action='store_true')
    build(parser.parse_args().traces)
