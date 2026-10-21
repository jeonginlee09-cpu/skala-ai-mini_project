"""Balanced 3-way SSE benchmark; snapshots and historical measurements stay separate."""
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
from datetime import datetime
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'evidence/optimization_20261021'
VARIANTS = ['original', 'improved', 'optimized']
PORTS = dict(zip(VARIANTS, [18230, 18231, 18232]))


def snapshot_source(name, optimized_ref):
    dest = OUT / 'stages' / name
    if dest.exists(): raise RuntimeError('Refusing to overwrite a previous source snapshot')
    dest.mkdir(parents=True)
    if name == 'original':
        original = ROOT / '기존과제자료_백업_20261020'
        for item in original.iterdir():
            if item.name.startswith('.') or item.name.startswith('session.db') or item.name.endswith('.json'):
                continue
            if item.is_dir(): shutil.copytree(item, dest / item.name, ignore=shutil.ignore_patterns('__pycache__'))
            else: shutil.copy2(item, dest / item.name)
    else:
        ref = 'baseline/pre-token-optimization-20261021' if name == 'improved' else optimized_ref
        archive = subprocess.check_output(['git', 'archive', ref], cwd=ROOT)
        with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
            tar.extractall(dest, filter='data')
        shutil.copytree(ROOT / '.runbook_index', dest / '.runbook_index')
    files = {str(p.relative_to(dest)): hashlib.sha256(p.read_bytes()).hexdigest()
             for p in dest.rglob('*') if p.is_file() and '.runbook_index' not in p.parts}
    return dest, files


async def collect(client, variant, case, repeat):
    request_id = f"cmp-{variant}-{case['id']}-r{repeat:02d}"
    thread = f"cmp-{variant}-memory-{repeat}" if case['id'].startswith('Q10') else request_id
    dest = OUT / 'runs' / request_id
    dest.mkdir(parents=True, exist_ok=False)
    row = {'request_id': request_id, 'variant': variant, 'case_id': case['id'], 'round': repeat,
        'question': case['query'], 'expected_targets': case['expected_targets'], 'events': [],
        'started_at': datetime.now().astimezone().isoformat(), 'manual_answer_correct': None}
    started = time.perf_counter()
    raw = []
    try:
        async with client.stream('GET', f'http://127.0.0.1:{PORTS[variant]}/chat/stream',
                params={'message': case['query'], 'thread_id': thread, 'request_id': request_id}) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                raw.append(line)
                if not line.startswith('data:'): continue
                data = line[5:].strip()
                if data == '[DONE]': row['done'] = True; continue
                event = json.loads(data)
                row['events'].append({'elapsed_s': time.perf_counter() - started, 'event': event})
                if event['type'] == 'FinalAnswer': row['answer'] = event['reply']
                if event['type'] in ['ValidationFailed', 'RequestFailed']: row['failure'] = event
    except Exception as exc:
        row['collection_error'] = type(exc).__name__
    row['total_s'] = time.perf_counter() - started
    row['status'] = 'ok' if row.get('done') and row.get('answer') else 'failed'
    events = [item['event'] for item in row['events']]
    row['actual_targets'] = [e['target'] for e in events
        if e['type'] == 'TaskCompleted' and e.get('source') == 'monitoring']
    row['target_set_match'] = set(row['actual_targets']) == set(case['expected_targets'])
    row['validation_attempts'] = sum(e['type'] == 'Validation' for e in events)
    first_check = next((i for i,e in enumerate(events) if e['type'] == 'Validation'), len(events))
    row['draft_exposed'] = bool(case['expected_targets']) and any(e['type'] == 'AnswerToken' for e in events[:first_check])
    row['name_present'] = '민수' in row.get('answer', '') if case['id'] == 'Q10B' else None
    row['actual_runbooks'] = [e['runbook_id'] for e in events if e['type'] == 'RunbookLoaded'] or [
        e['target'] for e in events if e['type'] == 'TaskCompleted' and e.get('source') == 'runbook']
    (dest / 'response.json').write_text(json.dumps(row, ensure_ascii=False, indent=2))
    (dest / 'stream.sse').write_text('\n'.join(raw))
    (dest / 'answer.txt').write_text(row.get('answer', json.dumps(row.get('failure'), ensure_ascii=False)))
    with (OUT / 'results.jsonl').open('a') as stream:
        stream.write(json.dumps(row, ensure_ascii=False) + '\n')
    print(json.dumps({'request': request_id, 'status': row['status'], 'seconds': round(row['total_s'], 3),
        'targets': row['actual_targets'], 'attempts': row['validation_attempts']}, ensure_ascii=False), flush=True)
    return row


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--optimized-ref', default='HEAD')
    parser.add_argument('--rounds', type=int, default=3)
    args = parser.parse_args()
    if (OUT / 'results.jsonl').exists(): raise RuntimeError('Previous run exists; preserve it before starting a new measurement')
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / 'telemetry').mkdir(exist_ok=True)
    cases = json.loads((ROOT / 'evidence/test_cases.json').read_text())
    manifest = {'started_at': datetime.now().astimezone().isoformat(), 'rounds': args.rounds,
        'python': sys.version, 'ports': PORTS, 'optimized_commit': subprocess.check_output(
            ['git', 'rev-parse', args.optimized_ref], cwd=ROOT, text=True).strip(), 'sources': {},
        'conditions': ['Real OpenAI, real FastAPI/SSE and real MCP stdio; sample monitoring only.',
            'All variants use the same monitoring.json with jitter=0 and fixed sample timestamp.',
            'The assignment first-call timeout simulation is disabled equally for all variants.',
            'Improved and optimized start from independent copies of the same existing RAG cache.',
            'Order rotates original/improved/optimized, improved/optimized/original, optimized/original/improved.',
            'No deployment/restart/rollback tool executes. Responses are recommendations.',
            'Historical 2026-10-20 measurements are not mixed into this controlled comparison.']}
    processes, logs = [], []
    try:
        for name in VARIANTS:
            source, files = snapshot_source(name, args.optimized_ref)
            manifest['sources'][name] = files
            log = (OUT / (name + '_server.log')).open('w'); logs.append(log)
            processes.append(subprocess.Popen([sys.executable, '-u', str(ROOT / 'tools/serve_comparison_variant.py'),
                '--source', str(source), '--variant', name, '--port', str(PORTS[name]),
                '--out', str(OUT / 'telemetry')], cwd=source, stdout=log, stderr=subprocess.STDOUT,
                env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'}))
        async with httpx.AsyncClient(trust_env=False, timeout=180) as client:
            for name in VARIANTS:
                for _ in range(80):
                    try:
                        response = await client.get(f'http://127.0.0.1:{PORTS[name]}/health', timeout=2)
                        response.raise_for_status()
                        manifest.setdefault('health', {})[name] = response.json()
                        break
                    except Exception:
                        await asyncio.sleep(.25)
                else: raise RuntimeError('Variant failed startup: ' + name)
            (OUT / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
            results = []
            for repeat in range(1, args.rounds + 1):
                offset = (repeat - 1) % 3
                order = VARIANTS[offset:] + VARIANTS[:offset]
                for case in cases:
                    for variant in order:
                        results.append(await collect(client, variant, case, repeat))
            (OUT / 'all_results.json').write_text(json.dumps(results, ensure_ascii=False, indent=2))
    finally:
        for process in processes: process.terminate()
        for process in processes:
            try: process.wait(timeout=20)
            except subprocess.TimeoutExpired: process.kill(); process.wait()
        for log in logs: log.close()


if __name__ == '__main__': asyncio.run(main())
