"""Render saved measurements; never invent semantic grades or billing amounts."""
import csv
import html
import json
import statistics
from collections import Counter
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'evidence/rag_validation'
esc=lambda value:html.escape(str(value))
runs=json.loads((OUT/'all_results.json').read_text())
main=[r for r in runs if not r['request_id'].endswith('-context')]
incidents=[r for r in main if r['expected_targets']]
before=json.loads((ROOT/'evidence/before_snapshot/all_results.json').read_text())
comparisons=[]
for case in ['Q01','Q02','Q03','Q04','Q05','Q06','Q07','Q08','Q09','Q10B']:
    old=[r for r in before if r['case_id']==case and 'context' not in r['request_id']]
    new=[r for r in main if r['case_id']==case]
    comparisons.append({'case_id':case,'question':new[0]['question'],
        'before_n':len(old),'after_n':len(new),
        'before_target_match':sum(r['target_set_match'] for r in old),
        'after_target_match':sum(r['target_set_match'] for r in new),
        'before_median_s':statistics.median(r['total_s'] for r in old),
        'after_median_s':statistics.median(r['total_s'] for r in new),
        'after_runbooks':[r['actual_runbooks'] for r in new],
        'rewritten_requests':sum(r['validation_attempts']>1 for r in new)})
with (OUT/'before_after.csv').open('w',encoding='utf-8-sig',newline='') as file:
    writer=csv.DictWriter(file,fieldnames=list(comparisons[0]));writer.writeheader();writer.writerows(comparisons)
with (OUT/'measurements.csv').open('w',encoding='utf-8-sig',newline='') as file:
    fields=['request_id','case_id','status','total_s','actual_targets','actual_runbooks','target_set_match',
        'validation_attempts','draft_token_exposed','writer_route_plan_analysis_tokens','embedding_usage','relevance_usage']
    writer=csv.DictWriter(file,fieldnames=fields);writer.writeheader();writer.writerows({k:r[k] for k in fields} for r in runs)
rejected=Counter(e['code'] for r in runs for item in r['events'] if item['event']['type']=='Validation'
                 for e in item['event'].get('errors',[]))
trace_file=OUT/'langsmith_verified.json'
traces=json.loads(trace_file.read_text()) if trace_file.exists() else {'graph_runs':0,'completed':0}
summary={'main_requests':len(main),'main_complete':sum(r['status']=='ok' for r in main),
    'total_requests':len(runs),'total_complete':sum(r['status']=='ok' for r in runs),
    'incident_target_match':sum(r['target_set_match'] for r in incidents),'incident_requests':len(incidents),
    'unvalidated_draft_tokens_exposed':sum(r['draft_token_exposed'] for r in incidents),
    'request_ids_match':all(r['event_ids_match'] for r in runs),
    'requests_rewritten':sum(r['validation_attempts']>1 for r in runs),'rejection_codes':dict(rejected),
    'langsmith_graph_runs':traces['graph_runs'],'langsmith_completed':traces['completed'],
    'manual_semantic_review':'pending','actual_actions_executed':False}
(OUT/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2))
style="body{font:16px/1.75 -apple-system,'Apple SD Gothic Neo',sans-serif;max-width:1120px;margin:40px auto;padding:24px;background:#f7f9fc;color:#23334c}h1{font-size:29px}h2{font-size:21px;margin-top:32px}table{border-collapse:collapse;width:100%;font-size:14px}td,th{padding:10px;border-bottom:1px solid #dbe3ed;text-align:left;vertical-align:top}th{background:#eaf0f9}details,.box{background:white;border:1px solid #dbe3ed;border-radius:12px;padding:18px;margin:14px 0}pre{white-space:pre-wrap;word-break:break-word;font:13px/1.7 monospace}.note{padding:18px;background:#fff3db;border-radius:12px}a{color:#285ba7}img{max-width:100%}"
parts=[f"<!doctype html><html lang='ko'><meta charset='utf-8'><meta name='viewport' content='width=device-width, initial-scale=1'><title>런북 RAG·근거 검수 검증</title><style>{style}</style>",
    '<h1>런북 RAG · 근거 및 조건 검수</h1><p>기존 6개 수업 런북 → Chroma 후보 검색 → 관련성·인용 검사 → MCP 원문 확인 → 구조화 초안 → 조건 검수 → 최종 표시</p>',
    '<p><a href="before_after.csv">전후 비교 CSV</a> · <a href="measurements.csv">요청별 실측</a> · <a href="all_results.json">전체 응답·이벤트</a> · <a href="langsmith_verified.json">LangSmith 조회</a> · <a href="verification.json">무결성 검증</a></p>',
    f"<div class='box'><strong>실제 SSE 요청 {summary['total_complete']}/{len(runs)}건 완료</strong><br>운영 질문의 조회 대상 일치 {summary['incident_target_match']}/{len(incidents)} · 검수 전 초안 토큰 노출 {summary['unvalidated_draft_tokens_exposed']}건 · 반려 후 재작성 {summary['requests_rewritten']}건</div>",
    '<p class="note">대상 일치와 자동 검수 통과는 최종 답변 정답률이 아닙니다. 모니터링은 무작위 변동이 있는 실습 샘플입니다. Q08의 auth-service 폴백은 기존 정책을 유지했습니다. 독립적인 사람 의미 검수는 미완료이며 실제 조치는 실행하지 않았습니다.</p>',
    '<h2>실제 질문 전후 비교</h2><p>기존 질문을 3회 반복한 기본 33건과 장애 대화 뒤 이름 등록·회상 2건을 구분합니다. 시간은 완성 응답까지의 전체 처리시간이며 모델 TTFT가 아닙니다. 검색·추가 검수·캐시 상태가 다르고 표본이 작으므로 인과적 성능 향상으로 단정하지 않습니다.</p>',
    '<table><tr><th>질문</th><th>대상 일치 전→후</th><th>시간 중앙값 전→후</th><th>재작성 요청</th></tr>']
for r in comparisons:
    parts.append(f"<tr><td>{r['case_id']}</td><td>{r['before_target_match']}/{r['before_n']} → {r['after_target_match']}/{r['after_n']}</td><td>{r['before_median_s']:.3f}초 → {r['after_median_s']:.3f}초</td><td>{r['rewritten_requests']}</td></tr>")
parts.append('</table><p>추가 모델 판정과 구조화 출력은 시간·사용량을 늘립니다. 임베딩·관련성 판정의 실제 요청/캐시 사용량은 measurements.csv에 분리했습니다. 기존 cost_usd는 가상 단가 값이며 청구액 또는 전체 RAG 비용이 아닙니다.</p>')
parts.append('<h2>검색 단독 실험과 한계</h2><p>거리 단독 및 작은 관련성 모델의 실패도 보존했습니다. 개발 중 여러 방식을 비교한 질문이므로 아래 결과를 독립 최종 성능으로 보고하지 않습니다. 기대 ID는 수업 원문을 대조해 작성한 개발 기준이며 별도 사람 검수가 필요합니다.</p><table><tr><th>방식·세트</th><th>기대 ID 집합 일치</th><th>무관 ID 수</th><th>기록</th></tr>')
for label,path in [('초기 거리 단독·14문항','vector_v1/retrieval_evaluation.json'),
    ('초기 관련성 판정·14문항','relevance_v1/retrieval_rerank_regression.json'),
    ('현재 관련성 판정·재검사 14문항','retrieval_rerank_regression.json'),
    ('현재 관련성 판정·별도 14문항','retrieval_heldout.json')]:
    data=json.loads((OUT/path).read_text());s=data['summary']
    parts.append(f"<tr><td>{esc(label)}</td><td>{s['rag_exact']}/{s['cases']}</td><td>{s['rag_irrelevant_hits']}</td><td><a href='{path}'>원본</a></td></tr>")
parts.append('</table><p>현재 판정은 벡터 상위 3개 후보에 대한 gpt-4o 관련성 판정과 실제 인용 문구 대조입니다. 점수를 정답 확률로 사용하지 않습니다. 일부 질문·서비스에 필요한 매뉴얼이 누락될 수 있어 최종 답변에 자료 부족 대상을 표시합니다. 최대 2개가 모든 복합 장애를 설명한다는 보장은 없습니다.</p>')
parts.append('<h2>통제된 오류·경계값 검사</h2><div class="box">Python 46개와 Node 4개 검사입니다. 실제 모델 성능과 분리합니다. 다른 대상/수치, 없는 ID, 완료 조치 위장, 반복 실패, 자료 오류, 세션 회귀를 포함합니다.<p><a href="all_python_final.log">Python 결과</a> · <a href="all_ui_final.log">화면 로직 결과</a> · <a href="red_pipeline.log">연결 전 실패</a> · <a href="mutation_condition_guard.log">조건 반전 오류 검출</a></p></div><table><tr><th>고정 입력</th><th>기대 동작·검사 결과</th></tr><tr><td>메모리 88.9%</td><td>수업의 90% 이상 조건 미충족. 직접 권고는 반려, 조건부 표현 허용.</td></tr><tr><td>메모리 90.0% / 90.1%</td><td>수업 조건 충족으로 판정. 실제 모든 서비스의 안전 기준이라는 뜻은 아님.</td></tr><tr><td>CPU 95.1% 한 번 조회</td><td>지속 여부 미확인. 확장 직접 권고는 반려.</td></tr><tr><td>계속 잘못된 초안</td><td>최대 3번 작성 뒤 validation_failed. POST/SSE에서 성공 FinalAnswer를 내보내거나 성공 이력으로 저장하지 않음.</td></tr></table>')
parts.append('<h2>실제 반려 사유</h2><pre>'+esc(json.dumps(dict(rejected),ensure_ascii=False,indent=2))+'</pre><p>이 숫자는 실제 실행의 자동 반려 코드 발생 수입니다. 사람이 판정한 오류 건수나 잘못된 검수 통과 건수는 미측정입니다.</p>')
parts.append('<h2>요청별 답변·원본 SSE</h2>')
for r in runs:
    path='runs/'+r['request_id']
    parts.append(f"<details><summary>{esc(r['request_id'])} · {esc(r['question'])}</summary><p>{r['total_s']:.3f}초 · 조회 {esc(r['actual_targets'])} · 런북 {esc(r['actual_runbooks'])} · 작성 {r['validation_attempts']}회</p><pre>{esc(r.get('answer',r.get('failure_reply','응답 미확보')))}</pre><a href='{path}/response.json'>응답·이벤트 JSON</a> · <a href='{path}/stream.sse'>원본 SSE</a></details>")
parts.append(f"<h2>원격 기록·화면·남은 검수</h2><p>LangSmith에서 이 실험의 그래프 {traces['graph_runs']}건 중 완료 {traces['completed']}건을 재조회했습니다. 반복 측정 외 smoke POST와 브라우저 시연도 별도 구분합니다. 실제 브라우저에서 검색·MCP 원문 확인·검수 통과·최종 답변 표시를 확인했습니다. 새 화면의 저장 스크린샷은 미확보입니다. 실행 화면은 <a href='http://127.0.0.1:18620'>localhost:18620</a>입니다.</p>")
parts.append('<ul><li>추가 확보: 독립 평가자의 원문·답변 의미 검수와 허용 가능한 런북 집합 판정.</li><li>구현 범위: 등록된 사실·조건·가설/권고 참조 검수. 모든 자연어 의미를 검증하는 범용 시스템이 아닙니다.</li><li>시계열·배포 이력·실제 조치 도구가 없어 지속 여부·원인·복구 완료를 확정하지 않습니다.</li><li>사람 승인(HITL)과 다중 worker 운영은 다음 범위입니다.</li></ul></html>')
(OUT/'report.html').write_text(''.join(parts))
print(json.dumps(summary,ensure_ascii=False,indent=2))
