"""Semantic reviewer boundaries, not streaming performance tests."""
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import httpx
import server
from agent import graph
from agent.request_state import initial_state
from agent.session_store import SqliteSessionStore
from tests import test_grounded_pipeline as pipeline_fixtures
from tests import test_grounding as grounding_fixtures

APPROVE = {'approved': True, 'issues': []}
REJECT = {'approved': False, 'issues': [{'code':'IRRELEVANT_ACTION',
    'target':'payments-service', 'action_ids':['A1'], 'message':'질문과 절차의 연결을 다시 검토하세요.'}]}


class ControlledReviewer:
    def __init__(self, decisions): self.decisions=decisions;self.calls=0
    async def review(self, query, evidence, actions, draft, targets):
        decision=self.decisions[min(self.calls,len(self.decisions)-1)];self.calls+=1
        if isinstance(decision,Exception): raise decision
        return decision, {'tokens':0,'usd':0,'model':'controlled-reviewer'}


class ReviewerContractTests(unittest.TestCase):
    def test_only_complex_grounded_requests_need_review(self):
        from agent.semantic_review import review_policy
        state={'validation_result':{'passed':True},'verified_targets':['payments-service'],
               'action_catalog':[{}],'runbook_hits':[{}]}
        self.assertFalse(review_policy(state)[0])
        self.assertTrue(review_policy({**state,'requested_targets':['payments-service','cache-service']})[0])
        self.assertTrue(review_policy({**state,'runbook_hits':[{},{}]})[0])
        self.assertTrue(review_policy({**state,'needs_analysis':True})[0])
        self.assertFalse(review_policy({**state,'unverified_targets':['cache-service']})[0])

    def test_review_cannot_change_report_or_approve_with_issues(self):
        from agent.semantic_review import parse_review
        _,actions,_=grounding_fixtures.GroundingTests().context()
        for raw in [{**APPROVE,'report':{}}, {'approved':True,'issues':REJECT['issues']},
                    {'approved':False,'issues':[]}]:
            with self.subTest(raw=raw),self.assertRaises(ValueError):
                parse_review(raw,['cache-service'],actions)

    def test_unknown_target_and_action_are_rejected(self):
        from agent.semantic_review import parse_review
        _,actions,_=grounding_fixtures.GroundingTests().context()
        for target,ids in [('billing-service',['A1']),('cache-service',['A999'])]:
            raw={'approved':False,'issues':[{'code':'IRRELEVANT_ACTION','target':target,
                 'action_ids':ids,'message':'근거를 확인하세요.'}]}
            with self.subTest(target=target),self.assertRaises(ValueError):
                parse_review(raw,['cache-service'],actions)

    def test_new_request_does_not_inherit_previous_review(self):
        first=initial_state('질문','t','a',[])
        first['review_result']['approved']=False
        first['review_attempts']=2
        second=initial_state('다음 질문','t','b',[])
        self.assertEqual(second['review_attempts'],0)
        self.assertEqual(second['review_result'],{})


class ReviewerGraphTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.delay=patch.object(graph,'RESEARCH_DELAY_SECONDS',0);self.delay.start()
    async def asyncTearDown(self): self.delay.stop()

    def setup_case(self,decisions,bad_until=0):
        deps=pipeline_fixtures.GroundedPipelineTests().deps(bad_until)
        deps.reviewer=ControlledReviewer(decisions)
        return deps,pipeline_fixtures.GroundedPipelineTests().state()

    async def test_reviewer_accepts_only_after_code_validation(self):
        deps,state=self.setup_case([APPROVE])
        result=await graph.build_graph(deps).ainvoke(state)
        self.assertEqual(result['review_status'],'approved')
        self.assertEqual(result['review_attempts'],1)
        self.assertTrue(result['validation_result']['passed'])
        self.assertEqual(result['response_status'],'success')

    async def test_rejection_reaches_writer_then_both_checks_run_again(self):
        deps,state=self.setup_case([REJECT,APPROVE]);feedback=[]
        original=deps.quality_llm.write_grounded
        async def write(query,evidence,actions,attempt,notes):
            feedback.append(notes)
            return await original(query,evidence,actions,attempt,notes)
        deps.quality_llm.write_grounded=write
        result=await graph.build_graph(deps).ainvoke(state)
        self.assertEqual(result['review_attempts'],2)
        self.assertEqual(result['validation_attempts'],2)
        self.assertEqual(result['review_status'],'approved')
        self.assertIn('SEMANTIC_REVIEW',feedback[1])

    async def test_second_rejection_does_not_force_pass(self):
        deps,state=self.setup_case([REJECT,REJECT])
        result=await graph.build_graph(deps).ainvoke(state)
        self.assertEqual(result['response_status'],'review_failed')
        self.assertEqual(deps.reviewer.calls,2)
        self.assertEqual(deps.quality_llm.calls,2)
        self.assertFalse(result['final'])
        self.assertTrue(any(e['type']=='ReviewerFailed' for e in result['events']))

    async def test_reviewer_error_is_explicit_failure(self):
        deps,state=self.setup_case([TimeoutError('controlled')])
        result=await graph.build_graph(deps).ainvoke(state)
        self.assertEqual(result['response_status'],'review_failed')
        self.assertEqual(result['review_status'],'error')
        self.assertEqual(deps.reviewer.calls,1)
        self.assertFalse(result['final'])

    async def test_invalid_review_does_not_pass_or_hide_known_api_usage(self):
        deps,state=self.setup_case([APPROVE])
        async def malformed(*args):
            return {**APPROVE,'report':{'facts':'invented'}},{'tokens':15,'usd':.03}
        deps.reviewer.review=malformed
        result=await graph.build_graph(deps).ainvoke(state)
        self.assertEqual(result['response_status'],'review_failed')
        self.assertAlmostEqual(result['cost_usd'],.03)
        event=next(e for e in result['events'] if e['type']=='ReviewerError')
        self.assertEqual(event['tokens'],15)

    async def test_reviewer_cannot_override_wrong_measurements(self):
        deps,state=self.setup_case([APPROVE],bad_until=99)
        result=await graph.build_graph(deps).ainvoke(state)
        self.assertEqual(result['response_status'],'validation_failed')
        self.assertEqual(deps.reviewer.calls,0)

    async def test_simple_query_bypasses_reviewer(self):
        deps,_=self.setup_case([RuntimeError('must not call')])
        state=initial_state('payments-service CPU 확인해줘','t','simple',[])
        result=await graph.build_graph(deps).ainvoke(state)
        self.assertEqual(result['review_status'],'skipped')
        self.assertEqual(deps.reviewer.calls,0)
        self.assertEqual(result['response_status'],'success')

    async def test_missing_manual_does_not_look_reviewed(self):
        deps,state=self.setup_case([RuntimeError('must not call')])
        deps.retriever=pipeline_fixtures.GroundedPipelineTests().deps(retrieval_status='not_found').retriever
        result=await graph.build_graph(deps).ainvoke(state)
        self.assertEqual(result['review_status'],'skipped')
        self.assertEqual(deps.reviewer.calls,0)

    async def test_failed_review_is_not_saved_as_successful_turn(self):
        deps,state=self.setup_case([REJECT,REJECT])
        with tempfile.TemporaryDirectory() as temp:
            deps.session_store=SqliteSessionStore(Path(temp)/'session.db')
            server.app.state.deps=deps;server.app.state.graph=graph.build_graph(deps)
            server.app.state.request_locks={};server.app.state.llm_backend='controlled'
            server.app.state.instance_id=1
            try:
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app),base_url='http://test') as client:
                    result=(await client.post('/chat',json={'message':state['query'],'thread_id':'review-fail'})).json()
                self.assertEqual(result['status'],'review_failed')
                self.assertEqual(deps.session_store.load('review-fail'),[])
            finally: deps.session_store.close()

    async def test_openai_reviewer_has_its_own_strict_read_only_contract(self):
        from agent.openai_llm import OpenAIReviewer
        evidence,actions,report=grounding_fixtures.GroundingTests().context()
        response=Mock();response.json.return_value={'choices':[{'message':{'content':json.dumps(APPROVE)}}],
                                                   'usage':{'total_tokens':12}}
        reviewer=OpenAIReviewer('gpt-4o',.005);reviewer.api_key='controlled-test-key'
        with patch('agent.openai_llm.httpx.post',return_value=response) as post:
            result,_=await reviewer.review('메모리 확인',evidence,actions,'검수된 초안',['cache-service'])
        payload=post.call_args.kwargs['json']
        self.assertIn('독립적인 내용 검토',payload['messages'][0]['content'])
        self.assertTrue(payload['response_format']['json_schema']['strict'])
        self.assertNotIn('tools',payload)
        self.assertEqual(result,APPROVE)
