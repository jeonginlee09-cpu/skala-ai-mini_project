"""Full graph and HTTP/SSE policy checks with real local rules, controlled providers."""
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import httpx
import server
from agent import graph as module
from agent.grounded_report import default_report
from agent.request_state import initial_state
from agent.runbook_retriever import load_catalog
from agent.session_store import SqliteSessionStore
from tests.test_target_scope import ControlledFast,ControlledMCP,measurement

ROOT=Path(__file__).resolve().parents[1]


class FixtureRetriever:
    def __init__(self,status='ok'):
        self.catalog=load_catalog(ROOT/'data/runbooks');self.calls=0;self.status=status
    def search(self,question,monitoring,max_results=2,retry=False):
        self.calls+=1
        hits=[{k:v for k,v in self.catalog[key].items() if k!='text'}|{'targets':[row['system_name']]}
              for row,key in zip(monitoring,['cpu-spike','memory-leak'])] if self.status=='ok' else []
        return {'status':self.status,'hits':hits,'seconds':0,'usage':{},'uncovered_targets':[]}


class FixtureMCP(ControlledMCP):
    async def read_resource(self,uri,server):
        return (ROOT/'data/runbooks'/(uri.rsplit('/',1)[-1]+'.md')).read_text()


class ReportWriter:
    def __init__(self,bad_until=0):self.calls=0;self.bad_until=bad_until
    async def write_grounded(self,query,evidence,actions,attempt,feedback):
        self.calls+=1;report=default_report(evidence,actions)
        if self.calls<=self.bad_until:
            report['facts'][0]['value']='UNVALIDATED_DRAFT_MUST_NOT_LEAK'
        return report,{'usd':0,'tokens':0,'mode':'controlled_test'}


class GroundedPipelineTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.delay=patch.object(module,'RESEARCH_DELAY_SECONDS',0);self.delay.start()
    async def asyncTearDown(self):self.delay.stop()

    def deps(self,bad_until=0,retrieval_status='ok'):
        return SimpleNamespace(fast_llm=ControlledFast(),quality_llm=ReportWriter(bad_until),
            mcp=FixtureMCP(),retriever=FixtureRetriever(retrieval_status),version='v2')

    def state(self):return initial_state('payments-service랑 cache-service 둘 다 확인해줘','test','grounded-test',[])

    async def test_retrieval_uses_mcp_and_keeps_both_sources(self):
        deps=self.deps();result=await module.build_graph(deps).ainvoke(self.state())
        self.assertEqual({r['runbook_id'] for r in result['runbook_hits']},{'cpu-spike','memory-leak'})
        self.assertTrue(result['validation_result']['passed'])
        self.assertIn('조건부 권고',result['final']);self.assertIn('미확인',result['final'])
        self.assertEqual(result['response_status'],'success')

    async def test_bad_draft_is_rewritten_without_exposure(self):
        deps=self.deps(bad_until=1);events=[];result=None
        async for mode,payload in module.build_graph(deps).astream(self.state(),stream_mode=['custom','updates']):
            if mode=='custom':events.append(payload)
            else:
                for update in payload.values():
                    if 'final' in update:result=update['final']
        self.assertEqual(deps.quality_llm.calls,2)
        self.assertFalse(any(e.get('type')=='AnswerToken' for e in events))
        self.assertNotIn('UNVALIDATED_DRAFT',result)

    async def test_persistent_invalid_report_never_force_passes(self):
        deps=self.deps(bad_until=99);result=await module.build_graph(deps).ainvoke(self.state())
        self.assertEqual(deps.quality_llm.calls,3)
        self.assertEqual(result['response_status'],'validation_failed')
        self.assertFalse(result['validation_result']['passed'])
        self.assertFalse(result.get('final'))
        self.assertFalse(any(e['type']=='Finalize' for e in result['events']))
        self.assertNotIn('UNVALIDATED_DRAFT',result['failure_reply'])

    async def test_search_failure_is_bounded_and_not_replaced_by_db_down(self):
        deps=self.deps(retrieval_status='not_found');result=await module.build_graph(deps).ainvoke(self.state())
        self.assertEqual(deps.retriever.calls,2)
        self.assertEqual(result['runbook_hits'],[])
        self.assertIn('not_found',result['final'])
        self.assertEqual(result['action_catalog'],[])

    async def test_one_mcp_resource_failure_keeps_other_source_and_measurements(self):
        deps=self.deps();original=deps.mcp.read_resource
        async def broken(uri,server):
            if uri.endswith('memory-leak'):raise OSError('controlled read failure')
            return await original(uri,server)
        deps.mcp.read_resource=broken;result=await module.build_graph(deps).ainvoke(self.state())
        self.assertEqual(result['runbook_search']['status'],'partial')
        self.assertEqual(len(result['runbook_hits']),1)
        self.assertIn('cache-service',result['final']);self.assertIn('payments-service',result['final'])

    async def test_http_and_sse_failure_have_no_success_final_or_saved_turn(self):
        with tempfile.TemporaryDirectory() as folder:
            deps=self.deps(bad_until=99);deps.session_store=SqliteSessionStore(Path(folder)/'session.db')
            server.app.state.deps=deps;server.app.state.graph=module.build_graph(deps)
            server.app.state.request_locks={};server.app.state.llm_backend='controlled_test';server.app.state.instance_id=1
            try:
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app),base_url='http://test') as client:
                    post=(await client.post('/chat',json={'message':self.state()['query'],'thread_id':'post-fail'})).json()
                    self.assertEqual(post['status'],'validation_failed')
                    response=await client.get('/chat/stream',params={'message':self.state()['query'],'thread_id':'sse-fail','request_id':'failure-id'})
                events=[json.loads(line[5:]) for line in response.text.splitlines() if line.startswith('data:') and line[5:].strip()!='[DONE]']
                self.assertTrue(any(e['type']=='ValidationFailed' for e in events))
                self.assertFalse(any(e['type'] in ['FinalAnswer','AnswerToken','Finalize'] for e in events))
                self.assertTrue(all(e['request_id']=='failure-id' for e in events))
                self.assertNotIn('UNVALIDATED_DRAFT',response.text)
                self.assertEqual(deps.session_store.load('post-fail'),[])
                self.assertEqual(deps.session_store.load('sse-fail'),[])
            finally:deps.session_store.close()


if __name__=='__main__':unittest.main()
