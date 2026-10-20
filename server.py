"""all_total 통합 실습 — FastAPI 백엔드 (Wave 1·3·12·14 축 + 전체 조립).

- Wave 1  : lifespan에서 Agent 번들(MCP 연결·LLM·그래프)을 프로세스당 정확히 1회 생성,
            app.state로 모든 요청이 공유 (creation_count/instance_id로 증명)
- Wave 2  : mcp_server.py를 subprocess(stdio)로 기동해 연결 유지
- Wave 3  : GET /chat/stream — 그래프 실행 과정을 SSE 이벤트로 스트리밍
- Wave 12 : 대화 이력을 SQLite(session.db)에 저장 — 재시작해도 유지
- Wave 14 : SUPERVISOR_VERSION(v1/v2) 환경변수로 legacy/신형 파이프라인 즉시 전환
- LangSmith: 요청당 chat_request 루트 run 아래로 노드·LLM·MCP 호출이 계층 기록됨
"""
import asyncio
import json
import logging
import os
from pathlib import Path
from contextlib import asynccontextmanager

from dotenv import load_dotenv

load_dotenv()   # LANGCHAIN_TRACING_V2 / API_KEY / PROJECT — import보다 먼저

from fastapi import FastAPI, Query
from fastapi.responses import StreamingResponse
from langsmith import traceable
from langsmith.run_helpers import get_current_run_tree
from pydantic import BaseModel, Field

from agent.deps import SupervisorDeps
from agent.fake_llm import FastLLM, QualityLLM
from agent.graph import build_graph
from agent.mcp_client import MCPClient
from agent.session_store import SqliteSessionStore
from agent.request_state import REQUEST_ID_PATTERN, initial_state, request_id_or_new, tag_event
from agent.supervisor_v1 import run_v1

CREATION_COUNT = 0   # Wave 1 — 프로세스 내 Agent 번들 생성 횟수 (항상 1이어야 정상)
logger = logging.getLogger(__name__)


async def _build_retriever(backend):
    mode=os.environ.get('RUNBOOK_SEARCH_MODE','chroma' if backend=='openai' else 'off')
    if mode=='off':return None
    if mode!='chroma':raise ValueError('RUNBOOK_SEARCH_MODE must be chroma or off')
    from agent.embeddings import OpenAIEmbedding
    from agent.relevance import OpenAIRelevanceFilter
    from agent.runbook_retriever import RunbookRetriever,digest,load_catalog
    root=Path(__file__).resolve().parent;policy=json.loads((root/'data/retrieval_policy.json').read_text())
    embedder=OpenAIEmbedding();catalog=load_catalog(root/'data/runbooks')
    actual_hash=digest(json.dumps({k:v['content_hash'] for k,v in catalog.items()},sort_keys=True))
    if policy['model']!=embedder.model or policy['corpus_hash']!=actual_hash:
        raise ValueError('Runbook corpus/model changed: re-evaluate retrieval policy before serving')
    path=Path(os.environ.get('RUNBOOK_INDEX_PATH',str(root/'.runbook_index')))
    searcher=RunbookRetriever(root/'data/runbooks',path,embedder,
        max_distance=policy['max_distance'],secondary_margin=policy['secondary_margin'],
        reranker=OpenAIRelevanceFilter(path))
    await asyncio.to_thread(searcher.prepare)
    return searcher


class ChatRequest(BaseModel):   # Wave 1 — Pydantic 요청 검증
    message: str
    thread_id: str = "default"
    request_id: str | None = Field(default=None, min_length=1, max_length=96,
                                   pattern=REQUEST_ID_PATTERN)


def _build_llms():
    """LLM_BACKEND=fake(기본, 결정론) | ollama(로컬 실모델) | openai(실제 OpenAI API)."""
    backend = os.environ.get("LLM_BACKEND", "fake")
    if backend == "ollama":
        from agent.ollama_llm import OllamaFastLLM, OllamaQualityLLM
        fast = OllamaFastLLM(os.environ.get("OLLAMA_FAST_MODEL", "qwen3:0.6b"),
                             usd_per_1k_tokens=0.00015)
        quality = OllamaQualityLLM(os.environ.get("OLLAMA_QUALITY_MODEL", "qwen3:8b"),
                                   usd_per_1k_tokens=0.005)
        return backend, fast, quality
    if backend == "openai":
        from agent.openai_llm import OpenAIFastLLM, OpenAIQualityLLM
        fast = OpenAIFastLLM(os.environ.get("OPENAI_FAST_MODEL", "gpt-4o-mini"),
                             usd_per_1k_tokens=0.00015)
        quality = OpenAIQualityLLM(os.environ.get("OPENAI_QUALITY_MODEL", "gpt-4o"),
                                   usd_per_1k_tokens=0.005)
        return backend, fast, quality
    fast = FastLLM("fast-model-stub", usd_per_1k_tokens=0.00015)
    quality = QualityLLM("quality-model-stub", usd_per_1k_tokens=0.005)
    return backend, fast, quality


@asynccontextmanager
async def lifespan(app: FastAPI):
    global CREATION_COUNT
    CREATION_COUNT += 1
    backend, fast_llm, quality_llm = _build_llms()
    retriever=await _build_retriever(backend)
    mcp = MCPClient()
    await mcp.start()   # Wave 2 — MCP subprocess는 서버 수명 동안 딱 1개
    app.state.llm_backend = backend
    deps = SupervisorDeps(
        fast_llm=fast_llm,
        quality_llm=quality_llm,
        mcp=mcp,
        session_store=SqliteSessionStore(Path(os.environ.get('SESSION_DB_PATH',
            str(Path(__file__).resolve().parent/'session.db')))),
        version=os.environ.get("SUPERVISOR_VERSION", "v2"),
        retriever=retriever,
    )
    app.state.deps = deps
    app.state.graph = build_graph(deps) if deps.version == "v2" else None
    app.state.instance_id = id(deps)
    # One process serializes overlapping turns in the same conversation.
    app.state.request_locks = {}
    yield
    await mcp.stop()
    deps.session_store.close()
    try:   # 종료 전 LangSmith 배치 전송 플러시 — kill 시 run이 pending으로 남는 것 방지
        from langchain_core.tracers.langchain import wait_for_all_tracers
        wait_for_all_tracers()
    except Exception:
        pass


app = FastAPI(lifespan=lifespan)


@app.get("/health")
async def health():
    return {"status": "ok", "instance_id": app.state.instance_id,
            "creation_count": CREATION_COUNT,
            "supervisor_version": app.state.deps.version,
            "llm_backend": app.state.llm_backend,
            "fast_llm": app.state.deps.fast_llm.name,
            "quality_llm": app.state.deps.quality_llm.name,
            'runbook_search_mode':'chroma_with_quote_checked_rerank' if app.state.deps.retriever else 'disabled_offline',
            'runbook_documents':len(app.state.deps.retriever.catalog) if app.state.deps.retriever else 0}


@app.get("/version")
async def version():
    return {"supervisor_version": app.state.deps.version}


def _metadata(result: dict, run_id: str | None) -> dict:
    return {
        "request_id": result.get("request_id"),
        "requested_targets": result.get("requested_targets",[]),
        "verified_targets": result.get("verified_targets",[]),
        "unverified_targets": result.get("unverified_targets",[]),
        "coverage_retry_rounds": result.get("coverage_retry_rounds",0),
        'validation_result':result.get('validation_result'),
        'search_count':result.get('search_count',0),
        'runbook_search_status':result.get('runbook_search',{}).get('status'),
        'runbook_embedding_usage':result.get('runbook_search',{}).get('usage',{}),
        'runbook_relevance_usage':result.get('runbook_search',{}).get('relevance_usage',{}),
        'runbook_ids':[r['runbook_id'] for r in result.get('runbook_hits',[])],
        'evidence':list(result.get('grounded_evidence',{}).values()),
        "route": result.get("route"),
        "task_count": len(result.get("tasks", [])),
        "gate_removed": result.get("gate_removed", 0),
        "replan_rounds": result.get("replan_rounds", 0),
        "retry_count": result.get("retry_count", 0),
        "parallel_elapsed": result.get("parallel_elapsed"),
        "validation_attempts": result.get("validation_attempts", 0),
        "cost_usd": result.get("cost_usd", 0.0),
        "llm_backend": app.state.llm_backend,
        "supervisor_version": app.state.deps.version,
        "instance_id": app.state.instance_id,
        "langsmith_run_id": run_id,
    }


@traceable(name="chat_request")
async def _handle_chat(message: str, thread_id: str, request_id: str | None = None) -> tuple[dict, str | None]:
    deps = app.state.deps
    rid = request_id_or_new(request_id)
    lock = app.state.request_locks.setdefault(thread_id, asyncio.Lock())
    async with lock:
        history = deps.session_store.load(thread_id)
        state = initial_state(message, thread_id, rid, history)
        config = {"metadata":{"request_id":rid,"conversation_id":thread_id}}
        if deps.version == "v2":
            result = await app.state.graph.ainvoke(state, config=config)
        else:
            result = await run_v1(deps, message, thread_id, history)
        if result.get('response_status')!='validation_failed':
            deps.session_store.append_turn(thread_id,message,result['final'])
    result['request_id'] = rid
    result['events'] = [tag_event(e, thread_id, rid) for e in result.get('events',[])]
    rt = get_current_run_tree()
    return result, (str(rt.id) if rt else None)


@app.post("/chat")
async def chat(req: ChatRequest):
    result, run_id = await _handle_chat(req.message, req.thread_id, req.request_id)
    return {'status':result.get('response_status') or 'success',
            "reply": result.get('final') or result.get('failure_reply',''), "events": result.get("events", []),
            "metadata": _metadata(result, run_id)}


@app.get("/chat/stream")
async def chat_stream(message: str, thread_id: str = "default",
                      request_id: str | None = Query(default=None, min_length=1,
                          max_length=96, pattern=REQUEST_ID_PATTERN)):
    """SSE events carry the same request ID as the final answer."""
    deps = app.state.deps
    rid = request_id_or_new(request_id)

    def encode(event):
        return f"data: {json.dumps(tag_event(event, thread_id, rid), ensure_ascii=False)}\n\n"

    async def gen():
        lock = app.state.request_locks.setdefault(thread_id, asyncio.Lock())
        async with lock:
            logger.info("request_started request_id=%s thread_id=%s", rid, thread_id)
            yield encode({"type":"RequestStarted"})
            history = deps.session_store.load(thread_id)
            try:
                if deps.version != "v2":
                    result = await run_v1(deps, message, thread_id, history)
                    for event in result['events']:
                        yield encode(event)
                    final, last_prompt = result['final'], result.get('last_prompt')
                else:
                    seen, final, last_prompt = 0, "", None
                    response_status='success'
                    config = {"metadata":{"request_id":rid,"conversation_id":thread_id}}
                    async for mode, payload in app.state.graph.astream(
                            initial_state(message, thread_id, rid, history), config=config,
                            stream_mode=["updates", "custom"]):
                        if mode == "custom":
                            yield encode(payload)
                            continue
                        for node_name, delta in payload.items():
                            for event in (delta.get("events") or [])[seen:]:
                                yield encode({**event, "node":node_name})
                            if delta.get("events"):
                                seen = len(delta["events"])
                            if delta.get("final"):
                                final = delta["final"]
                            if delta.get("last_prompt"):
                                last_prompt = delta["last_prompt"]
                            if delta.get('response_status'):response_status=delta['response_status']
                if deps.version!='v2' or response_status!='validation_failed':
                    deps.session_store.append_turn(thread_id,message,final)
                    logger.info("request_completed request_id=%s thread_id=%s", rid, thread_id)
                    yield encode({"type":"FinalAnswer", "reply":final, "prompt":last_prompt})
            except Exception as exc:
                logger.exception("request_failed request_id=%s", rid)
                yield encode({"type":"RequestFailed", "error_type":type(exc).__name__,
                              "message":"요청 처리에 실패했습니다. 요청 ID로 로그를 확인하세요."})
            yield "data: [DONE]\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8100, log_level="warning")
