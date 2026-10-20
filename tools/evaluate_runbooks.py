"""Measure retrieval separately; calibration labels never enter the searcher."""
import argparse
import csv
import json
import os
import sys
import time
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from dotenv import load_dotenv
from agent.embeddings import OpenAIEmbedding
from agent.runbook_retriever import RunbookRetriever,digest
from agent.graph import _classify_incident_type

parser=argparse.ArgumentParser();parser.add_argument('--calibrate',action='store_true')
parser.add_argument('--rerank',action='store_true');parser.add_argument('--heldout',action='store_true')
args=parser.parse_args();load_dotenv(ROOT/'.env')
os.environ['LANGSMITH_TRACING']='false';os.environ['LANGCHAIN_TRACING_V2']='false'
out=ROOT/'evidence/rag_validation';out.mkdir(parents=True,exist_ok=True)
cases=json.loads((ROOT/'data/retrieval_cases.json').read_text())
searcher=RunbookRetriever(ROOT/'data/runbooks',ROOT/'.runbook_index',OpenAIEmbedding())
prepared=searcher.prepare()
if args.calibrate:
    # Retrieve distances once. Tune on calibration examples only.
    searcher.max_distance=2;searcher.secondary_margin=2
    measured=[(case,searcher.search(case['question'],case.get('monitoring',[]))) for case in cases['calibration']]
    distances=sorted({r['distance'] for _,result in measured for r in result['candidates']})
    thresholds=[0]+[(a+b)/2 for a,b in zip(distances,distances[1:])]+[2]
    best=None
    for threshold in thresholds:
        for margin in [0,.01,.02,.035,.05,.08]:
            searcher.max_distance=threshold;searcher.secondary_margin=margin
            results=[(c,searcher.search(c['question'],c.get('monitoring',[]))) for c,_ in measured]
            exact=sum({r['runbook_id'] for r in result['hits']}==set(c['gold']) for c,result in results)
            wrong=sum(len({r['runbook_id'] for r in result['hits']}-set(c['gold'])) for c,result in results)
            rank=(exact,-wrong,-margin,-threshold)
            if best is None or rank>best[0]:best=(rank,threshold,margin)
    policy={'model':searcher.embedder.model,'max_distance':best[1],'secondary_margin':best[2],
        'corpus_hash':digest(json.dumps({k:v['content_hash'] for k,v in searcher.catalog.items()},sort_keys=True)),
        'calibration_ids':[c['id'] for c in cases['calibration']],
        'calibration_exact_matches':best[0][0],'calibration_count':len(cases['calibration']),
        'note':'개발용 조정 자료에 맞춘 거리 기준이며 정답 확률이나 독립 평가 결과가 아닙니다.'}
    (ROOT/'data/retrieval_policy.json').write_text(json.dumps(policy,ensure_ascii=False,indent=2))
    (out/'calibration_distances.json').write_text(json.dumps(measured,ensure_ascii=False,indent=2))
policy=json.loads((ROOT/'data/retrieval_policy.json').read_text())
searcher.max_distance=policy['max_distance'];searcher.secondary_margin=policy['secondary_margin']
if args.rerank:
    from agent.relevance import OpenAIRelevanceFilter
    searcher.reranker=OpenAIRelevanceFilter(ROOT/'.runbook_index')
    policy={**policy,'selection':'quote_checked_llm_rerank','candidate_count':3,
        'note':'거리 단독 조정 결과는 vector_v1에 보존했습니다. 현재 선택은 상위 3개 후보의 관련성·인용 문구를 검증합니다. 거리 임계값은 현재 선택에 사용하지 않습니다.'}
    (ROOT/'data/retrieval_policy.json').write_text(json.dumps(policy,ensure_ascii=False,indent=2))
results=[]
for case in cases['heldout'] if args.heldout else cases['evaluation']:
    rows=case.get('monitoring') or [{'system_name':'fixture','alerts':[case['question']]}]
    began=time.perf_counter();old=_classify_incident_type([{'source':'monitoring','data':r} for r in rows])
    old_seconds=time.perf_counter()-began
    new=searcher.search(case['question'],case.get('monitoring',[]));ids=[r['runbook_id'] for r in new['hits']]
    gold=set(case['gold']);found=set(ids)
    results.append({'id':case['id'],'question':case['question'],'gold':case['gold'],
        'keyword_ids':[old],'rag_ids':ids,'keyword_exact':{old}==gold,'rag_exact':found==gold,
        'rag_recall':len(found&gold)/len(gold) if gold else None,'irrelevant_hits':len(found-gold),
        'keyword_seconds':old_seconds,'rag_seconds':new['seconds'],'search':new})
payload={'prepared':prepared,'policy':policy,'mode':'real_openai_embeddings_with_local_chroma',
    'note':cases['note'],'results':results,'final_embedding_stats':searcher.stats,
    'summary':{'cases':len(results),'keyword_exact':sum(r['keyword_exact'] for r in results),
        'rag_exact':sum(r['rag_exact'] for r in results),'rag_irrelevant_hits':sum(r['irrelevant_hits'] for r in results)}}
if args.rerank:payload['relevance_usage']=searcher.reranker.stats
stem='retrieval_heldout' if args.heldout else ('retrieval_rerank_regression' if args.rerank else 'retrieval_evaluation')
(out/(stem+'.json')).write_text(json.dumps(payload,ensure_ascii=False,indent=2))
with (out/(stem+'.csv')).open('w',encoding='utf-8-sig',newline='') as file:
    fields=[k for k in results[0] if k!='search'];writer=csv.DictWriter(file,fieldnames=fields)
    writer.writeheader();writer.writerows({k:r[k] for k in fields} for r in results)
print(json.dumps(payload['summary'],ensure_ascii=False));print(json.dumps(policy,ensure_ascii=False))
