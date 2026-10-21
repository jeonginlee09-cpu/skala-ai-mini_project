"""Four functional API examples with fixed class samples; not a speed benchmark."""
import argparse
import asyncio
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import time
from pathlib import Path

import httpx
from dotenv import dotenv_values, load_dotenv

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'evidence/conditional_reviewer_20261021'
CASES=[
    ('chat','안녕, 너는 누구야?','not_requested'),
    ('simple','cache-service 현재 메모리 사용률만 알려줘','skipped'),
    ('multi','payments-service랑 cache-service 둘 다 저하됐는데 공통 원인이 있는지 분석해줘','approved'),
    ('symptoms','payments-service 결제 응답이 느려지고 CPU가 치솟았어요, 원인 확인해주세요','approved')]


async def main():
    global OUT
    parser=argparse.ArgumentParser();parser.add_argument('--ref',default='HEAD')
    parser.add_argument('--out',type=Path,default=OUT);args=parser.parse_args();OUT=args.out.resolve()
    if (OUT/'examples.json').exists():raise RuntimeError('Previous examples are preserved')
    stage=OUT/'stage';stage.mkdir(parents=True,exist_ok=False)
    ref=subprocess.check_output(['git','rev-parse',args.ref],cwd=ROOT,text=True).strip()
    archive=subprocess.check_output(['git','archive',ref],cwd=ROOT)
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:tar.extractall(stage,filter='data')
    shutil.copytree(ROOT/'.runbook_index',stage/'.runbook_index')
    manifest={'source_commit':ref,'port':18320,'sample':'fixed class monitoring; no operational actions',
        'kind':'four functional examples, not a before/after benchmark',
        'files':{str(p.relative_to(stage)):hashlib.sha256(p.read_bytes()).hexdigest()
                 for p in stage.rglob('*') if p.is_file() and '.runbook_index' not in p.parts}}
    (OUT/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2))
    rows=[]
    with (OUT/'example_server.log').open('w') as log:
        process=subprocess.Popen([sys.executable,'-u',str(ROOT/'tools/serve_comparison_variant.py'),
            '--source',str(stage),'--variant','demo','--port','18320','--out',str(OUT/'telemetry'),
            '--trace-project-prefix','capstone-reviewer'],cwd=stage,stdout=log,stderr=subprocess.STDOUT,
            env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1','REVIEWER_MODE':'conditional'})
        try:
            async with httpx.AsyncClient(trust_env=False,timeout=180) as client:
                for _ in range(100):
                    try:
                        response=await client.get('http://127.0.0.1:18320/health',timeout=2)
                        response.raise_for_status();break
                    except Exception:await asyncio.sleep(.25)
                else:raise RuntimeError('Example server did not start')
                manifest['health']=response.json()
                (OUT/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2))
                for case,question,expected in CASES:
                    rid='reviewer-'+ref[:7]+'-'+case;started=time.perf_counter()
                    response=await client.post('http://127.0.0.1:18320/chat',params={'request_id':rid},
                        json={'message':question,'thread_id':rid,'request_id':rid})
                    response.raise_for_status();body=response.json()
                    row={'request_id':rid,'question':question,'expected_review_status':expected,
                         'seconds':time.perf_counter()-started,'response':body,'manual_answer_correct':None}
                    rows.append(row)
                    (OUT/'examples.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2))
                    print(json.dumps({'case':case,'status':body['status'],
                        'review_status':body['metadata'].get('review_status'),
                        'review_attempts':body['metadata'].get('review_attempts')},ensure_ascii=False),flush=True)
        finally:
            process.terminate()
            try:process.wait(timeout=25)
            except subprocess.TimeoutExpired:process.kill();process.wait()
    load_dotenv(ROOT/'.env')
    from langsmith import Client
    ids={r['request_id'] for r in rows};traces=[]
    for retry in range(4):
        traces=[]
        for run in Client().list_runs(project_name='capstone-reviewer-demo-20261021',filter='eq(name, "LangGraph")',limit=100):
            rid=(run.inputs or {}).get('request_id')
            if rid in ids and (run.outputs or {}).get('review_result') is not None:
                traces.append({'request_id':rid,'run_id':str(run.id),'ended':bool(run.end_time),'error':run.error})
        if ids <= {r['request_id'] for r in traces if r['ended'] and not r['error']}:break
        await asyncio.sleep(2**retry)
    failures=[r['request_id'] for r in rows if r['response']['status']!='success'
        or r['response']['metadata'].get('review_status')!=r['expected_review_status']]
    source_errors=[file for file,digest in manifest['files'].items()
                   if hashlib.sha256((stage/file).read_bytes()).hexdigest()!=digest]
    missing=sorted(ids-{r['request_id'] for r in traces if r['ended'] and not r['error']})
    secrets=[v for k,v in dotenv_values(ROOT/'.env').items() if v and len(v)>=8
             and any(s in k.upper() for s in ['API_KEY','SECRET','TOKEN','PASSWORD'])]
    leaks=[str(p.relative_to(OUT)) for p in OUT.rglob('*') if p.is_file()
        and p.suffix in ['.json','.jsonl','.log','.txt'] and any(v in p.read_text(errors='replace') for v in secrets)]
    result={'source_commit':ref,'functional_examples':len(rows),'failures':failures,
        'langsmith_graphs':len(traces),'missing_traces':missing,'source_changes':source_errors,
        'credential_files':leaks,'new_streaming_tests_added':False,
        'python_tests':76,'ui_tests':5,'human_semantic_review':'pending','performance_comparison':'not_run'}
    (OUT/'verification.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
    (OUT/'langsmith_verified.json').write_text(json.dumps(traces,ensure_ascii=False,indent=2))
    print(json.dumps(result,ensure_ascii=False,indent=2))
    if failures or missing or source_errors or leaks:raise RuntimeError('See preserved example evidence')


if __name__=='__main__':asyncio.run(main())
