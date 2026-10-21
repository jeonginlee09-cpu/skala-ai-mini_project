"""Verify benchmark invariants and retrieve only this comparison's remote traces."""
import argparse
import hashlib
import json
import re
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path

from dotenv import dotenv_values, load_dotenv

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT/'evidence/optimization_20261021'


class Links(HTMLParser):
    def __init__(self): super().__init__(); self.links=[]
    def handle_starttag(self,tag,attrs):
        if tag=='a': self.links.extend(v for k,v in attrs if k=='href')


def comparison_trace_query(client, variant):
    return client.list_runs(project_name='capstone-3way-'+variant+'-20261021',
        filter='eq(name, "LangGraph")', limit=100)


def verify(traces=False):
    measured=json.loads((OUT/'all_results.json').read_text())
    summary=json.loads((OUT/'summary.json').read_text())
    secrets=[v for k,v in dotenv_values(ROOT/'.env').items() if v and len(v)>=8
        and any(word in k.upper() for word in ['API_KEY','SECRET','TOKEN','PASSWORD'])]
    credential_files=[]
    for file in OUT.rglob('*'):
        if file.is_file() and file.suffix in ['.json','.jsonl','.csv','.html','.txt','.log','.sse']:
            content=file.read_text(errors='replace')
            if any(secret in content for secret in secrets):credential_files.append(str(file.relative_to(OUT)))
    optimized=[r for r in measured if r['variant']=='optimized' and r['expected_targets']]
    records=[json.loads(line) for line in (OUT/'telemetry/optimized.jsonl').read_text().splitlines()]
    fact_failures=[];condition_failures=[]
    for row in optimized:
        data=[e['result'] for e in records if e['request_id']==row['request_id'] and e['kind']=='monitoring']
        for observation in data:
            target=observation['system_name']
            for field,label in [('status','상태'),('cpu_percent','CPU'),('memory_percent','메모리')]:
                required=f"- {target} {label}: {observation[field]}"
                if required not in row.get('answer',''):fact_failures.append((row['request_id'],field))
        for line in row.get('answer','').splitlines():
            if ('판정=unknown' in line or '판정=not_met' in line) and '조건부 권고:' not in line:
                condition_failures.append(row['request_id'])
    b={(r['case_id'],r['round']):set(r['actual_runbooks']) for r in measured if r['variant']=='improved'}
    c={(r['case_id'],r['round']):set(r['actual_runbooks']) for r in measured if r['variant']=='optimized'}
    retrieval_differences=[str(key) for key in b if b[key]!=c[key]]
    policy_unchanged=(ROOT/'agent/validator.py').read_bytes()==(OUT/'stages/improved/agent/validator.py').read_bytes()
    runtime_differences=[]
    for file in (OUT/'stages/optimized/agent').glob('*.py'):
        if file.read_bytes()!=(ROOT/'agent'/file.name).read_bytes():runtime_differences.append(file.name)
    parser=Links();parser.feed((OUT/'report.html').read_text())
    broken=[link for link in parser.links if not link.startswith(('http:','https:','#')) and not (OUT/link).exists()]
    models={name:set(data['provider_model_names']) for name,data in summary['results'].items()}
    same_models=len({tuple(sorted(values)) for values in models.values()})==1
    result={'verified_at':datetime.now().astimezone().isoformat(),'requests':len(measured),
        'completed':sum(r['status']=='ok' for r in measured),'credential_files':credential_files,
        'optimized_observed_fact_display_errors':fact_failures,'optimized_condition_label_errors':condition_failures,
        'retrieval_set_differences_improved_vs_optimized':retrieval_differences,
        'validator_source_unchanged':policy_unchanged,'runtime_source_differences':runtime_differences,
        'same_provider_model_versions':same_models,'broken_report_links':broken,
        'fixture_errors':summary['fixture_errors'],'source_differences':summary['source_differences'],
        'human_semantic_review':'pending','invoice_measured':False}
    if traces:
        from langsmith import Client
        load_dotenv(ROOT/'.env');client=Client();trace_rows=[];remote_leak=False
        ids={r['request_id'] for r in measured}
        for variant in ['original','improved','optimized']:
            project='capstone-3way-'+variant+'-20261021'
            for run in comparison_trace_query(client,variant):
                inputs=run.inputs or {};meta=(run.extra or {}).get('metadata') or {}
                request_id=inputs.get('request_id') or meta.get('comparison_request_id')
                if request_id not in ids:continue
                raw=json.dumps({'inputs':inputs,'outputs':run.outputs},ensure_ascii=False,default=str)
                remote_leak=remote_leak or any(secret in raw for secret in secrets)
                trace_rows.append({'variant':variant,'request_id':request_id,'run_id':str(run.id),
                    'end_time':run.end_time.isoformat() if run.end_time else None,'error':run.error})
        missing=sorted(ids-{r['request_id'] for r in trace_rows if r['end_time'] and not r['error']})
        trace_result={'runs':trace_rows,'missing_measured_requests':missing,'credential_value_detected':remote_leak}
        (OUT/'langsmith_verified.json').write_text(json.dumps(trace_result,ensure_ascii=False,indent=2))
        result.update(langsmith_graph_runs=len(trace_rows),missing_langsmith_requests=missing,
                      remote_credential_detected=remote_leak)
    result['passed']=len(measured)==99 and result['completed']==99 and policy_unchanged and same_models and not any([
        credential_files,fact_failures,condition_failures,retrieval_differences,runtime_differences,broken,
        summary['fixture_errors'],any(summary['source_differences'].values()),
        result.get('missing_langsmith_requests',[]),result.get('remote_credential_detected',False)])
    (OUT/'verification.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
    print(json.dumps(result,ensure_ascii=False,indent=2))
    return result['passed']


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--traces',action='store_true');args=parser.parse_args()
    raise SystemExit(0 if verify(args.traces) else 1)
