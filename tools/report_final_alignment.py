"""Summarize the frozen final run; warmup and feature checks stay separate."""
import argparse
import hashlib
import html
import json
import statistics
from collections import Counter
from datetime import datetime
from pathlib import Path

from dotenv import dotenv_values, load_dotenv
from report_reviewer_comparison import is_reviewer, write_csv
from report_three_versions import provider_group, summarize

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'evidence/final_alignment_verified_20261021'
NAMES = {'original': '기존 과제', 'latest': '최종 수정본'}
METRICS = [
    ('완성 답변 / 33건', 'completed'), ('평균 처리시간(초)', 'mean_s'),
    ('중앙값(초)', 'median_s'), ('p95(초)', 'p95_s'),
    ('조회 대상 집합 일치 / 장애 24건 (기존 폴백 정책 포함)', 'target_exact'),
    ('누락 대상 수', 'missing_targets'), ('요청 범위 밖 대상 수', 'outside_scope_targets'),
    ('모니터링 호출 수', 'monitoring_calls'), ('검수 전 초안 노출 / 장애 24건', 'draft_exposed_requests'),
    ('재작성 발생 요청 / 장애 24건', 'rewrite_requests'),
    ('모델 호출 수(Reviewer 포함)', 'core_model_calls_all'),
    ('모델 입력+출력 토큰(Reviewer 포함)', 'core_tokens_all'),
    ('장애 24건 입력 토큰', 'core_input_tokens_incident'),
    ('장애 24건 출력 토큰', 'core_output_tokens_incident'),
    ('Reviewer 호출 수', 'reviewer_calls'), ('Reviewer 토큰', 'reviewer_tokens'),
    ('Reviewer 승인 요청', 'review_approved_requests'), ('Reviewer 생략 요청', 'review_skipped_requests'),
    ('Reviewer 반려·오류 보류 요청', 'review_failed_requests'),
    ('모니터링 재시도 수', 'monitoring_retries'), ('추가 검색 시도 수', 'extra_search_attempts'),
    ('이름 회상 성공 / 3건', 'name_recall_passed'),
    ('관련성 모델 신규 호출', 'relevance_calls'), ('임베딩 신규 호출', 'embedding_calls'),
]


def events(row):
    return [item['event'] for item in row['events']]


def feature_check(row, case):
    observed = events(row)
    checks = {'completed': row['status'] == 'ok',
              'targets': set(row['actual_targets']) == set(case['expected_targets'])}
    if 'expected_context' in case:
        checks['context'] = any(e['type'] == 'ContextResolved' and e['status'] == case['expected_context']
                                for e in observed)
    if case.get('expected_context') == 'ambiguous':
        checks['clarifies_without_monitoring'] = not row['actual_targets'] and any(
            e['type'] == 'ClarificationRequired' for e in observed)
    if 'expected_runbooks' in case:
        checks['runbooks'] = set(row['actual_runbooks']) == set(case['expected_runbooks'])
    if 'expected_name' in case:
        checks['name'] = case['expected_name'] in (row.get('answer') or '')
    return {'case_id': case['id'], 'question': case['query'], 'passed': all(checks.values()),
            'checks': checks, 'actual_targets': row['actual_targets'],
            'actual_runbooks': row['actual_runbooks'], 'seconds': row['total_s'],
            'answer': row.get('answer'), 'request_id': row['request_id']}


def main(check_traces=False):
    rows = json.loads((OUT / 'all_results.json').read_text())
    warm = json.loads((OUT / 'warmup_results.json').read_text())
    features = json.loads((OUT / 'feature_results.json').read_text())
    manifest = json.loads((OUT / 'manifest.json').read_text())
    groups = {name: [r for r in rows if r['variant'] == name] for name in NAMES}
    assert len(rows) == 66 and all(len(group) == 33 for group in groups.values())
    keys = [{(r['case_id'], r['round'], r['question']) for r in group} for group in groups.values()]
    assert keys[0] == keys[1], 'Before and After must use exactly the same inputs'
    assert len(warm) == 8 and len(features) == 10
    all_rows = rows + warm + features
    assert len({r['request_id'] for r in all_rows}) == len(all_rows)
    telemetry = {name: [json.loads(line) for line in (OUT / 'telemetry' / (name + '.jsonl')).read_text().splitlines()]
                 for name in NAMES}
    summary = {name: summarize(groups[name], telemetry[name]) for name in NAMES}
    for name, group in groups.items():
        ids = {r['request_id'] for r in group}
        reviews = [t for t in telemetry[name] if t['kind'] == 'provider' and t['request_id'] in ids and is_reviewer(t)]
        all_events = [e for r in group for e in events(r)]
        count = lambda kind: sum(any(e['type'] == kind for e in events(r)) for r in group)
        summary[name].update(
            reviewer_calls=len(reviews), reviewer_tokens=sum(t['usage']['total_tokens'] for t in reviews),
            review_approved_requests=sum(any(e['type'] == 'ReviewerCompleted' and e['approved']
                for e in events(r)) for r in group), review_skipped_requests=count('ReviewerSkipped'),
            review_failed_requests=count('ReviewerFailed'),
            review_rejected_attempts=sum(e['type'] == 'ReviewerCompleted' and not e['approved'] for e in all_events),
            reviewer_issue_codes=dict(Counter(c for e in all_events if e['type'] == 'ReviewerCompleted'
                                              for c in e.get('issue_codes', []))),
            monitoring_retries=sum(e.get('retries', 0) for e in all_events
                                   if e['type'] == 'TaskCompleted' and e.get('source') == 'monitoring'),
            extra_search_attempts=sum(max(0, sum(e['type'] == 'RunbookSearch' for e in events(r)) - 1) for r in group),
        )
    cases = {c['id']: c for c in json.loads((ROOT / 'data/final_feature_cases.json').read_text())}
    checked_features = [feature_check(row, cases[row['case_id']]) for row in features]
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
    usage_by_phase = {}
    for phase, selected in [('comparison', rows), ('warmup', warm), ('features', features)]:
        ids = {r['request_id'] for r in selected}
        calls = [t for ts in telemetry.values() for t in ts if t['kind'] == 'provider' and t['request_id'] in ids]
        usage_by_phase[phase] = {kind: {'calls': sum(provider_group(t) == kind for t in calls),
            'tokens': sum(t['usage']['total_tokens'] for t in calls if provider_group(t) == kind)}
            for kind in ('core', 'relevance', 'embedding')}
    payload = {'latest_commit': manifest['latest_commit'], 'requests': len(rows), 'results': summary,
        'verification_errors': errors, 'features_passed': sum(r['passed'] for r in checked_features),
        'features_total': len(checked_features), 'usage_by_phase': usage_by_phase,
        'human_semantic_accuracy': 'not_measured', 'actual_invoice': 'not_measured',
        'new_streaming_tests_added': False,
        'failed_requests': [r['request_id'] for r in rows if r['status'] != 'ok']}
    secrets = [v for k, v in dotenv_values(ROOT / '.env').items() if v and len(v) >= 8
               and any(word in k.upper() for word in ('API_KEY', 'SECRET', 'TOKEN', 'PASSWORD'))]
    if check_traces:
        from langsmith import Client
        load_dotenv(ROOT / '.env'); client = Client(); traced = []; leaked = False
        ids = {r['request_id'] for r in all_rows}
        for name in NAMES:
            for run in client.list_runs(project_name=manifest['trace_project_prefix'] + '-' + name + '-20261021',
                    filter='eq(name, "LangGraph")', limit=100):
                rid = (run.inputs or {}).get('request_id') or ((run.extra or {}).get('metadata') or {}).get('comparison_request_id')
                if rid not in ids:
                    continue
                raw = json.dumps({'inputs': run.inputs, 'outputs': run.outputs}, ensure_ascii=False, default=str)
                leaked |= any(secret in raw for secret in secrets)
                traced.append({'request_id': rid, 'run_id': str(run.id), 'end_time': str(run.end_time), 'error': run.error})
        missing = sorted(ids - {t['request_id'] for t in traced if t['end_time'] != 'None' and not t['error']})
        payload.update(langsmith_graph_runs=len(traced), missing_langsmith_requests=missing, remote_secret_detected=leaked)
        (OUT / 'langsmith_verified.json').write_text(json.dumps(traced, ensure_ascii=False, indent=2))
        assert not missing and not leaked, 'Trace coverage or credential check failed'
    aggregates = [{'metric': title, **{name: summary[name][key] for name in NAMES}} for title, key in METRICS]
    write_csv(OUT / 'comparison.csv', aggregates)
    per_question = []
    for case in sorted({r['case_id'] for r in rows}):
        record = {'case_id': case, 'question': next(r['question'] for r in rows if r['case_id'] == case)}
        for name, group in groups.items():
            selected = [r for r in group if r['case_id'] == case]
            record[name + '_median_s'] = statistics.median(r['total_s'] for r in selected)
            record[name + '_completed'] = sum(r['status'] == 'ok' for r in selected)
        per_question.append(record)
    write_csv(OUT / 'per_question.csv', per_question)
    write_csv(OUT / 'measurements.csv', [{k: r.get(k) for k in ('request_id', 'variant', 'case_id', 'round', 'status',
        'total_s', 'actual_targets', 'actual_runbooks', 'target_set_match', 'validation_attempts', 'draft_exposed', 'failure')}
        for r in rows])
    (OUT / 'summary.json').write_text(json.dumps(payload, ensure_ascii=False, indent=2))
    (OUT / 'feature_checks.json').write_text(json.dumps(checked_features, ensure_ascii=False, indent=2))
    (ROOT / 'doc/final_comparison.csv').write_bytes((OUT / 'comparison.csv').read_bytes())
    compact = [{k: r.get(k) for k in ('variant', 'case_id', 'question', 'answer', 'status', 'request_id',
        'actual_targets', 'actual_runbooks', 'total_s')} for r in rows if r['round'] == 1]
    compact.extend(checked_features)
    (ROOT / 'doc/final_execution_examples.json').write_text(json.dumps(compact, ensure_ascii=False, indent=2))
    (ROOT / 'doc/final_measurement_summary.json').write_text(json.dumps(payload, ensure_ascii=False, indent=2))
    old, new = summary['original'], summary['latest']
    fmt = lambda v: f'{v:.3f}' if isinstance(v, float) else f'{v:,}'
    notes = [
        '같은 11개 입력 × 3회 = 버전별 33건입니다. 장애 요청 24건, 일반 대화 9건이며 실행 순서를 교차했습니다.',
        '현재 코드를 고정한 사본과 원본을 모두 새로 실행했습니다. 과거 다른 실험의 숫자를 합치지 않았습니다.',
        '수업용 모니터링 샘플의 지표·알림·시각을 고정했습니다. 실서비스 장애나 실제 복구를 측정한 것이 아닙니다.',
        '시간은 HTTP 요청 시작부터 스트림 종료까지이며 실패·보류도 분모에 포함합니다. p95는 nearest-rank입니다.',
        '검색 캐시 준비 8건과 새 기능 확인 10건은 별도 기록입니다. 준비 비용을 아래 66건의 시간·토큰에 더하지 않습니다.',
        '모델 토큰은 OpenAI usage의 입력+출력입니다. Reviewer를 포함하며 임베딩·관련성 호출은 별도 집계합니다. 실제 청구액은 미측정입니다.',
        '대상 일치는 요청 범위와 조회 집합의 일치입니다. 의존 서비스가 실제로 불필요하다는 뜻이나 답변 정답률이 아닙니다.',
        'Q08 billing-service는 기존 auth-service 대체 정책을 유지합니다. 이 항목의 정책 일치를 billing-service 조사 성공으로 부르지 않습니다.',
        '일반 대화의 이전 이름 회상과 지시 대상 연결은 구분합니다. 새 기능 10건은 정의한 입력의 스모크 검사이며 일반적인 한국어 이해 성능이 아닙니다.',
        '독립 평가자의 답변 의미·권고 적절성 검수, 실제 청구액, 사람 승인, 실제 조치 실행, 실제 발표는 확인하지 않았습니다.',
        '여러 변경의 묶음을 비교했으므로 각 변경의 개별 기여율이나 통계적 유의성은 입증하지 않았습니다.',
        '새 스트리밍 전용 검증과 첫 토큰 시간 측정은 추가하지 않았습니다.',
    ]
    lines = ['# 기존 과제와 최종 수정본 — 새 실측', '',
        '측정일 2026-10-21 · 제품 소스 `' + manifest['latest_commit'] + '` · 실제 API 66건', '',
        '| 항목 | 기존 과제 | 최종 수정본 |', '|---|---:|---:|']
    lines += ['| ' + title + ' | ' + ' | '.join(fmt(summary[n][key]) for n in NAMES) + ' |' for title, key in METRICS]
    lines += ['', f"평균 시간 변화 {(new['mean_s'] / old['mean_s'] - 1) * 100:+.1f}%, 모델 총 토큰 변화 {(new['core_tokens_all'] / old['core_tokens_all'] - 1) * 100:+.1f}%입니다.",
        '', '## 측정 조건·해석', ''] + ['- ' + note for note in notes]
    expected_calls = sum(len(r['expected_targets']) for r in groups['latest'])
    lines += ['', '## 수치 해석', '',
        f"- 장애 24건의 기대 대상 합계는 {expected_calls}회입니다. 요청 수와 도구 호출 수는 다릅니다. 실제 호출은 기존 {old['monitoring_calls']}회·수정본 {new['monitoring_calls']}회입니다.",
        f"- 이번 실행에서 범위 밖 대상은 기존 {old['outside_scope_targets']}개·수정본 {new['outside_scope_targets']}개, 누락은 각각 {old['missing_targets']}개·{new['missing_targets']}개입니다. 누락 개선 효과를 이 데이터만으로 주장하지 않습니다.",
        f"- 수정본 Reviewer는 {new['reviewer_calls']}회·{new['reviewer_tokens']:,}토큰입니다. 승인 {new['review_approved_requests']}건, 생략 {new['review_skipped_requests']}건, 반려 시도 {new['review_rejected_attempts']}회입니다. 반려 후 환류는 실제 발생 여부와 통제 테스트를 구분합니다.",
        '- 짧은 ID 출력과 코드 조립은 생성량을 줄일 수 있지만, 근거·조건·Reviewer 입력이 추가됩니다. 전체 토큰이 원본보다 줄었다고 미리 결론 내리지 않습니다.',
        '', '## 별도 새 기능 확인', '', '| 사례 | 대상·상태·런북 검사 | 시간(초) |', '|---|---|---:|']
    lines += ['| ' + r['case_id'] + ' ' + r['question'] + ' | ' + ('통과' if r['passed'] else '미통과') + ' | ' + f"{r['seconds']:.3f}" + ' |' for r in checked_features]
    lines += ['', f"별도 입력 {sum(r['passed'] for r in checked_features)}/{len(checked_features)}건이 정의한 자동 검사를 통과했습니다. 답변 의미의 독립 채점은 아닙니다.",
        '', '## 질문별 중앙값', '', '| 질문 | 기존 초 | 수정본 초 |', '|---|---:|---:|']
    lines += ['| ' + r['case_id'] + ' ' + r['question'] + ' | ' + f"{r['original_median_s']:.3f}" + ' | ' + f"{r['latest_median_s']:.3f}" + ' |' for r in per_question]
    lines += ['', '## 증빙 위치', '',
        '- [집계 CSV](final_comparison.csv) · [작은 측정 요약](final_measurement_summary.json) · [실제 응답 예시](final_execution_examples.json)',
        '- [최종 코드 설명](final_code_guide.md) · [평가 항목 점검](final_review.md)',
        '- 원시 요청·응답·제공자 usage·고정 샘플·소스 해시·LangSmith 재조회는 `evidence/final_alignment_verified_20261021/`에 보존합니다. 키·대화 DB와 원시 로그는 Git에 넣지 않습니다.',
    ]
    (ROOT / 'doc/final_comparison.md').write_text('\n'.join(lines) + '\n')
    from markdown_it import MarkdownIt
    body = MarkdownIt('commonmark', {'html': False}).enable('table').render('\n'.join(lines))
    # The HTML lives beside the raw records, so rewrite only relative report links.
    for target in ('final_comparison.csv', 'final_measurement_summary.json', 'final_execution_examples.json',
                   'final_code_guide.md', 'final_review.md'):
        body = body.replace('href="' + target + '"', 'href="../../doc/' + target + '"')
    style = 'body{max-width:1100px;margin:32px auto;padding:24px;color:#24334a;background:#f5f7fb;font:16px/1.8 -apple-system,"Apple SD Gothic Neo",sans-serif}table{border-collapse:collapse;background:white;width:100%}th,td{border:1px solid #dce4ef;padding:10px;text-align:left}th{background:#eaf0fa}pre{white-space:pre-wrap;word-break:break-word}a{color:#2457a5}details{background:white;margin:16px 0;padding:18px}'
    page = '<!doctype html><html lang="ko"><head><meta charset="utf-8"><title>최종 실측 비교</title><style>' + style + '</style></head><body>' + body
    for r in compact:
        page += '<details><summary>' + html.escape(r.get('variant', '새 기능') + ' ' + r['case_id'] + ' ' + r['question']) + '</summary><pre>' + html.escape(r.get('answer') or '') + '</pre></details>'
    (OUT / 'report.html').write_text(page + '</body></html>')
    leaked = [str(p.relative_to(OUT)) for p in OUT.rglob('*') if p.is_file()
        and p.suffix in ('.json', '.jsonl', '.html', '.csv', '.log', '.txt', '.sse')
        and any(secret in p.read_text(errors='replace') for secret in secrets)]
    assert not leaked, 'Credential detected in evidence'
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('--traces', action='store_true')
    main(parser.parse_args().traces)
