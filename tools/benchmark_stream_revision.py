"""Re-run original / frozen optimized / messages-updates revision, 33 requests each."""
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
from datetime import datetime

import httpx
import benchmark_three_versions as common

ROOT = common.ROOT
OUT = ROOT / 'evidence/stream_revision_20261021'
VARIANTS = ['original', 'optimized', 'revised']
PORTS = dict(zip(VARIANTS, [18240, 18241, 18242]))
OPTIMIZED_REF = '1d903c6aafb92042bfcfe351a814753cdc5bab42'


def snapshot(name, ref):
    if name == 'original':
        return common.snapshot_source(name, ref)
    dest = OUT / 'stages' / name
    dest.mkdir(parents=True, exist_ok=False)
    archive = subprocess.check_output(['git', 'archive', ref], cwd=ROOT)
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        tar.extractall(dest, filter='data')
    shutil.copytree(ROOT / '.runbook_index', dest / '.runbook_index')
    files = {str(p.relative_to(dest)): hashlib.sha256(p.read_bytes()).hexdigest()
             for p in dest.rglob('*') if p.is_file() and '.runbook_index' not in p.parts}
    return dest, files


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--revised-ref', default='HEAD')
    args = parser.parse_args()
    if (OUT/'results.jsonl').exists():
        raise RuntimeError('Existing measurement is preserved; choose a new output before rerunning')
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT/'telemetry').mkdir(exist_ok=True)
    common.OUT, common.PORTS = OUT, PORTS
    refs = {'optimized': OPTIMIZED_REF, 'revised': subprocess.check_output(
        ['git','rev-parse',args.revised_ref],cwd=ROOT,text=True).strip()}
    cases = json.loads((ROOT/'evidence/test_cases.json').read_text())
    manifest = {'started_at':datetime.now().astimezone().isoformat(), 'rounds':3,
        'python':sys.version, 'ports':PORTS, 'refs':refs, 'sources':{},
        'conditions':[
            'Each version runs the same 11 questions three times, 33 requests per version; real API calls.',
            'Fixed class sample, jitter=0, timestamp=2026-10-21T09:00:00+09:00; no real operational actions.',
            'Original artificial first-call timeout disabled equally; independent conversation databases.',
            'Optimized and revised start from independent copies of the same warm RAG cache.',
            'Round order rotates original/optimized/revised, optimized/revised/original, revised/original/optimized.',
            'Core tokens come from provider usage, including the final usage event of streaming responses.',
            'No new streaming-specific tests or timing metrics; existing regression checks and previous comparison metrics only.',
            'Previous measurements are preserved and not substituted for this new run.']}
    processes, logs = [], []
    try:
        for name in VARIANTS:
            source, files = snapshot(name, refs.get(name))
            manifest['sources'][name] = files
            log = (OUT/(name+'_server.log')).open('w'); logs.append(log)
            processes.append(subprocess.Popen([sys.executable,'-u',str(ROOT/'tools/serve_comparison_variant.py'),
                '--source',str(source),'--variant',name,'--port',str(PORTS[name]),
                '--out',str(OUT/'telemetry'),'--trace-project-prefix','capstone-stream3way'],
                cwd=source,stdout=log,stderr=subprocess.STDOUT,
                env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1'}))
        async with httpx.AsyncClient(trust_env=False,timeout=180) as client:
            for name in VARIANTS:
                for _ in range(100):
                    try:
                        response = await client.get(f'http://127.0.0.1:{PORTS[name]}/health',timeout=2)
                        response.raise_for_status()
                        manifest.setdefault('health',{})[name] = response.json()
                        break
                    except Exception:
                        await asyncio.sleep(.25)
                else: raise RuntimeError('Startup failed: '+name)
            (OUT/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2))
            results=[]
            for repeat in range(1,4):
                offset=repeat-1
                for case in cases:
                    for name in VARIANTS[offset:]+VARIANTS[:offset]:
                        row=await common.collect(client,name,case,repeat)
                        results.append(row)
                        if row['status']!='ok':
                            raise RuntimeError('Request failed; preserve this run and diagnose '+row['request_id'])
            (OUT/'all_results.json').write_text(json.dumps(results,ensure_ascii=False,indent=2))
    finally:
        for process in processes: process.terminate()
        for process in processes:
            try: process.wait(timeout=20)
            except subprocess.TimeoutExpired: process.kill(); process.wait()
        for log in logs: log.close()


if __name__=='__main__': asyncio.run(main())
