"""Follow-up scope tests use old conversation text, but fresh controlled measurements."""
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from agent import graph
from agent.request_state import initial_state
from tests.test_target_scope import ControlledFast, ControlledMCP, ControlledQuality


class FollowupContextTests(unittest.TestCase):
    known={'payments-service','cache-service','auth-service'}

    def resolve(self,question,history):
        function=getattr(graph,'resolve_followup',None)
        self.assertTrue(callable(function),'An explicit follow-up scope resolver is required')
        return function(question,history,self.known)

    def test_singular_reference_uses_prior_user_target_not_assistant_claims(self):
        result=self.resolve('그 서비스의 메모리도 확인해줘',[
            {'role':'user','content':'payments-service CPU 확인'},
            {'role':'assistant','content':'cache-service CPU 99%라고 가정'}])
        self.assertEqual(result['targets'],['payments-service'])
        self.assertIn('payments-service',result['effective_query'])
        self.assertNotIn('99%',result['effective_query'])

    def test_multiple_antecedents_require_clarification_for_singular_reference(self):
        result=self.resolve('그 서비스 메모리도 확인해줘',[{'role':'user','content':'payments-service와 cache-service 비교'}])
        self.assertTrue(result['needs_clarification'])
        self.assertEqual(result['targets'],[])

    def test_plural_reference_can_keep_both_prior_targets(self):
        result=self.resolve('그 두 서비스의 메모리도 비교해줘',[{'role':'user','content':'payments-service와 cache-service 확인'}])
        self.assertEqual(result['targets'],['payments-service','cache-service'])

    def test_explicit_new_target_overrides_history(self):
        result=self.resolve('cache-service 메모리 확인',[{'role':'user','content':'payments-service 확인'}])
        self.assertEqual(result['effective_query'],'cache-service 메모리 확인')
        self.assertEqual(result['targets'],['cache-service'])

    def test_unknown_explicit_target_is_not_silently_replaced_by_history(self):
        question='billing-service도 확인해줘'
        result=self.resolve(question,[{'role':'user','content':'payments-service 확인'}])
        self.assertEqual(result['effective_query'],question)
        self.assertEqual(result['targets'],[])

    def test_reference_without_history_requests_a_target(self):
        self.assertTrue(self.resolve('그 서비스 상태도 확인해줘',[])['needs_clarification'])

    def test_unrelated_chat_does_not_inherit_an_incident(self):
        result=self.resolve('제 이름은 민수입니다',[{'role':'user','content':'payments-service 장애'}])
        self.assertEqual(result['effective_query'],'제 이름은 민수입니다')
        self.assertFalse(result['needs_clarification'])


class FollowupGraphTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.delay=patch.object(graph,'RESEARCH_DELAY_SECONDS',0);self.delay.start()
    async def asyncTearDown(self):self.delay.stop()

    def deps(self):
        class RecordingFast(ControlledFast):
            def route(self,query):
                self.route_query=query
                return super().route(query)
            def plan(self,query,procedure):
                self.plan_query=query
                return super().plan(query,procedure)
        return SimpleNamespace(fast_llm=RecordingFast(),quality_llm=ControlledQuality(),mcp=ControlledMCP())

    async def test_resolved_scope_reaches_route_plan_and_new_monitoring(self):
        deps=self.deps()
        state=initial_state('그 서비스 메모리도 확인해줘','thread','followup',[
            {'role':'user','content':'payments-service CPU 확인'},
            {'role':'assistant','content':'과거 CPU 99%'}])
        result=await graph.build_graph(deps).ainvoke(state)
        self.assertEqual(result['requested_targets'],['payments-service'])
        self.assertIn('payments-service',deps.fast_llm.route_query)
        self.assertIn('payments-service',deps.fast_llm.plan_query)
        self.assertEqual(dict(deps.mcp.calls),{'payments-service':1})
        self.assertEqual(result['query'],state['query'])
        self.assertTrue(all(e['value']!=99 for e in result['grounded_evidence'].values()))

    async def test_ambiguous_reference_does_not_query_auth_fallback(self):
        deps=self.deps()
        state=initial_state('그 서비스 상태 확인','thread','ambiguous',[
            {'role':'user','content':'payments-service와 cache-service 확인'}])
        result=await graph.build_graph(deps).ainvoke(state)
        self.assertEqual(result['response_status'],'clarification_required')
        self.assertFalse(deps.mcp.calls)
        self.assertEqual(deps.quality_llm.calls,0)

    async def test_resolved_operational_reference_cannot_bypass_fresh_lookup_as_chat(self):
        deps=self.deps()
        original_route=deps.fast_llm.route
        def mistaken_route(query):
            _,usage=original_route(query)
            return {'route':'chat','needs_analysis':True},usage
        async def stale_chat(query,history,on_token=None):
            return '과거 메모리 99%',{'usd':0}
        deps.fast_llm.route=mistaken_route
        deps.fast_llm.chat=stale_chat
        state=initial_state('그 두 서비스의 메모리도 비교해줘','thread','route-guard',[
            {'role':'user','content':'payments-service와 cache-service 상태를 비교해줘'},
            {'role':'assistant','content':'과거 메모리 99%'}])
        result=await graph.build_graph(deps).ainvoke(state)
        self.assertEqual(result['route'],'incident')
        self.assertEqual(dict(deps.mcp.calls),{'payments-service':1,'cache-service':1})
        event=next(e for e in result['events'] if e['type']=='RouteDecided')
        self.assertEqual(event['model_route'],'chat')
        self.assertEqual(event['override_reason'],'resolved_operational_followup')
        self.assertNotIn('과거 메모리 99%',result['final'])

    async def test_new_session_without_history_does_not_inherit_a_target(self):
        deps=self.deps()
        result=await graph.build_graph(deps).ainvoke(initial_state('그 서비스 상태 확인','other','isolated',[]))
        self.assertEqual(result['response_status'],'clarification_required')
        self.assertFalse(deps.mcp.calls)
