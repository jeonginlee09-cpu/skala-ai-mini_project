"""Deterministic request scope and successful-evidence checks."""
import math
import re
from numbers import Real


def requested_targets(query: str, known_systems: set[str]) -> list[str]:
    matches = []
    for name in known_systems:
        pattern = r'(?<![A-Za-z0-9_-])' + re.escape(name) + r'(?![A-Za-z0-9_-])'
        match = re.search(pattern, query, re.IGNORECASE)
        if match:
            matches.append((match.start(), name))
    # Preserve the existing Q07/Q08 auth fallback policy, explicitly.
    return [name for _, name in sorted(matches)] or ['auth-service']


def enforce_scope(tasks: list[dict], expected: list[str]) -> tuple[list[dict], dict]:
    monitoring = [t for t in tasks if t.get('agent')=='research' and t.get('kind')=='monitoring']
    planned = [t.get('target') for t in monitoring]
    report = {'requested_targets':expected,
              'added_targets':[s for s in expected if s not in planned],
              'removed_targets':sorted({s for s in planned if s and s not in expected}),
              'duplicate_count':len(planned)-len(set(planned))}
    # The code determines the explicit scope; the LLM still plans analysis tasks.
    fixed = [{'id':'monitor-'+str(i),'agent':'research','kind':'monitoring',
              'target':name,'status':'pending'} for i,name in enumerate(expected)]
    others = [dict(t) for t in tasks if t not in monitoring]
    return fixed + others, report


def monitoring_error(data, expected: str) -> str | None:
    if not isinstance(data, dict) or data.get('system_name') != expected:
        return 'response_target_mismatch'
    if data.get('status') not in {'healthy','degraded','down'}:
        return 'status_unavailable'
    for key in ('cpu_percent','memory_percent'):
        value = data.get(key)
        if isinstance(value,bool) or not isinstance(value,Real) or not math.isfinite(value):
            return 'metric_unavailable'
    if not isinstance(data.get('alerts'),list) or not all(isinstance(x,str) for x in data['alerts']):
        return 'alerts_unavailable'
    return None


def coverage(collected: list[dict], expected: list[str]) -> tuple[list[str], list[str]]:
    verified = set()
    for row in collected:
        if row.get('source') != 'monitoring':continue
        data = row.get('data')
        name = data.get('system_name') if isinstance(data,dict) else None
        if name in expected and monitoring_error(data,name) is None:verified.add(name)
    return [s for s in expected if s in verified], [s for s in expected if s not in verified]


def incomplete_reply(collected: list[dict], verified: list[str], unverified: list[str]) -> str:
    facts=[]
    for name in verified:
        data=next(c['data'] for c in reversed(collected)
                  if c.get('source')=='monitoring' and c['data']['system_name']==name)
        facts.append(f"{name}: 상태={data['status']}, CPU {data['cpu_percent']}%, 메모리 {data['memory_percent']}%")
    return ('1) 확인한 조회 결과: '+('; '.join(facts) or '없음')+
            '\n2) 미확인: '+', '.join(unverified)+' — 제한된 재조회 후에도 유효한 지표를 얻지 못했습니다.'+
            '\n3) 미확인 서비스의 정상 여부와 공통 원인은 판단을 보류합니다. 연결·조회 대상·모니터링 자료를 확인한 뒤 다시 요청해 주세요.')
