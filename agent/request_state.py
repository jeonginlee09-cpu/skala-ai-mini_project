"""Create fresh per-request state while retaining only explicit conversation history."""
from uuid import uuid4

REQUEST_ID_PATTERN = r'^[A-Za-z0-9._:-]+$'


def request_id_or_new(value: str | None) -> str:
    return value or str(uuid4())


def initial_state(message: str, thread_id: str, request_id: str, history: list[dict]) -> dict:
    # Every mutable list is new; graph work from the previous question is never reused.
    return {'query':message, 'thread_id':thread_id, 'request_id':request_id,
            'history':[dict(m) for m in history], 'events':[], 'tasks':[], 'collected':[],
            'draft':'', 'final':'', 'last_prompt':None, 'validation_feedback':None,
            'validation_attempts':0, 'replan_rounds':0, 'retry_count':0, 'cost_usd':0.0,
            'requested_targets':[], 'verified_targets':[], 'unverified_targets':[],
            'coverage_retry_rounds':0}


def tag_event(event: dict, thread_id: str, request_id: str) -> dict:
    return {**event, 'thread_id':thread_id, 'request_id':request_id}
