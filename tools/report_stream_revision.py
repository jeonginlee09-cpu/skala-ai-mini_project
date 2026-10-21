"""Use existing comparison metrics; no streaming-specific tests or timing metrics."""
import argparse
import csv
import hashlib
import html
import json
import statistics
from datetime import datetime
from pathlib import Path

from dotenv import dotenv_values, load_dotenv
from report_three_versions import summarize

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT/'evidence/stream_revision_20261021'
NAMES = {'original':'기존 과제', 'optimized':'직전 최적화본', 'revised':'스트리밍 수정본'}
METRICS = [('완료 요청 / 33','completed'), ('평균 응답시간(초)','mean_s'),
    ('응답시간 중앙값(초)','median_s'), ('p95 응답시간(초)','p95_s'),
    ('조회 대상 일치 / 24','target_exact'), ('범위 밖 조회 대상 수','outside_scope_targets'),
    ('누락 조회 대상 수','missing_targets'), ('성공 모니터링 호출','monitoring_calls'),
    ('검수 전 장애 초안 노출 / 24','draft_exposed_requests'), ('재작성 요청 / 24','rewrite_requests'),
    ('핵심 모델 호출(전체 33건)','core_model_calls_all'), ('핵심 모델 호출(장애 24건)','core_model_calls_incident'),
    ('입력 토큰(장애 24건)','core_input_tokens_incident'), ('출력 토큰(장애 24건)','core_output_tokens_incident'),
    ('핵심 총 토큰(장애 24건)','core_tokens_incident'), ('핵심 총 토큰(전체 33건)','core_tokens_all'),
    ('관련성 모델 신규 호출','relevance_calls'), ('임베딩 신규 호출','embedding_calls'),
    ('관련성 캐시 재사용','relevance_cache_hits'), ('이름 회상 성공 / 3','name_recall_passed')]


def write_csv(path, rows):
    with path.open('w',encoding='utf-8-sig',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=list(rows[0]),lineterminator='\n')
        writer.writeheader();writer.writerows(rows)


def build(traces=False):
    rows=json.loads((OUT/'all_results.json').read_text())
    manifest=json.loads((OUT/'manifest.json').read_text())
    groups={name:[r for r in rows if r['variant']==name] for name in NAMES}
    assert len(rows)==99 and all(len(group)==33 for group in groups.values())
    keys=[{(r['case_id'],r['round'],r['question']) for r in group} for group in groups.values()]
    assert keys[0]==keys[1]==keys[2]
    telemetry={name:[json.loads(line) for line in (OUT/'telemetry'/(name+'.jsonl')).read_text().splitlines()]
               for name in NAMES}
    summary={name:summarize(groups[name],telemetry[name]) for name in NAMES}
    fixture=json.loads((ROOT/'data/monitoring.json').read_text())
    fixture_errors=[];source_errors=[];usage_errors=[]
    for name in NAMES:
        for file,expected in manifest['sources'][name].items():
            if hashlib.sha256((OUT/'stages'/name/file).read_bytes()).hexdigest()!=expected:
                source_errors.append(name+':'+file)
        for record in telemetry[name]:
            if record['kind']=='provider' and (record['http_status']!=200 or not record['usage'].get('total_tokens')):
                usage_errors.append(record['request_id'])
            if record['kind']=='monitoring':
                value=record['result'];expected=fixture[value['system_name']]
                if (value['cpu_percent']!=expected['cpu_base'] or value['memory_percent']!=expected['memory_base']
                        or value['alerts']!=expected['alerts'] or datetime.fromisoformat(value['last_checked'])
                        !=datetime.fromisoformat('2026-10-21T09:00:00+09:00')):
                    fixture_errors.append(record['request_id'])
    assert not source_errors and not fixture_errors and not usage_errors
    assert len({tuple(sorted(data['provider_model_names'])) for data in summary.values()})==1
    assert (OUT/'stages/optimized/agent/validator.py').read_bytes()==(OUT/'stages/revised/agent/validator.py').read_bytes()
    books={name:{(r['case_id'],r['round']):set(r['actual_runbooks']) for r in group} for name,group in groups.items()}
    differences=[str(key) for key in books['optimized'] if books['optimized'][key]!=books['revised'][key]]
    per_question=[]
    for case in sorted({r['case_id'] for r in rows}):
        data={'case_id':case,'question':next(r['question'] for r in rows if r['case_id']==case)}
        data.update({name+'_median_s':statistics.median(r['total_s'] for r in group if r['case_id']==case)
                     for name,group in groups.items()})
        per_question.append(data)
    aggregates=[{'metric':title,**{name:summary[name][key] for name in NAMES}} for title,key in METRICS]
    write_csv(OUT/'comparison.csv',aggregates)
    write_csv(OUT/'per_question.csv',per_question)
    write_csv(OUT/'measurements.csv',[{k:r[k] for k in ['request_id','variant','case_id','round','status',
        'total_s','actual_targets','actual_runbooks','target_set_match','validation_attempts','draft_exposed']} for r in rows])
    payload={'captured_at':datetime.now().astimezone().isoformat(),'results':summary,
        'source_errors':source_errors,'fixture_errors':fixture_errors,'provider_usage_errors':usage_errors,
        'runbook_set_differences_optimized_revised':differences,'validator_source_unchanged':True,
        'human_semantic_review':'pending','actual_invoice':'not_measured',
        'python_existing_tests_passed':60,'ui_existing_tests_passed':4,'new_streaming_tests_added':False}
    secrets=[v for k,v in dotenv_values(ROOT/'.env').items() if v and len(v)>=8
             and any(word in k.upper() for word in ['API_KEY','SECRET','TOKEN','PASSWORD'])]
    if traces:
        from langsmith import Client
        load_dotenv(ROOT/'.env');client=Client();traced=[];leak=False
        ids={r['request_id'] for r in rows}
        for name in NAMES:
            for run in client.list_runs(project_name='capstone-stream3way-'+name+'-20261021',
                    filter='eq(name, "LangGraph")',limit=100):
                rid=(run.inputs or {}).get('request_id') or ((run.extra or {}).get('metadata') or {}).get('comparison_request_id')
                if rid not in ids:continue
                raw=json.dumps({'inputs':run.inputs,'outputs':run.outputs},ensure_ascii=False,default=str)
                leak=leak or any(secret in raw for secret in secrets)
                traced.append({'request_id':rid,'run_id':str(run.id),'end_time':str(run.end_time),'error':run.error})
        missing=sorted(ids-{r['request_id'] for r in traced if r['end_time']!='None' and not r['error']})
        payload.update(langsmith_graph_runs=len(traced),missing_langsmith_requests=missing,remote_secret_detected=leak)
        (OUT/'langsmith_verified.json').write_text(json.dumps(traced,ensure_ascii=False,indent=2))
        assert not missing and not leak
    (OUT/'summary.json').write_text(json.dumps(payload,ensure_ascii=False,indent=2))
    fmt=lambda value:f'{value:.3f}' if isinstance(value,float) else f'{value:,}'
    lines=['# 기존 과제 · 최적화본 · 스트리밍 수정본 실측 비교','',
        '2026-10-21 / 같은 질문 버전별 33건, 총 99건 / 고정 수업 샘플 / 실제 OpenAI·MCP·FastAPI 호출','',
        '최적화본: `'+manifest['refs']['optimized']+'` · 수정본: `'+manifest['refs']['revised']+'`','',
        '| 항목 | '+' | '.join(NAMES.values())+' |','|---|---:|---:|---:|']
    lines += ['| '+title+' | '+' | '.join(fmt(summary[name][key]) for name in NAMES)+' |' for title,key in METRICS]
    old,new=summary['optimized'],summary['revised']
    lines += ['',f"직전 최적화본 대비 평균 응답시간 변화: {(new['mean_s']/old['mean_s']-1)*100:+.1f}%.",
        f"직전 최적화본 대비 장애 질문 핵심 토큰 변화: {(new['core_tokens_incident']/old['core_tokens_incident']-1)*100:+.1f}%.",'',
        '## 조건과 해석','',
        '- 이전 측정값을 재사용하지 않고 세 버전을 모두 다시 실행했습니다. 같은 모델·고정 지표·질문·캐시와 회차별 교차 순서입니다.',
        '- 토큰은 제공자 usage입니다. 스트리밍 응답도 마지막 usage JSON에서 계수하며 관련성·임베딩 호출은 별도로 집계합니다.',
        '- 응답시간은 전체 완료까지입니다. 스트리밍 전용 검증·새 테스트·첫 토큰 시간 측정은 추가하지 않았습니다.',
        '- 스트리밍 변경이 전체 생성 시간이나 토큰 감소를 보장하지 않습니다. 모델 출력·네트워크 변동이 있으며 변경별 기여율은 분리하지 않았습니다.',
        '- Q08의 알 수 없는 서비스명→auth-service 대체 정책은 유지합니다. 완료율·조회 대상 일치는 답변 정답률이 아닙니다.',
        '- 독립 평가자의 의미 검수·실제 청구액·실서비스 조치 실행은 확인하지 않았습니다. 기존 검수 코드와 검색 정책을 보존했습니다.',
        '- 이번 원시 자료: `evidence/stream_revision_20261021/`. 이전 `evidence/optimization_20261021/`는 보존합니다.','',
        '## 질문별 응답시간 중앙값','', '| 질문 | '+' | '.join(NAMES.values())+' |','|---|---:|---:|---:|']
    lines += ['| '+r['case_id']+' '+r['question']+' | '+' | '.join(f"{r[n+'_median_s']:.3f}초" for n in NAMES)+' |' for r in per_question]
    (ROOT/'doc/stream_revision_comparison.md').write_text('\n'.join(lines)+'\n')
    (ROOT/'doc/stream_revision_comparison.csv').write_bytes((OUT/'comparison.csv').read_bytes())
    esc=lambda value:html.escape(str(value))
    body=['<!doctype html><html lang="ko"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">',
        '<title>기존 과제·최적화본·수정본 비교</title><style>body{max-width:1120px;margin:32px auto;padding:24px;font:16px/1.8 Pretendard,-apple-system,sans-serif;color:#24334a;background:#f5f7fb}table{width:100%;border-collapse:collapse;background:white}th,td{padding:10px;border:1px solid #dde4ef;text-align:left}th{background:#eaf0fa}pre{white-space:pre-wrap;word-break:break-word}details{padding:18px;background:white;margin:12px 0;border-radius:12px}</style>',
        '<h1>기존 과제 · 직전 최적화본 · 스트리밍 수정본</h1><p>2026-10-21 / 버전별 33건 / 실제 API / 같은 고정 수업 샘플</p>',
        '<p>새 스트리밍 전용 검증 항목은 추가하지 않았습니다. 아래 시간은 전체 완료 시간이며, 처리 완료는 답변 정답이나 실제 복구 성공을 뜻하지 않습니다.</p>',
        '<table><tr><th>항목</th>'+''.join('<th>'+v+'</th>' for v in NAMES.values())+'</tr>']
    body += ['<tr><td>'+title+'</td>'+''.join('<td>'+fmt(summary[n][key])+'</td>' for n in NAMES)+'</tr>' for title,key in METRICS]
    body += ['</table><h2>측정 조건·한계</h2><ul>'+''.join('<li>'+esc(v[2:])+'</li>' for v in lines if v.startswith('- '))+'</ul>',
        '<h2>동일 질문의 실제 답변</h2>']
    for item in per_question:
        body.append('<details><summary>'+esc(item['case_id']+' '+item['question'])+'</summary>')
        for name,label in NAMES.items():
            row=next(r for r in groups[name] if r['case_id']==item['case_id'] and r['round']==1)
            body.append('<h3>'+label+'</h3><pre>'+esc(row.get('answer',''))+'</pre><a href="runs/'+row['request_id']+'/response.json">원시 응답</a>')
        body.append('</details>')
    body.append('<p>'+ ' · '.join('<a href="'+file+'">'+label+'</a>' for file,label in [
        ('comparison.csv','집계 CSV'),('per_question.csv','질문별 CSV'),('measurements.csv','99건 실측'),
        ('all_results.json','전체 결과'),('manifest.json','소스·측정 조건'),('summary.json','집계 JSON'),
        ('green_tests.log','기존 Python 검사'),('green_ui.log','기존 화면 검사')])+'</p></html>')
    (OUT/'report.html').write_text(''.join(body))
    leaked=[str(p.relative_to(OUT)) for p in OUT.rglob('*') if p.is_file() and p.suffix in ['.json','.jsonl','.html','.csv','.log','.txt','.sse']
            and any(secret in p.read_text(errors='replace') for secret in secrets)]
    assert not leaked, 'Credential detected in result files'
    print(json.dumps(payload,ensure_ascii=False,indent=2))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--traces',action='store_true')
    build(parser.parse_args().traces)
