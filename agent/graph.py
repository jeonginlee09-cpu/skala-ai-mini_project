"""v2 Supervisor — 실제 langgraph.StateGraph (Wave 4·5·6·7·8·10b·15 통합).

route → (chat 직행 | incident 파이프라인)
incident: plan(MCP Prompt 절차 반영) → gate(권위 게이트) → research(가드+병렬+재시도)
          → replan_check(부족 시 gap task 후 재조사) → analysis(needs_analysis=True일 때만
          실제 실행) → write → validate(코드 검사) → review(복합 질문 내용 검토) → finalize
검수/검토 반려는 상한 내 write로 환류하며, 내용 검토 실패는 성공 답변으로 공개하지 않는다.

모든 노드는 SupervisorDeps를 functools.partial로 주입받는 순수 함수다(Wave 13).
각 노드는 @traceable로 LangSmith에 계층 트리로 기록된다.
"""
import asyncio
import json
import time
from functools import partial
from pathlib import Path
from typing import Any, TypedDict

from langgraph.config import get_stream_writer
from langgraph.graph import END, StateGraph
from langsmith import traceable

from .deps import SupervisorDeps
from .retry import with_retry
from .target_scope import requested_targets, enforce_scope, monitoring_error, coverage, incomplete_reply
from .evidence import build_evidence
from .grounded_report import render_report
from .runbook_retriever import digest
from .validator import prepare_actions,validate_report
from .semantic_review import review_policy, parse_review, rewrite_feedback

RESEARCH_DELAY_SECONDS = 0.35
MAX_COVERAGE_RETRIES = 1
MAX_REPORT_ATTEMPTS = 3  # Initial draft + at most two rewrites; never force-pass.
MAX_REVIEW_ATTEMPTS = 2  # One semantic revision, and no unlimited approval loop.


def _progress_writer():
    """Direct node calls have no stream consumer; graph calls use its real writer."""
    try:
        return get_stream_writer()
    except RuntimeError as exc:
        if str(exc) != 'Called get_config outside of a runnable context':
            raise
        return lambda event: None


def _load_known_systems() -> set[str]:
    """ollama_llm.py/openai_llm.py와 동일한 소스(data/monitoring.json)에서
    시스템 목록을 읽는다 — plan_node가 MCP Prompt에 넘길 system_name을
    쿼리에서 멤버십 매칭으로 뽑아낼 때 쓴다."""
    path = Path(__file__).resolve().parent.parent / "data" / "monitoring.json"
    try:
        return set(json.loads(path.read_text()).keys())
    except (OSError, json.JSONDecodeError):
        return {"auth-service", "web-01"}


KNOWN_SYSTEMS = _load_known_systems()


def _extract_system_name(query: str) -> str:
    """정규식 대신 실제 KNOWN_SYSTEMS 멤버십 매칭 — MCP Prompt(incident_analysis_
    prompt)의 system_name은 "시스템: X" 한 줄짜리 컨텍스트라 항상 정해진 이름
    중 하나로만 고정한다(여러 개 언급돼도 join하지 않음 — 실제 조사 대상 목록은
    이후 plan()의 tasks가 별도로 결정). 쿼리에서 가장 먼저 등장하는 시스템을
    고르고, 하나도 안 걸리면 가장 흔한 사례인 auth-service로 폴백."""
    matches = [(query.index(s), s) for s in KNOWN_SYSTEMS if s in query]
    return min(matches)[1] if matches else "auth-service"


class AgentState(TypedDict, total=False):
    query: str
    thread_id: str
    request_id: str
    requested_targets: list[str]
    verified_targets: list[str]
    unverified_targets: list[str]
    coverage_retry_rounds: int
    history: list[dict]
    route: str
    needs_analysis: bool
    comparison_requested: bool
    tasks: list[dict]
    gate_removed: int
    collected: list[dict]
    replan_rounds: int
    retry_count: int
    parallel_elapsed: float
    draft: str
    validation_attempts: int
    validation_feedback: str
    final: str
    last_prompt: str
    cost_usd: float
    events: list[dict]
    grounded_evidence: dict
    action_catalog: list[dict]
    structured_draft: dict
    draft_mode: str
    validation_result: dict
    response_status: str
    failure_reply: str
    runbook_hits: list[dict]
    runbook_search: dict
    search_count: int
    review_attempts: int
    review_result: dict
    review_status: str
    review_feedback: str | None


def _cost(state: AgentState, usage: dict) -> float:
    return round(state.get("cost_usd", 0.0) + usage["usd"], 6)


@traceable(name="route_node")
async def route_node(state: AgentState, deps: SupervisorDeps) -> dict:
    """Wave 5 — 모든 쿼리가 무거운 파이프라인을 탈 필요는 없다."""
    result, usage = await asyncio.to_thread(deps.fast_llm.route, state["query"])
    event = {"type": "RouteDecided", "route": result["route"],
             'tokens':usage.get('tokens'),'model':usage.get('model')}
    return {"route": result["route"], "needs_analysis": result["needs_analysis"],
            "cost_usd": _cost(state, usage),
            "events": state.get("events", []) + [event]}


@traceable(name="chat_answer_node")
async def chat_answer_node(state: AgentState, deps: SupervisorDeps) -> dict:
    """Wave 5 direct 경로 — 히스토리 기반 응답 (Wave 12 세션 증명 지점).

    토큰 단위 스트리밍 — get_stream_writer()는 /chat/stream처럼 custom 모드로 소비될
    때만 실제로 전송되고, /chat(ainvoke)처럼 스트리밍하지 않는 호출에서는 자동으로
    no-op이 된다."""
    writer = get_stream_writer()
    # Native OpenAI chunks are consumed from messages. Other adapters retain custom.
    native = getattr(deps.fast_llm, "native_message_stream", False)
    reply, usage = await deps.fast_llm.chat(
        state["query"], state.get("history", []),
        on_token=None if native else lambda piece: writer({"type": "AnswerToken", "delta": piece}))
    return {"final": reply, "last_prompt": usage.get("prompt"),
            "cost_usd": _cost(state, usage),
            "events": state["events"] + [{"type": "Finalize", "pipeline": "chat-direct"}]}


@traceable(name="plan_node")
async def plan_node(state: AgentState, deps: SupervisorDeps) -> dict:
    """Wave 4 — Plan/Task 분해. Wave 2의 MCP Prompt가 표준 절차를 깔아준다.

    resource와 마찬가지로 이름을 어느 서버가 들고 있는지 미리 가정하지 않고,
    find_prompt()로 연결된 서버들을 먼저 조회해 찾은 뒤에만 get_prompt를 보낸다."""
    expected = requested_targets(state['query'], KNOWN_SYSTEMS)
    prompt_server = await deps.mcp.find_prompt("incident_analysis_prompt")
    procedure = await deps.mcp.get_prompt(
        "incident_analysis_prompt",
        {"system_name": ', '.join(expected), "symptom": state["query"]},
        server=prompt_server,
    )
    result, usage = await asyncio.to_thread(deps.fast_llm.plan, state["query"], procedure)
    targets = [t["target"] for t in result["tasks"]
               if t["agent"] == "research" and t.get("target")]
    event = {"type": "PlanCreated", "task_count": len(result["tasks"]),
             "targets": targets,'tokens':usage.get('tokens'),'model':usage.get('model')}
    return {"tasks": result["tasks"], "requested_targets":expected,
            "needs_analysis": result["needs_analysis"],
            "cost_usd": _cost(state, usage), "events": state["events"] + [event]}


@traceable(name="gate_node")
async def gate_node(state: AgentState, deps: SupervisorDeps) -> dict:
    """Wave 6 — 권위 게이트. needs_analysis=False인데 섞여 들어온 analysis task를
    프롬프트가 아니라 코드로 강제 제거한다."""
    expected = state.get('requested_targets') or requested_targets(state['query'], KNOWN_SYSTEMS)
    tasks, scope_report = enforce_scope(state['tasks'], expected)
    if not state.get("needs_analysis", False):
        kept = [t for t in tasks if t["agent"] != "analysis"]
        removed = len(tasks) - len(kept)
    else:
        kept, removed = tasks, 0
    event = {"type": "GateApplied", "removed_analysis_tasks": removed}
    return {"tasks": kept, "gate_removed": removed, "requested_targets":expected,
            "events": state["events"] + [{"type":"PlanCoverageChecked", **scope_report},event]}


@traceable(name="research_node")
async def research_node(state: AgentState, deps: SupervisorDeps) -> dict:
    """Wave 15 가드 + Wave 10b 병렬 + Wave 9 재시도 + Wave 2 Tool/Resource 실호출."""
    tasks = state["tasks"]
    research_indices = [i for i, t in enumerate(tasks)
                        if t["agent"] == "research" and t["status"] == "pending"]
    # Wave 15 — 객체가 아니라 '실행할 목록'을 검사한다 (not research_agent 버그의 수정판)
    if not research_indices:
        return {"events": state["events"] +
                [{"type": "ResearchSkipped", "reason": "pending research task 0개"}]}

    retry_total = state.get("retry_count", 0)

    # URI를 코드에 하드코딩하지 않는다. 여러 MCP 서버가 연결돼 있고 그중 어느 서버가
    # "get_incident_runbook" resource를 제공하는지 모른다고 가정하고, 먼저 연결된
    # 서버들을 조회(list)해 유효한 서버를 찾은 뒤에만 그 서버로 요청한다.
    needs_runbook = any(tasks[i]["kind"] != "monitoring" for i in research_indices)
    runbook_server = runbook_uri_template = None
    if needs_runbook:
        runbook_server, runbook_uri_template = await deps.mcp.find_resource_template(
            "get_incident_runbook")

    async def run_one(idx: int) -> dict:
        task = tasks[idx]
        await asyncio.sleep(RESEARCH_DELAY_SECONDS)
        writer = _progress_writer()
        call_id = f"{state.get('request_id', 'direct')}:{task['id']}"
        tool_name = 'get_monitoring_status' if task['kind'] == 'monitoring' else 'read_runbook'
        base = {'call_id':call_id, 'name':tool_name, 'target':task['target']}
        writer({'type':'ToolCallStart', **base})
        if task["kind"] == "monitoring":
            try:
                data, retries = await with_retry(
                    deps.mcp.call_tool, "get_monitoring_status",
                    {"system_name": task["target"]})
                error = monitoring_error(data, task['target'])
                if error is None:
                    writer({'type':'ToolCallEnd', **base, 'retries':retries})
                    return {"idx":idx, "source":"monitoring", "data":data, "retries":retries}
            except Exception as exc:
                # A failed tool is an unavailable measurement, never a zero/healthy value.
                error = type(exc).__name__
                retries = 2 if isinstance(exc, asyncio.TimeoutError) else 0
            writer({'type':'ToolCallError', **base, 'error_type':error})
            return {"idx":idx, "source":"monitoring_error", "retries":retries,
                    "data":{"system_name":task['target'], "error_type":error}}
        uri = runbook_uri_template.format(incident_type=task["target"])
        try:
            data = await deps.mcp.read_resource(uri, server=runbook_server)
        except Exception as exc:
            writer({'type':'ToolCallError', **base, 'error_type':type(exc).__name__})
            raise
        writer({'type':'ToolCallEnd', **base})
        return {"idx": idx, "source": "runbook", "data": data, "retries": 0}

    started = time.perf_counter()
    results = await asyncio.gather(*(run_one(i) for i in research_indices))
    elapsed = round(time.perf_counter() - started, 2)

    new_tasks = [dict(t) for t in tasks]
    collected = list(state.get("collected", []))
    events = list(state["events"])
    for r in results:
        failed = r['source'] == 'monitoring_error'
        new_tasks[r["idx"]]["status"] = 'failed' if failed else 'done'
        collected.append({"source": r["source"], "data": r["data"]})
        retry_total += r["retries"]
        events.append({"type": 'TaskFailed' if failed else 'TaskCompleted', "id": new_tasks[r["idx"]]["id"],
                       "source": r["source"], "target": new_tasks[r["idx"]]["target"],
                       "retries": r["retries"], 'streamed':True,
                       **({'error_type':r['data']['error_type']} if failed else {})})
    return {"tasks": new_tasks, "collected": collected, "retry_count": retry_total,
            "parallel_elapsed": elapsed, "events": events}


# 수집된 모니터링 결과(상태·알림 문구)를 보고 어떤 runbook이 맞는지 고른다. LLM에게
# 다시 묻는 대신 코드 레벨 규칙으로 판단한다 — gate(Wave 6)·replan(Wave 7)과 같은 설계
# 원칙: 모델의 판단을 그대로 믿지 않고, 실측 데이터로 검증 가능한 결정은 코드가 내린다.
_INCIDENT_TYPE_RULES = [
    ("latency", "high-latency"),
    ("pool", "high-latency"),
    ("cpu", "cpu-spike"),
    ("memory", "memory-leak"),
    ("heap", "memory-leak"),
    ("timeout", "network-timeout"),
    ("network", "network-timeout"),
    ("disk", "disk-full"),
    ("down", "db-down"),
]


def _classify_incident_type(collected: list[dict]) -> str:
    text = " ".join(
        f"{c['data'].get('status', '')} " + " ".join(c["data"].get("alerts", []))
        for c in collected if c.get("source") == "monitoring"
    ).lower()
    for keyword, incident_type in _INCIDENT_TYPE_RULES:
        if keyword in text:
            return incident_type
    return "db-down"   # 매칭되는 증상이 없으면 가장 일반적인 장애 대응 절차로 폴백


@traceable(name="replan_check_node")
async def replan_check_node(state: AgentState, deps: SupervisorDeps) -> dict:
    """Check successful monitoring coverage before the existing runbook check."""
    expected = state.get('requested_targets') or requested_targets(state['query'],KNOWN_SYSTEMS)
    verified, missing = coverage(state.get('collected',[]), expected)
    info = {'verified_targets':verified, 'unverified_targets':missing}
    events = state['events'] + [{'type':'CoverageChecked', **info}]
    retry_rounds = state.get('coverage_retry_rounds',0)
    if missing:
        retry = retry_rounds < MAX_COVERAGE_RETRIES
        event = {'type':'ReplanCheck', 'is_sufficient':False,
                 'next_action':'research' if retry else 'analysis',
                 'reason':'coverage_retry' if retry else 'monitoring_unavailable',
                 'missing_targets':missing}
        result = {**info, 'events':events+[event]}
        if retry:
            extra = [{'id':f'coverage-{retry_rounds}-{i}', 'agent':'research',
                      'kind':'monitoring','target':name,'status':'pending'}
                     for i,name in enumerate(missing)]
            result.update(tasks=state['tasks']+extra, coverage_retry_rounds=retry_rounds+1)
        return result
    return {**info,'events':events+[{'type':'ReplanCheck','is_sufficient':False,
        'reason':'retrieve_runbooks','next_action':'retrieve_runbooks'}]}


@traceable(name='retrieve_runbooks_node')
async def retrieve_runbooks_node(state: AgentState,deps: SupervisorDeps) -> dict:
    writer=_progress_writer()
    evidence=build_evidence(state.get('collected',[]),state['request_id'])
    retriever=getattr(deps,'retriever',None);events=list(state['events']);search_count=0
    result={'status':'disabled_offline','hits':[]};books=[];errors=[]
    if retriever is not None:
        monitoring=[r['data'] for r in state['collected'] if r['source']=='monitoring']
        for attempt in range(2):
            base={'call_id':f"{state['request_id']}:search:{attempt}", 'name':'search_runbooks', 'target':'runbooks'}
            writer({'type':'ToolCallStart', **base})
            result=await asyncio.to_thread(retriever.search,state['query'],monitoring,retry=bool(attempt))
            writer({'type':'ToolCallError' if result['status']=='search_error' else 'ToolCallEnd',
                    **base,'status':result['status']})
            search_count+=1
            events.append({'type':'RunbookSearch','attempt':search_count,'status':result['status'],
                'runbook_ids':[r['runbook_id'] for r in result['hits']],
                'seconds':result.get('seconds'),'usage':result.get('usage',{}),
                'relevance_usage':result.get('relevance_usage',{})})
            if result['status'] not in {'not_found','search_error'}:break
        if result['hits']:
            try:
                resource_server,template=await deps.mcp.find_resource_template('get_incident_runbook')
                for hit in result['hits'][:2]:
                    key=hit['runbook_id']
                    base={'call_id':f"{state['request_id']}:runbook:{key}", 'name':'read_runbook', 'target':key}
                    writer({'type':'ToolCallStart', **base})
                    try:
                        if key not in retriever.catalog:raise ValueError('unregistered_runbook_id')
                        text=await deps.mcp.read_resource(template.format(incident_type=key),server=resource_server)
                        if text.startswith('# Runbook Not Found'):raise FileNotFoundError('runbook_not_found')
                        if digest(text)!=retriever.catalog[key]['content_hash'] or digest(text)!=hit['content_hash']:
                            raise ValueError('runbook_source_hash_mismatch')
                        targets=[t for t in hit.get('targets',[]) if t in state['verified_targets']]
                        if not targets and len(state['verified_targets'])==1:targets=list(state['verified_targets'])
                        books.append({**hit,'text':text,'targets':targets})
                        writer({'type':'ToolCallEnd', **base})
                        events.append({'type':'RunbookLoaded','runbook_id':key,'content_hash':hit['content_hash']})
                    except Exception as exc:
                        writer({'type':'ToolCallError', **base, 'error_type':type(exc).__name__})
                        errors.append({'runbook_id':key,'error_type':type(exc).__name__})
            except Exception as exc:errors.append({'error_type':type(exc).__name__})
    status=result['status']
    if result['hits'] and not books:status='read_error'
    elif errors:status='partial'
    actions=prepare_actions(books,evidence)
    if books and not actions:status='rules_unverified'
    covered={a['target'] for a in actions}
    search={**result,'status':status,'errors':errors,
        'uncovered_targets':[t for t in state.get('verified_targets',[]) if t not in covered]}
    collected=state.get('collected',[])+[{'source':'runbook','data':b['text'],
        'runbook_id':b['runbook_id'],'content_hash':b['content_hash']} for b in books]
    events.append({'type':'RunbookEvidenceReady','status':status,'runbook_ids':[b['runbook_id'] for b in books],
                   'uncovered_targets':search['uncovered_targets']})
    return {'grounded_evidence':evidence,'action_catalog':actions,'runbook_hits':books,
        'runbook_search':search,'search_count':search_count,'collected':collected,'events':events}


@traceable(name="analysis_node")
async def analysis_node(state: AgentState, deps: SupervisorDeps) -> dict:
    """Wave 6 보완 — needs_analysis=True가 정당한 경우(모델의 자기모순이 아니라
    실제로 패턴 비교가 필요하다고 판단한 경우)를 위한 실행 경로. gate가 살려둔
    agent=='analysis' task가 있을 때만 수집된 monitoring 결과들을 비교 분석한다."""
    tasks = state["tasks"]
    if state.get('unverified_targets'):
        return {'tasks':[{**t,'status':'skipped'} if t.get('agent')=='analysis' else dict(t) for t in tasks],
                'events':state['events']+[{'type':'AnalysisSkipped','reason':'모니터링 미확인 대상 존재'}]}
    pending = [t for t in tasks if t["agent"] == "analysis" and t["status"] == "pending"]
    if not pending:
        return {"events": state["events"] +
                [{"type": "AnalysisSkipped", "reason": "analysis task 0개"}]}
    # The previous standalone model summary was not consumed by write_grounded.
    # Compare verified current measurements in the renderer; never infer causality.
    new_tasks = [dict(t) for t in tasks]
    for t in new_tasks:
        if t["agent"] == "analysis" and t["status"] == "pending":
            t["status"] = "done"
    event = {"type": "AnalysisCompleted", "mode":"deterministic_comparison", 'tokens':0,
             'scope':'현재 측정값 비교; 공통 원인 미확인'}
    return {"tasks": new_tasks, 'comparison_requested':True,
            "events": state["events"] + [event]}


@traceable(name="write_node")
async def write_node(state: AgentState, deps: SupervisorDeps) -> dict:
    """Keep incident drafts internal until source and condition checks pass."""
    attempt = state.get("validation_attempts", 0) + 1
    if state.get('unverified_targets'):
        draft = incomplete_reply(state.get('collected',[]), state.get('verified_targets',[]),
                                 state['unverified_targets'])
        return {'draft':draft,'draft_mode':'monitoring_incomplete','last_prompt':None,
                'events':state['events']+[{'type':'DraftWritten','attempt':attempt,
                    'length':len(draft),'mode':'coverage_incomplete'}]}
    notes=[]
    for feedback in [state.get('validation_feedback'), state.get('review_feedback')]:
        if feedback:notes.extend(json.loads(feedback))
    report,usage=await deps.quality_llm.write_grounded(state['query'],state['grounded_evidence'],
        state['action_catalog'],attempt,json.dumps(notes,ensure_ascii=False) if notes else None)
    return {'structured_draft':report,'draft_mode':'grounded','draft':'',"last_prompt":usage.get('prompt'),
            "cost_usd": _cost(state, usage),
            "events": state["events"] + [{"type": "DraftWritten", "attempt": attempt,
                                          'mode':'grounded','tokens':usage.get('tokens'),'model':usage.get('model')}]}


@traceable(name="validate_node")
async def validate_node(state: AgentState, deps: SupervisorDeps) -> dict:
    """Validate typed evidence, target and condition contracts; no forced success."""
    attempts = state.get("validation_attempts", 0) + 1
    if state.get('draft_mode')=='monitoring_incomplete':
        expected=incomplete_reply(state['collected'],state['verified_targets'],state['unverified_targets'])
        checked={'passed':state['draft']==expected,'errors':[]};draft=expected
    else:
        checked=validate_report(state['structured_draft'],state['grounded_evidence'],
            state['action_catalog'],state['verified_targets'])
        draft=render_report(state['structured_draft'],state['grounded_evidence'],state['action_catalog'],
            search_status=state['runbook_search']['status'],
            uncovered_targets=state['runbook_search'].get('uncovered_targets'),
            compare_targets=state.get('comparison_requested',False)) if checked['passed'] else ''
    feedback=None if checked['passed'] else json.dumps(checked['errors'],ensure_ascii=False)
    event={'type':'Validation','attempt':attempts,'verdict':'합격' if checked['passed'] else '불합격',
           'errors':checked['errors']}
    return {'validation_attempts':attempts,'validation_feedback':feedback,'validation_result':checked,
            'draft':draft,'events':state['events']+[event]}


@traceable(name='review_node')
async def review_node(state: AgentState, deps: SupervisorDeps) -> dict:
    # Numeric/source checks are a prerequisite, never a decision for this Agent.
    if not state.get('validation_result', {}).get('passed'):
        return {'review_status':'error','review_result':{'error_type':'code_validation_required'}}
    required, reason = review_policy(state)
    reviewer = getattr(deps, 'reviewer', None)
    if not required or reviewer is None:
        reason = reason if not required else 'disabled'
        return {'review_status':'skipped','review_feedback':None,
                'review_result':{'reason':reason},
                'events':state['events']+[{'type':'ReviewerSkipped','reason':reason}]}
    attempt = state.get('review_attempts', 0) + 1
    if attempt > MAX_REVIEW_ATTEMPTS:
        return {'review_status':'rejected','review_result':{'reason':'attempt_limit'}}
    _progress_writer()({'type':'ReviewerStarted','attempt':attempt,'reason':reason})
    usage = {}
    try:
        raw, usage = await reviewer.review(state['query'], state['grounded_evidence'],
            state['action_catalog'], state['draft'], state['requested_targets'])
        result = parse_review(raw, state['requested_targets'], state['action_catalog'])
    except Exception as exc:
        return {'review_attempts':attempt,'review_status':'error',
            'review_result':{'error_type':type(exc).__name__},
            'cost_usd':round(state.get('cost_usd',0)+usage.get('usd',0),6),
            'events':state['events']+[{'type':'ReviewerError','attempt':attempt,'error_type':type(exc).__name__,
                'tokens':usage.get('tokens'),'model':usage.get('model')}]}
    approved = result['approved']
    return {'review_attempts':attempt, 'review_result':result,
        'review_status':'approved' if approved else 'rejected',
        'review_feedback':None if approved else rewrite_feedback(result),
        'cost_usd':_cost(state,usage),
        'events':state['events']+[{'type':'ReviewerCompleted','attempt':attempt,'approved':approved,
            'issue_codes':[i['code'] for i in result['issues']],
            'tokens':usage.get('tokens'),'model':usage.get('model')}]}


async def review_failed_node(state: AgentState,deps: SupervisorDeps) -> dict:
    reply='내용 검토를 완료하거나 통과하지 못해 대응 권고를 확정하지 않았습니다. 실제 조치는 실행하지 않았습니다.'
    return {'response_status':'review_failed','failure_reply':reply,'final':'',
        'events':state['events']+[{'type':'ReviewerFailed','reply':reply,
            'attempts':state.get('review_attempts',0),'status':state.get('review_status')}]}


@traceable(name="finalize_node")
async def finalize_node(state: AgentState, deps: SupervisorDeps) -> dict:
    return {"final": state["draft"],'response_status':'partial' if state.get('unverified_targets') else 'success',
            "events": state["events"] + [{"type": "Finalize", "pipeline": "incident-v2"}]}


async def validation_failed_node(state: AgentState,deps: SupervisorDeps) -> dict:
    facts=[f"{r['data']['system_name']}: 상태={r['data']['status']}, CPU {r['data']['cpu_percent']}%, 메모리 {r['data']['memory_percent']}%"
           for r in state['collected'] if r['source']=='monitoring']
    reply='답변의 근거·조건 검증에 실패해 대응 권고를 확정하지 않았습니다.\n확인된 실습 조회값: '+'; '.join(facts)
    return {'response_status':'validation_failed','failure_reply':reply,'final':'',
        'events':state['events']+[{'type':'ValidationFailed','reply':reply,'attempts':state['validation_attempts']}]}


def _after_route(state: AgentState) -> str:
    return "chat_answer" if state["route"] == "chat" else "plan"


def _after_replan_check(state: AgentState) -> str:
    last = state["events"][-1]
    return last.get('next_action') or ("analysis" if last.get("is_sufficient") else "research")


def _after_validate(state: AgentState) -> str:
    if state['validation_result']['passed']:return 'review'
    return 'write' if state['validation_attempts']<MAX_REPORT_ATTEMPTS else 'validation_failed'


def _after_review(state: AgentState) -> str:
    if state['review_status'] in {'approved','skipped'}:return 'finalize'
    if (state['review_status']=='rejected' and state['review_attempts']<MAX_REVIEW_ATTEMPTS
            and state['validation_attempts']<MAX_REPORT_ATTEMPTS):return 'write'
    return 'review_failed'


def build_graph(deps: SupervisorDeps):
    g = StateGraph(AgentState)
    node = lambda fn: partial(fn, deps=deps)   # Wave 13 — 명시적 의존성 주입
    g.add_node("route", node(route_node))
    g.add_node("chat_answer", node(chat_answer_node))
    g.add_node("plan", node(plan_node))
    g.add_node("gate", node(gate_node))
    g.add_node("research", node(research_node))
    g.add_node("replan_check", node(replan_check_node))
    g.add_node('retrieve_runbooks',node(retrieve_runbooks_node))
    g.add_node("analysis", node(analysis_node))
    g.add_node("write", node(write_node))
    g.add_node("validate", node(validate_node))
    g.add_node('review', node(review_node))
    g.add_node('review_failed', node(review_failed_node))
    g.add_node("finalize", node(finalize_node))
    g.add_node('validation_failed',node(validation_failed_node))

    g.set_entry_point("route")
    g.add_conditional_edges("route", _after_route,
                            {"chat_answer": "chat_answer", "plan": "plan"})
    g.add_edge("chat_answer", END)
    g.add_edge("plan", "gate")
    g.add_edge("gate", "research")
    g.add_edge("research", "replan_check")
    g.add_conditional_edges("replan_check", _after_replan_check,
                            {"analysis": "analysis", "research": "research",'retrieve_runbooks':'retrieve_runbooks'})
    g.add_edge('retrieve_runbooks','analysis')
    g.add_edge("analysis", "write")
    g.add_edge("write", "validate")
    g.add_conditional_edges("validate", _after_validate,
                            {"review": "review", "write": "write",'validation_failed':'validation_failed'})
    g.add_conditional_edges('review', _after_review,
                            {'finalize':'finalize','write':'write','review_failed':'review_failed'})
    g.add_edge('review_failed',END)
    g.add_edge('validation_failed',END)
    g.add_edge("finalize", END)
    return g.compile()
