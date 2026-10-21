"""Run an immutable source variant with common sample fixtures and API accounting."""
import argparse
import asyncio
import contextvars
import hashlib
import json
import os
import sys
import threading
import time
from functools import partial
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import parse_qs

PROJECT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument('--source', type=Path, required=True)
parser.add_argument('--variant', required=True)
parser.add_argument('--port', type=int, required=True)
parser.add_argument('--out', type=Path, required=True)
parser.add_argument('--trace-project-prefix', default='capstone-3way')
args = parser.parse_args()
source, out = args.source.resolve(), args.out.resolve()
out.mkdir(parents=True, exist_ok=True)
from dotenv import load_dotenv
load_dotenv(PROJECT / '.env')
os.environ.update(LLM_BACKEND='openai', SUPERVISOR_VERSION='v2',
    SESSION_DB_PATH=str(source / 'comparison_sessions.db'),
    BENCHMARK_SOURCE=str(source), RUNBOOK_INDEX_PATH=str(source / '.runbook_index'),
    LANGSMITH_PROJECT=args.trace_project_prefix + '-' + args.variant + '-20261021',
    LANGCHAIN_PROJECT=args.trace_project_prefix + '-' + args.variant + '-20261021',
    LANGSMITH_TRACING='true', LANGCHAIN_TRACING_V2='true')
os.chdir(source)
sys.path.insert(0, str(source))

rid = contextvars.ContextVar('comparison_request_id', default='startup')
lock = threading.Lock()
telemetry = out / (args.variant + '.jsonl')


def record(value):
    with lock, telemetry.open('a') as stream:
        stream.write(json.dumps({'request_id': rid.get(), **value}, ensure_ascii=False) + '\n')


# Capture explicit provider fields only: never headers, environment values, or self.
import httpx
real_post = httpx.post


def observed_post(url, *positional, **kwargs):
    started = time.perf_counter()
    response = real_post(url, *positional, **kwargs)
    if str(url).startswith('https://api.openai.com/v1/'):
        payload = kwargs.get('json', {})
        data = response.json() if response.is_success else {}
        record({'kind': 'provider', 'endpoint': str(url).rsplit('/', 1)[-1],
            'model': data.get('model', payload.get('model')), 'http_status': response.status_code,
            'seconds': time.perf_counter() - started, 'usage': data.get('usage', {}),
            'messages': payload.get('messages'),
            'output': data.get('choices', [{}])[0].get('message', {}).get('content')})
    return response


httpx.post = observed_post

# Streaming requests expose usage in the provider's last SSE JSON, not response.json().
real_stream = httpx.AsyncClient.stream


@asynccontextmanager
async def observed_stream(client, method, url, *positional, **kwargs):
    started = time.perf_counter()
    async with real_stream(client, method, url, *positional, **kwargs) as response:
        if not str(url).startswith('https://api.openai.com/v1/'):
            yield response
            return
        payload = kwargs.get('json', {})
        measured = {'usage': {}, 'model': payload.get('model'), 'pieces': []}
        lines = response.aiter_lines

        async def observe_lines():
            async for line in lines():
                if line.startswith('data:') and line[5:].strip() != '[DONE]':
                    data = json.loads(line[5:].strip())
                    measured['model'] = data.get('model', measured['model'])
                    if data.get('usage'): measured['usage'] = data['usage']
                    choices = data.get('choices', [])
                    text = choices[0].get('delta', {}).get('content') if choices else None
                    if text: measured['pieces'].append(text)
                yield line

        response.aiter_lines = observe_lines
        try:
            yield response
        finally:
            record({'kind':'provider', 'endpoint':str(url).rsplit('/',1)[-1],
                'model':measured['model'], 'http_status':response.status_code,
                'seconds':time.perf_counter()-started, 'usage':measured['usage'],
                'messages':payload.get('messages'), 'output':''.join(measured['pieces']),
                'transport':'stream'})


httpx.AsyncClient.stream = observed_stream

import langsmith
real_traceable = langsmith.traceable


def safe_traceable(*positional, **kwargs):
    processor = kwargs.get('process_inputs')
    def clean(inputs):
        clean_inputs = {key: value for key, value in inputs.items() if key != 'self'}
        return processor(clean_inputs) if processor else clean_inputs
    kwargs['process_inputs'] = clean
    return real_traceable(*positional, **kwargs)


langsmith.traceable = safe_traceable
import server
from agent.mcp_client import MCPClient


class ObservedMCP(MCPClient):
    def __init__(self):
        super().__init__(flaky_first_tool_call=False, server_scripts={
            'ops': PROJECT / 'tools/benchmark_snapshot_mcp.py',
            'docs': source / 'mcp_server_docs.py'})

    async def call_tool(self, name, arguments, server='ops'):
        result = await super().call_tool(name, arguments, server)
        record({'kind': 'monitoring', 'name': name, 'arguments': arguments, 'result': result})
        return result

    async def read_resource(self, uri, server):
        result = await super().read_resource(uri, server)
        record({'kind': 'resource', 'uri': str(uri),
            'sha256': hashlib.sha256(result.encode()).hexdigest()})
        return result


server.MCPClient = ObservedMCP


class AccountingMiddleware:
    def __init__(self, app): self.app = app
    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            return await self.app(scope, receive, send)
        query = parse_qs(scope.get('query_string', b'').decode())
        request_id = query.get('request_id', ['health'])[0]
        token = rid.set(request_id)
        try:
            with langsmith.tracing_context(metadata={'comparison_variant': args.variant,
                    'comparison_request_id': request_id}):
                await self.app(scope, receive, send)
        finally:
            rid.reset(token)


import uvicorn
if __name__ == '__main__':
    uvicorn.run(AccountingMiddleware(server.app), host='127.0.0.1', port=args.port, log_level='warning')
