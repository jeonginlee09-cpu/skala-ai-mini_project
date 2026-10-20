"""Synthetic request-boundary tests: no external model or MCP calls."""
import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import httpx
import server
from agent.session_store import SqliteSessionStore


class ConversationGraph:
    async def ainvoke(self, state, **kwargs):
        await asyncio.sleep(0.02)
        query = state['query']
        if '이름이' in query:
            text = '민수님입니다' if any('민수' in m['content'] for m in state['history']) else '이름 미확인'
        elif '민수' in query:
            text = '민수님 반갑습니다'
        elif 'billing' in query:
            text = '장애 대응 답변'
        else:
            text = '안녕하세요'
        return {**state, 'final':text, 'draft':text, 'route':'chat', 'events':[{'type':'Finalize'}]}

    async def astream(self, state, **kwargs):
        result = await self.ainvoke(state, **kwargs)
        yield 'custom', {'type':'AnswerToken', 'delta':result['final']}
        yield 'updates', {'chat_answer':result}


class RequestTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = SqliteSessionStore(Path(self.temp.name)/'sessions.db')
        server.app.state.deps = SimpleNamespace(session_store=self.store, version='v2')
        server.app.state.graph = ConversationGraph()
        server.app.state.llm_backend = 'synthetic_test'
        server.app.state.instance_id = 1
        server.app.state.request_locks = {}
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app),base_url='http://test')

    async def asyncTearDown(self):
        await self.client.aclose()
        self.store.close()
        self.temp.cleanup()

    async def ask(self, message, thread='one', request_id='request-1'):
        response = await self.client.post('/chat',json={'message':message,'thread_id':thread,'request_id':request_id})
        self.assertEqual(response.status_code,200,response.text)
        return response.json()

    async def test_post_exposes_request_id_and_tags_events(self):
        result=await self.ask('안녕하세요',request_id='post-unique')
        self.assertEqual(result['metadata']['request_id'],'post-unique')
        self.assertTrue(all(e['request_id']=='post-unique' for e in result['events']))

    async def test_sse_tokens_and_final_belong_to_same_request(self):
        response=await self.client.get('/chat/stream',params={'message':'안녕하세요','thread_id':'one','request_id':'sse-unique'})
        events=[json.loads(line[5:].strip()) for line in response.text.splitlines()
                if line.startswith('data:') and line[5:].strip()!='[DONE]']
        self.assertTrue(events)
        self.assertTrue(all(e.get('request_id')=='sse-unique' for e in events))
        self.assertEqual(''.join(e['delta'] for e in events if e['type']=='AnswerToken'),
                         next(e['reply'] for e in events if e['type']=='FinalAnswer'))

    async def test_topic_changes_keep_name_without_incident_answer(self):
        await self.ask('billing-service 응답이 느려요',request_id='incident')
        name=await self.ask('제 이름은 민수입니다',request_id='name')
        recall=await self.ask('제 이름이 뭐였죠?',request_id='recall')
        other=await self.ask('제 이름이 뭐였죠?',thread='other',request_id='other')
        self.assertNotIn('장애',name['reply'])
        self.assertIn('민수',recall['reply'])
        self.assertNotIn('민수',other['reply'])

    async def test_overlapping_same_session_requests_see_committed_history(self):
        first=asyncio.create_task(self.ask('제 이름은 민수입니다',request_id='concurrent-name'))
        await asyncio.sleep(0.005)
        second=asyncio.create_task(self.ask('제 이름이 뭐였죠?',request_id='concurrent-recall'))
        await first
        self.assertIn('민수',(await second)['reply'])

    async def test_request_id_invalid_format_returns_422(self):
        response=await self.client.post('/chat',json={'message':'안녕','request_id':'bad\nrequest'})
        self.assertEqual(response.status_code,422)


if __name__=='__main__':unittest.main()
