"""Run the preserved baseline questions against the improved SSE endpoint."""
import argparse
import asyncio
import json
import platform
import sys
import time
from datetime import datetime
from importlib.metadata import version
from pathlib import Path

import httpx

ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'evidence/rag_validation'
BASE='http://127.0.0.1:18120'


async def collect(client,case,repeat,thread=None,suffix=''):
    rid=f"rag-{case['id']}-r{repeat:02d}{suffix}"
    thread=thread or 'rag-20261020-'+rid
    dest=OUT/'runs'/rid;dest.mkdir(parents=True,exist_ok=True)
    row={'request_id':rid,'thread_id':thread,'case_id':case['id'],'round':repeat,
         'question':case['query'],'expected_targets':case['expected_targets'],
         'started_at':datetime.now().astimezone().isoformat(),'events':[],'manual_answer_correct':None}
    began=time.perf_counter();raw=[]
    async with client.stream('GET',BASE+'/chat/stream',params={
            'message':case['query'],'thread_id':thread,'request_id':rid}) as response:
        response.raise_for_status()
        async for line in response.aiter_lines():
            raw.append(line)
            if not line.startswith('data:'):continue
            value=line[5:].strip()
            if value=='[DONE]':row['done']=True;continue
            event=json.loads(value);elapsed=time.perf_counter()-began
            row['events'].append({'elapsed_s':elapsed,'event':event})
            row.setdefault('first_event_s',elapsed)
            if event['type']=='AnswerToken':row.setdefault('first_token_s',elapsed)
            if event['type']=='FinalAnswer':row.update(answer=event['reply'],final_answer_s=elapsed)
            if event['type']=='ValidationFailed':row.update(failure_reply=event['reply'])
    row['total_s']=time.perf_counter()-began;row['ended_at']=datetime.now().astimezone().isoformat()
    events=[r['event'] for r in row['events']]
    row['status']='ok' if row.get('done') and row.get('answer') else ('validation_failed' if row.get('failure_reply') else 'incomplete')
    row['actual_targets']=[e['target'] for e in events if e['type']=='TaskCompleted' and e['source']=='monitoring']
    row['actual_runbooks']=[e['runbook_id'] for e in events if e['type']=='RunbookLoaded']
    row['target_set_match']=set(row['actual_targets'])==set(case['expected_targets'])
    row['event_ids_match']=all(e['request_id']==rid for e in events)
    row['draft_token_exposed']=any(e['type']=='AnswerToken' for e in events) and bool(case['expected_targets'])
    row['writer_route_plan_analysis_tokens']=sum(e.get('tokens') or 0 for e in events)
    row['embedding_usage']={k:sum(e.get('usage',{}).get(k,0) for e in events if e['type']=='RunbookSearch')
                            for k in ['query_tokens','embedding_requests','cache_hits']}
    row['relevance_usage']={k:sum(e.get('relevance_usage',{}).get(k,0) for e in events if e['type']=='RunbookSearch')
                            for k in ['tokens','requests','cache_hits']}
    row['validation_attempts']=len([e for e in events if e['type']=='Validation'])
    row['name_present']='민수' in row.get('answer','') if case['id'].startswith('Q10B') else None
    (dest/'response.json').write_text(json.dumps(row,ensure_ascii=False,indent=2))
    (dest/'stream.sse').write_text('\n'.join(raw)+'\n')
    (dest/'answer.txt').write_text(row.get('answer',row.get('failure_reply','응답 미확보')))
    print(json.dumps({k:row[k] for k in ['request_id','status','total_s','actual_targets','actual_runbooks','validation_attempts']},ensure_ascii=False),flush=True)
    return row


async def main():
    parser=argparse.ArgumentParser();parser.add_argument('--rounds',type=int,default=3);args=parser.parse_args()
    OUT.mkdir(parents=True,exist_ok=True)
    cases=json.loads((ROOT/'evidence/test_cases.json').read_text())
    async with httpx.AsyncClient(trust_env=False,timeout=180) as client:
        health=(await client.get(BASE+'/health')).json()
        environment={'captured_at':datetime.now().astimezone().isoformat(),'python':sys.version,
            'platform':platform.platform(),'health':health,'packages':{k:version(k) for k in
            ['fastapi','uvicorn','langgraph','langchain-core','langsmith','fastmcp','mcp','httpx','pydantic','chromadb']},
            'notes':['SSE final latency, not model TTFT. Sample monitoring values have random jitter.',
                     'Tokens are API usage where available; no invoice or fabricated quality judgement.',
                     'Existing Q08 auth-service fallback policy is preserved.']}
        (OUT/'environment.json').write_text(json.dumps(environment,ensure_ascii=False,indent=2))
        results=[]
        for repeat in range(1,args.rounds+1):
            for case in cases:
                thread=f'rag-20261020-memory-{repeat}' if case['id'].startswith('Q10') else None
                results.append(await collect(client,case,repeat,thread))
        for case in cases[-2:]:
            results.append(await collect(client,case,3,'rag-20261020-rag-Q08-r03',suffix='-context'))
        (OUT/'all_results.json').write_text(json.dumps(results,ensure_ascii=False,indent=2))


if __name__=='__main__':asyncio.run(main())
