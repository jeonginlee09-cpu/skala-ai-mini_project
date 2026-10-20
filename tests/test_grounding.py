"""Adversarial reports and policy boundaries, using fixed sample measurements."""
import copy
import unittest
from pathlib import Path

from agent.evidence import build_evidence
from agent.validator import prepare_actions,validate_report
from agent.grounded_report import default_report,render_report
from agent.runbook_retriever import load_catalog

ROOT=Path(__file__).resolve().parents[1]


def observations(memory=88.9,cpu=95.1):
    return [{'source':'monitoring','data':{'system_name':'cache-service','status':'degraded',
        'cpu_percent':cpu,'memory_percent':memory,'alerts':['CPU 91% (threshold 80%)'],
        'last_checked':'2026-10-20T15:00:00+09:00'}}]


class GroundingTests(unittest.TestCase):
    def context(self,memory=88.9,cpu=95.1,book='memory-leak'):
        evidence=build_evidence(observations(memory,cpu),'test')
        source=load_catalog(ROOT/'data/runbooks')[book]
        actions=prepare_actions([{**source,'targets':['cache-service']}],evidence)
        report=default_report(evidence,actions)
        return evidence,actions,report

    def test_wrong_number_is_rejected(self):
        evidence,actions,report=self.context();report['facts'][0]['value']='12345'
        result=validate_report(report,evidence,actions,['cache-service'])
        self.assertFalse(result['passed']);self.assertIn('FACT_VALUE_MISMATCH',[e['code'] for e in result['errors']])

    def test_other_service_cannot_borrow_a_measurement(self):
        evidence,actions,report=self.context();report['facts'][0]['target']='auth-service'
        self.assertFalse(validate_report(report,evidence,actions,['cache-service'])['passed'])

    def test_invented_evidence_id_is_rejected(self):
        evidence,actions,report=self.context();report['facts'][0]['evidence_id']='invented'
        self.assertFalse(validate_report(report,evidence,actions,['cache-service'])['passed'])

    def test_missing_service_fact_is_rejected(self):
        evidence,actions,report=self.context();report['facts']=[]
        self.assertFalse(validate_report(report,evidence,actions,['cache-service'])['passed'])

    def test_restart_threshold_boundary_and_conditional_mode(self):
        for value,expected in [(88.9,'not_met'),(90,'met'),(90.1,'met')]:
            with self.subTest(value=value):
                evidence,actions,report=self.context(memory=value)
                action=next(a for a in actions if a['step_id']==2)
                self.assertEqual(action['condition_status'],expected)
                report['recommendations']=[{'action_id':action['action_id'],'mode':'recommend','execution_status':'proposed'}]
                result=validate_report(report,evidence,actions,['cache-service'])
                self.assertEqual(result['passed'],expected=='met')
                report['recommendations'][0]['mode']='conditional'
                self.assertTrue(validate_report(report,evidence,actions,['cache-service'])['passed'])

    def test_cpu_single_point_does_not_prove_sustained_condition(self):
        evidence,actions,report=self.context(book='cpu-spike')
        action=next(a for a in actions if a['step_id']==3)
        self.assertEqual(action['condition_status'],'unknown')
        report['recommendations']=[{'action_id':action['action_id'],'mode':'recommend','execution_status':'proposed'}]
        self.assertFalse(validate_report(report,evidence,actions,['cache-service'])['passed'])

    def test_completed_action_and_arbitrary_prose_are_rejected(self):
        evidence,actions,report=self.context()
        report['recommendations'][0]['execution_status']='completed'
        self.assertFalse(validate_report(report,evidence,actions,['cache-service'])['passed'])
        report=default_report(evidence,actions);report['answer']='원인은 배포이며 재시작했습니다.'
        self.assertFalse(validate_report(report,evidence,actions,['cache-service'])['passed'])

    def test_invented_cause_is_rejected(self):
        evidence,actions,report=self.context()
        report['hypotheses']=[{'action_id':actions[0]['action_id'],'candidate_id':'confirmed_deployment_cause'}]
        self.assertFalse(validate_report(report,evidence,actions,['cache-service'])['passed'])

    def test_alert_cpu_and_current_cpu_keep_separate_provenance(self):
        evidence,actions,report=self.context()
        cpu=next(e for e in evidence.values() if e['field']=='cpu_percent')
        alert=next(e for e in evidence.values() if e['field']=='alert')
        self.assertEqual(cpu['value'],95.1);self.assertIn('91%',alert['value'])
        self.assertEqual(alert['origin'],'alert_text');self.assertIsNone(alert['observed_at'])

    def test_rendered_report_preserves_unknowns_and_does_not_claim_execution(self):
        evidence,actions,report=self.context(book='cpu-spike')
        result=validate_report(report,evidence,actions,['cache-service'])
        self.assertTrue(result['passed'],result)
        text=render_report(report,evidence,actions,search_status='ok')
        self.assertIn('95.1%',text);self.assertIn('지속',text);self.assertIn('미확인',text)
        self.assertNotIn('재시작했습니다',text);self.assertIn('실행하지 않았습니다',text)

    def test_stale_source_hash_cannot_produce_actions(self):
        evidence,_,_=self.context();book=load_catalog(ROOT/'data/runbooks')['cpu-spike']
        book['content_hash']='wrong'
        self.assertEqual(prepare_actions([{**book,'targets':['cache-service']}],evidence),[])


if __name__=='__main__':unittest.main()
