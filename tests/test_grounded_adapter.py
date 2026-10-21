"""Inspect the actual REST contract without making an external request."""
import unittest
from unittest.mock import Mock,patch

from agent.openai_llm import OpenAIQualityLLM
from tests import test_grounding as fixtures


class AdapterTests(unittest.IsolatedAsyncioTestCase):
    async def test_openai_uses_strict_schema_and_never_streams_unvalidated_draft(self):
        evidence,actions,_=fixtures.GroundingTests().context()
        import json
        response=Mock();response.json.return_value={'choices':[{'message':{'content':json.dumps({'action_ids':['A1'],'hypothesis_ids':[]})}}],
                                                   'usage':{'total_tokens':42}}
        llm=OpenAIQualityLLM('gpt-4o',.005);llm.api_key='controlled-test-key'
        with patch('agent.openai_llm.httpx.post',return_value=response) as post:
            result,usage=await llm.write_grounded('고정 테스트',evidence,actions,1,None)
        payload=post.call_args.kwargs['json']
        self.assertTrue(payload['response_format']['json_schema']['strict'])
        self.assertFalse(payload.get('stream',False))
        self.assertEqual(result['recommendations'][0]['mode'],'check')
        self.assertEqual(len(result['facts']),3)
        self.assertEqual(usage['tokens'],42)  # Mock usage, not a measured API result.

    async def test_refusal_or_missing_content_does_not_become_a_successful_report(self):
        response=Mock();response.json.return_value={'choices':[{'message':{'content':None}}]}
        llm=OpenAIQualityLLM('gpt-4o',.005);llm.api_key='controlled-test-key'
        with patch('agent.openai_llm.httpx.post',return_value=response):
            result,_=await llm.write_grounded('test',{},[],1,None)
        self.assertEqual(result,{'invalid_json':True})
