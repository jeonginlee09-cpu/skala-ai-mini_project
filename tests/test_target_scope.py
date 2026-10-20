"""Controlled planner/tool outputs; these are not measured OpenAI results."""
import unittest
from collections import Counter
from types import SimpleNamespace
from unittest.mock import patch

from agent import graph as module
from agent.request_state import initial_state


def task(name):
    return {'id':'task-'+name,'agent':'research','kind':'monitoring','target':name,'status':'pending'}


def measurement(name):
    return {'system_name':name,'status':'degraded','cpu_percent':50.0,
            'memory_percent':60.0,'alerts':['CPU elevated'],'last_checked':'synthetic-test'}


class ControlledMCP:
    def __init__(self, failures=0, wrong_target=False):
        self.calls=Counter();self.failures=failures;self.wrong_target=wrong_target
    async def find_prompt(self,name):return 'test'
    async def get_prompt(self,*args,**kwargs):return 'Synthetic procedure'
    async def find_resource_template(self,name):return 'test','runbook://incident-response/{incident_type}'
    async def read_resource(self,*args,**kwargs):return '# Synthetic Runbook\n1. Check the measured values.'
    async def call_tool(self,name,args):
        target=args['system_name'];self.calls[target]+=1
        if target=='cache-service':
            if self.calls[target]<=self.failures:raise ValueError('synthetic unavailable')
            if self.wrong_target:return measurement('auth-service')
        return measurement(target)


class ControlledFast:
    def route(self,query):return {'route':'incident','needs_analysis':False},{'usd':0}
    def plan(self,query,procedure):
        return {'needs_analysis':False,'tasks':[task('payments-service'),task('auth-service')]},{'usd':0}


class ControlledQuality:
    def __init__(self):self.calls=0
    async def write(self,query,collected,attempt,feedback,on_token=None):
        self.calls+=1
        names=[c['data']['system_name'] for c in collected if c['source']=='monitoring']
        text='1) 확인한 서비스: '+', '.join(names)+'\n2) 자료를 바탕으로 확인합니다.\n3) 추가 측정이 필요합니다.'
        if on_token:on_token(text)
        return text,{'usd':0,'prompt':'synthetic test'}


class TargetScopeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.delay=patch.object(module,'RESEARCH_DELAY_SECONDS',0,create=True)
        self.delay.start()

    async def asyncTearDown(self):self.delay.stop()

    def state(self,tasks=None):
        state=initial_state('payments-service랑 cache-service 둘 다 확인해줘','test','test-request',[])
        state.update(requested_targets=['payments-service','cache-service'],tasks=tasks or [])
        return state

    async def test_gate_repairs_partial_omission_and_removes_unrequested_service(self):
        original=[task('payments-service'),task('auth-service')]
        result=await module.gate_node(self.state(original),None)
        self.assertEqual({t['target'] for t in result['tasks']},{'payments-service','cache-service'})
        self.assertEqual([t['target'] for t in original],['payments-service','auth-service'])

    async def test_gate_keeps_analysis_when_requested_and_deduplicates_monitoring(self):
        state=self.state([task('payments-service'),task('payments-service'),{'id':'analysis','agent':'analysis','status':'pending'}])
        state['needs_analysis']=True
        result=await module.gate_node(state,None)
        self.assertEqual(sum(t.get('kind')=='monitoring' for t in result['tasks']),2)
        self.assertEqual(sum(t['agent']=='analysis' for t in result['tasks']),1)

    async def test_tool_failure_does_not_discard_successful_sibling(self):
        mcp=ControlledMCP(failures=99)
        result=await module.research_node(self.state([task('payments-service'),task('cache-service')]),SimpleNamespace(mcp=mcp))
        successful=[c['data']['system_name'] for c in result['collected'] if c['source']=='monitoring']
        self.assertEqual(successful,['payments-service'])
        self.assertTrue(any(c['source']=='monitoring_error' for c in result['collected']))

    async def test_replan_checks_missing_service_even_when_runbook_exists(self):
        state=self.state([task('payments-service')]);state['collected']=[{'source':'monitoring','data':measurement('payments-service')},{'source':'runbook','data':'available'}]
        result=await module.replan_check_node(state,None)
        self.assertTrue(any(t.get('target')=='cache-service' and t['status']=='pending' for t in result['tasks']))

    async def run_graph(self,mcp):
        quality=ControlledQuality()
        deps=SimpleNamespace(fast_llm=ControlledFast(),quality_llm=quality,mcp=mcp)
        result=await module.build_graph(deps).ainvoke(self.state())
        return result,quality

    async def test_full_graph_only_queries_requested_targets(self):
        mcp=ControlledMCP();result,_=await self.run_graph(mcp)
        self.assertEqual(set(mcp.calls),{'payments-service','cache-service'})
        self.assertEqual(result['unverified_targets'],[])

    async def test_second_coverage_attempt_recovers_only_failed_target(self):
        mcp=ControlledMCP(failures=1);result,_=await self.run_graph(mcp)
        self.assertEqual(mcp.calls['payments-service'],1)
        self.assertEqual(mcp.calls['cache-service'],2)
        self.assertEqual(result['unverified_targets'],[])

    async def test_persistent_failure_is_bounded_and_reported_without_llm_guess(self):
        mcp=ControlledMCP(failures=99);result,quality=await self.run_graph(mcp)
        self.assertEqual(mcp.calls['cache-service'],2)
        self.assertIn('cache-service',result['unverified_targets'])
        self.assertIn('미확인',result['final'])
        self.assertEqual(quality.calls,0)

    async def test_response_for_wrong_service_is_not_evidence(self):
        mcp=ControlledMCP(wrong_target=True);result,_=await self.run_graph(mcp)
        self.assertIn('cache-service',result['unverified_targets'])
        self.assertFalse(any(c['source']=='monitoring' and c['data']['system_name']=='auth-service' for c in result['collected']))


if __name__=='__main__':unittest.main()
