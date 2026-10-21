"""Ensure 3-way metrics count requests, calls and token categories separately."""
import unittest
from unittest.mock import Mock

from tools.report_three_versions import p95, provider_group, summarize


class AccountingTests(unittest.TestCase):
    def test_p95_uses_nearest_rank(self):
        self.assertEqual(p95(list(range(1,34))),32)
        self.assertIsNone(p95([]))

    def test_relevance_and_embeddings_are_not_counted_as_core_generation(self):
        self.assertEqual(provider_group({'endpoint':'embeddings'}),'embedding')
        self.assertEqual(provider_group({'endpoint':'completions','messages':[{
            'content':'운영 런북 검색 후보의 적용 가능성을 판정하라.'}]}),'relevance')
        self.assertEqual(provider_group({'endpoint':'completions','messages':[{
            'content':'운영 보고서에 필요한 절차를 고르세요.'}]}),'core')

    def test_one_request_can_contain_two_monitoring_calls_and_one_extra_target(self):
        rows=[{'request_id':'r1','expected_targets':['a','b'],'actual_targets':['a','b','c'],
            'target_set_match':False,'status':'ok','total_s':2,'draft_exposed':False,
            'validation_attempts':1,'events':[],'case_id':'Q01','name_present':None}]
        telemetry=[{'kind':'monitoring','request_id':'r1'} for _ in range(3)]+[{
            'kind':'provider','request_id':'r1','endpoint':'completions','model':'test',
            'messages':[{'content':'report'}],'usage':{'prompt_tokens':10,'completion_tokens':2,'total_tokens':12}}]
        result=summarize(rows,telemetry)
        self.assertEqual(result['requests'],1)
        self.assertEqual(result['monitoring_calls'],3)
        self.assertEqual(result['outside_scope_targets'],1)
        self.assertEqual(result['core_tokens_incident'],12)

    def test_remote_trace_query_respects_the_observed_hundred_run_api_limit(self):
        from tools import verify_three_versions as verifier
        query = getattr(verifier, 'comparison_trace_query', None)
        self.assertTrue(callable(query), 'Trace query helper must enforce the provider limit')
        client = Mock()
        client.list_runs.return_value = []
        self.assertEqual(list(query(client, 'optimized')), [])
        self.assertLessEqual(client.list_runs.call_args.kwargs['limit'], 100)
