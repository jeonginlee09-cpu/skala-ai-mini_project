"""Chroma integration with controlled vectors; never presented as model accuracy."""
import shutil
import tempfile
import unittest
from pathlib import Path

from agent.runbook_retriever import RunbookRetriever

SOURCE=Path(__file__).resolve().parents[1]/'data/runbooks'


class ControlledEmbedding:
    model='controlled-six-dimensional-test'
    def __init__(self):self.calls=[]
    def embed(self,texts):
        self.calls.append(list(texts));vectors=[]
        for text in texts:
            vector=[0.0]*7
            if text.startswith('# Runbook:'):
                titles=['CPU 급증','DB Down','디스크 용량','응답 지연','메모리 누수','네트워크 타임아웃']
                vector[next(i for i,t in enumerate(titles) if t in text)]=1.0
            elif 'synthetic-cpu' in text:vector[0]=1.0
            elif 'synthetic-memory' in text:vector[4]=1.0
            else:vector[6]=1.0
            vectors.append(vector)
        return vectors,{'tokens':0,'requests':1,'mode':'controlled_test'}


class RetrievalTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        shutil.copytree(SOURCE,self.root/'runbooks');self.embed=ControlledEmbedding()
        self.retriever=RunbookRetriever(self.root/'runbooks',self.root/'index',self.embed,
                                       max_distance=.2,secondary_margin=.02)
        self.retriever.prepare()
    def tearDown(self):self.temp.cleanup()

    def test_compound_request_retrieves_two_distinct_runbooks(self):
        result=self.retriever.search('synthetic-cpu',[{'system_name':'payments-service','alerts':['synthetic-cpu']},
            {'system_name':'cache-service','alerts':['synthetic-memory']}])
        self.assertEqual(result['status'],'ok')
        self.assertEqual({r['runbook_id'] for r in result['hits']},{'cpu-spike','memory-leak'})
        self.assertTrue(all(r['uri'].startswith('runbook://') and r['content_hash'] for r in result['hits']))

    def test_unrelated_query_has_no_fallback_document(self):
        result=self.retriever.search('synthetic-unrelated',[])
        self.assertEqual(result['status'],'not_found');self.assertEqual(result['hits'],[])

    def test_missing_catalog_and_embedding_failure_have_distinct_status(self):
        empty=RunbookRetriever(self.root/'absent',self.root/'empty',self.embed)
        self.assertEqual(empty.search('anything',[])['status'],'catalog_unavailable')
        self.embed.embed=lambda texts:(_ for _ in ()).throw(ValueError('controlled failure'))
        self.assertEqual(self.retriever.search('not-cached',[])['status'],'search_error')

    def test_unchanged_documents_and_repeated_query_reuse_embeddings(self):
        self.retriever.search('synthetic-cpu',[]);calls=len(self.embed.calls)
        reopened=RunbookRetriever(self.root/'runbooks',self.root/'index',self.embed,
                                  max_distance=.2,secondary_margin=.02)
        reopened.prepare();reopened.search('synthetic-cpu',[])
        self.assertEqual(len(self.embed.calls),calls)

    def test_document_change_gets_new_hash_and_only_changed_embedding(self):
        file=self.root/'runbooks/cpu-spike.md';old=self.retriever.catalog['cpu-spike']['content_hash']
        file.write_text(file.read_text()+'\n설명 추가\n')
        self.retriever.prepare()
        self.assertNotEqual(old,self.retriever.catalog['cpu-spike']['content_hash'])
        self.assertEqual(len(self.embed.calls[-1]),1)

    def test_cap_is_enforced_even_when_caller_requests_more(self):
        result=self.retriever.search('synthetic-cpu',[],max_results=100)
        self.assertLessEqual(len(result['hits']),2)


if __name__=='__main__':unittest.main()
