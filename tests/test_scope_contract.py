import unittest
from agent.target_scope import requested_targets, monitoring_error, coverage
from agent.request_state import initial_state
from tests.test_target_scope import measurement


class ScopeContractTests(unittest.TestCase):
    def test_service_names_use_boundaries_case_insensitivity_and_mention_order(self):
        known={'cache-service','payments-service','auth-service'}
        self.assertEqual(requested_targets('PAYMENTS-service랑 cache-service 확인해줘',known),['payments-service','cache-service'])
        self.assertEqual(requested_targets('cache-service-old와 payments-service 확인',known),['payments-service'])

    def test_existing_login_and_unknown_service_fallback_policy_is_preserved(self):
        self.assertEqual(requested_targets('로그인 응답이 느려요',{'auth-service'}),['auth-service'])
        self.assertEqual(requested_targets('billing-service 응답이 느려요',{'auth-service'}),['auth-service'])

    def test_unknown_status_zero_values_are_not_successful_evidence(self):
        data={**measurement('cache-service'),'status':'unknown','cpu_percent':0,'memory_percent':0}
        self.assertEqual(coverage([{'source':'monitoring','data':data}],['cache-service']),([],['cache-service']))

    def test_real_zero_metrics_are_valid_but_missing_nan_and_boolean_are_not(self):
        data={**measurement('cache-service'),'cpu_percent':0.0,'memory_percent':0.0}
        self.assertIsNone(monitoring_error(data,'cache-service'))
        for value in [None,float('nan'),True,'50']:
            self.assertIsNotNone(monitoring_error({**data,'cpu_percent':value},'cache-service'))

    def test_new_requests_have_independent_lists_and_clear_work_fields(self):
        history=[{'role':'user','content':'이름은 민수'}]
        a=initial_state('first','thread','a',history);b=initial_state('second','thread','b',history)
        a['tasks'].append({'target':'old'});a['history'][0]['content']='changed'
        self.assertEqual(b['tasks'],[])
        self.assertEqual(b['draft'],'')
        self.assertEqual(b['history'][0]['content'],'이름은 민수')

    def test_session_history_survives_reopening_its_database(self):
        import tempfile
        from pathlib import Path
        from agent.session_store import SqliteSessionStore
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'session.db';store=SqliteSessionStore(path)
            store.append_turn('one','제 이름은 민수입니다','반갑습니다');store.close()
            reopened=SqliteSessionStore(path)
            self.assertEqual(len(reopened.load('one')),2)
            self.assertEqual(reopened.load('other'),[])
            reopened.close()
