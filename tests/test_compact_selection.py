"""Keep the evidence/condition policy while shrinking the actual model contract."""
import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from agent import graph
from agent.openai_llm import OpenAIQualityLLM
from agent.validator import validate_report
from tests import test_grounding as fixtures


class CompactSelectionTests(unittest.IsolatedAsyncioTestCase):
    async def invoke(self, selection, book='memory-leak', memory=88.9):
        evidence, actions, _ = fixtures.GroundingTests().context(book=book, memory=memory)
        response = Mock()
        response.json.return_value = {'choices': [{'message': {'content': json.dumps(selection)}}],
                                     'usage': {'total_tokens': 42}}
        llm = OpenAIQualityLLM('gpt-4o', .005)
        llm.api_key = 'controlled-test-key'
        with patch('agent.openai_llm.httpx.post', return_value=response) as post:
            result, usage = await llm.write_grounded('메모리 상태 점검', evidence, actions, 1, None)
        return evidence, actions, result, usage, post.call_args.kwargs['json']

    async def test_model_selects_short_ids_and_code_supplies_facts_and_conditional_mode(self):
        evidence, actions, result, _, payload = await self.invoke(
            {'action_ids': ['A1', 'A2'], 'hypothesis_ids': []})
        self.assertIn('facts', result)
        self.assertEqual(result['facts'][0]['value'], 'degraded')
        self.assertEqual(result['recommendations'][0]['mode'], 'check')
        self.assertEqual(result['recommendations'][1]['mode'], 'conditional')
        self.assertTrue(validate_report(result, evidence, actions, ['cache-service'])['passed'])
        props = payload['response_format']['json_schema']['schema']['properties']
        self.assertEqual(set(props), {'action_ids', 'hypothesis_ids'})

    async def test_model_context_does_not_repeat_source_hashes_paths_or_long_evidence_ids(self):
        _, _, _, _, payload = await self.invoke({'action_ids': ['A1'], 'hypothesis_ids': []})
        text = payload['messages'][1]['content']
        self.assertNotIn('content_hash', text)
        self.assertNotIn('runbook://', text)
        self.assertNotIn('test:observation:', text)
        data = json.loads(text)
        self.assertEqual(len(data['sources']), 1)
        self.assertNotIn('hypotheses', data['actions'][0])

    async def test_duplicate_selections_are_normalized_without_a_model_retry(self):
        evidence, actions, result, _, _ = await self.invoke(
            {'action_ids': ['A1', 'A1'], 'hypothesis_ids': ['H1', 'H1']})
        self.assertIn('recommendations', result)
        self.assertEqual(len(result['recommendations']), 1)
        self.assertEqual(len(result['hypotheses']), 1)
        self.assertTrue(validate_report(result, evidence, actions, ['cache-service'])['passed'])

    async def test_unknown_selection_id_remains_a_validation_failure(self):
        evidence, actions, result, _, _ = await self.invoke(
            {'action_ids': ['A999'], 'hypothesis_ids': []})
        self.assertFalse(validate_report(result, evidence, actions, ['cache-service'])['passed'])

    async def test_model_cannot_supply_even_plausible_measurements_or_completed_actions(self):
        _, _, old_report = fixtures.GroundingTests().context()
        evidence, actions, result, _, _ = await self.invoke(old_report)
        self.assertFalse(validate_report(result, evidence, actions, ['cache-service'])['passed'])

    async def test_unknown_cpu_duration_still_produces_a_conditional_recommendation(self):
        evidence, actions, result, _, _ = await self.invoke(
            {'action_ids': ['A3'], 'hypothesis_ids': []}, book='cpu-spike')
        self.assertIn('recommendations', result)
        self.assertEqual(result['recommendations'][0]['mode'], 'conditional')
        self.assertTrue(validate_report(result, evidence, actions, ['cache-service'])['passed'])

    async def test_compiled_recommendation_keeps_memory_threshold_boundaries(self):
        for value, mode in [(88.9, 'conditional'), (90, 'recommend'), (90.1, 'recommend')]:
            with self.subTest(memory=value):
                evidence, actions, result, _, _ = await self.invoke(
                    {'action_ids': ['A2'], 'hypothesis_ids': []}, memory=value)
                self.assertEqual(result['recommendations'][0]['mode'], mode)
                self.assertTrue(validate_report(result, evidence, actions, ['cache-service'])['passed'])

    async def test_invented_hypothesis_id_is_rejected(self):
        evidence, actions, result, _, _ = await self.invoke(
            {'action_ids': ['A1'], 'hypothesis_ids': ['H999']})
        self.assertFalse(validate_report(result, evidence, actions, ['cache-service'])['passed'])

    async def test_comparison_does_not_spend_an_unused_separate_model_call(self):
        evidence, _, _ = fixtures.GroundingTests().context()
        fast = Mock()
        fast.analyze.side_effect = AssertionError('An unused separate model call must be removed')
        state = {'tasks': [{'agent': 'analysis', 'status': 'pending'}], 'events': [],
                 'collected': [], 'grounded_evidence': evidence}
        result = await graph.analysis_node(state, SimpleNamespace(fast_llm=fast))
        self.assertTrue(result['comparison_requested'])
        self.assertEqual(result['events'][-1]['mode'], 'deterministic_comparison')
        fast.analyze.assert_not_called()

    async def test_full_graph_renders_current_value_comparison_without_claiming_a_cause(self):
        from tests import test_grounded_pipeline as pipeline
        deps = pipeline.GroundedPipelineTests().deps()
        original_plan = deps.fast_llm.plan
        def comparative_plan(query, procedure):
            result, usage = original_plan(query, procedure)
            result['needs_analysis'] = True
            result['tasks'].append({'id': 'compare', 'agent': 'analysis', 'status': 'pending'})
            return result, usage
        deps.fast_llm.plan = comparative_plan
        state = pipeline.GroundedPipelineTests().state()
        with patch.object(graph, 'RESEARCH_DELAY_SECONDS', 0):
            result = await graph.build_graph(deps).ainvoke(state)
        self.assertIn('서비스 간 현재값 비교', result['final'])
        self.assertIn('공통 원인·증가 추세는 확정할 수 없습니다', result['final'])
