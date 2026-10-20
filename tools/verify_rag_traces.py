"""Read back this experiment's traces and export only allowlisted fields."""
import json
from datetime import datetime
from pathlib import Path

from dotenv import dotenv_values,load_dotenv
from langsmith import Client

ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'evidence/rag_validation'
load_dotenv(ROOT/'.env')
secrets=[value for key,value in dotenv_values(ROOT/'.env').items() if value and len(value)>=8
         and any(part in key.upper() for part in ['API_KEY','SECRET','TOKEN','PASSWORD'])]
measured=json.loads((OUT/'all_results.json').read_text());ids={r['request_id'] for r in measured}
ids.update(['rag-smoke-compound','744736b8-69ac-484c-a26b-2e19a221e46c'])
rows=[];leaks=False
for run in Client().list_runs(project_name='capstone-rag-grounding-openai',filter='eq(name, "LangGraph")',limit=100):
    inputs=run.inputs or {};rid=inputs.get('request_id')
    if rid not in ids:continue
    serialized=json.dumps({'inputs':inputs,'outputs':run.outputs},ensure_ascii=False,default=str)
    if any(value in serialized for value in secrets):leaks=True
    rows.append({'run_id':str(run.id),'request_id':rid,'thread_id':inputs.get('thread_id'),
        'query':inputs.get('query'),'start_time':run.start_time.isoformat() if run.start_time else None,
        'end_time':run.end_time.isoformat() if run.end_time else None,'error':run.error,
        'response_status':(run.outputs or {}).get('response_status'),
        'validation_result':(run.outputs or {}).get('validation_result')})
unmatched=[r['request_id'] for r in measured if not any(x['request_id']==r['request_id'] and x['end_time'] and not x['error'] for x in rows)]
result={'verified_at':datetime.now().astimezone().isoformat(),'project':'capstone-rag-grounding-openai',
    'graph_runs':len(rows),'completed':sum(bool(r['end_time']) and not r['error'] for r in rows),
    'credential_value_detected':leaks,'missing_measured_requests':unmatched,'runs':rows}
(OUT/'langsmith_verified.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
print(json.dumps({k:v for k,v in result.items() if k!='runs'},ensure_ascii=False,indent=2))
raise SystemExit(1 if leaks or unmatched else 0)
