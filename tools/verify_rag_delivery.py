"""Verify original preservation and exported evidence; package no local secrets/state."""
import hashlib
import json
import os
import re
import subprocess
import zipfile
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote,urlparse

from dotenv import dotenv_values

ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'evidence/rag_validation'
original=json.loads((ROOT/'evidence/baseline_manifest.json').read_text())
source=Path(original['original_root'])
changed=[r['path'] for r in original['files'] if hashlib.sha256((source/r['path']).read_bytes()).hexdigest()!=r['sha256']]
secrets=[v for k,v in dotenv_values(ROOT/'.env').items() if v and len(v)>=8
         and any(x in k.upper() for x in ['API_KEY','TOKEN','PASSWORD','SECRET'])]
excluded={'.git','.venv','.venv-rag','.runbook_index','__pycache__','.pytest_cache'}
files=[];leaks=[]
for directory,dirs,names in os.walk(ROOT,followlinks=False):
    dirs[:]=[d for d in dirs if d not in excluded and not (Path(directory)/d).is_symlink()]
    for name in names:
        file=Path(directory)/name
        if file.is_symlink() or name.startswith('session.db') or file.suffix in {'.pyc','.zip'}:continue
        if name.startswith('.env') and name!='.env.example':continue
        if name in {'.DS_Store','package_verification.json'}:continue
        files.append(file)
        if file.suffix!='.png' and any(v.encode() in file.read_bytes() for v in secrets):leaks.append(str(file.relative_to(ROOT)))

class Links(HTMLParser):
    def __init__(self):super().__init__();self.links=[]
    def handle_starttag(self,tag,attrs):self.links.extend(v for k,v in attrs if k in ['href','src'] and v)
parser=Links();parser.feed((OUT/'report.html').read_text());broken=[]
for link in parser.links:
    parsed=urlparse(link)
    path=OUT/unquote(parsed.path)
    if not parsed.scheme and parsed.path and path!=OUT/'verification.json' and not path.exists():broken.append(link)
runs=json.loads((OUT/'all_results.json').read_text());traces=json.loads((OUT/'langsmith_verified.json').read_text())
python_log=(OUT/'all_python_final.log').read_text();node_log=(OUT/'all_ui_final.log').read_text()
python_ok=bool(re.search(r'Ran 46 tests.*\n\nOK',python_log,re.S))
node_ok='pass 4' in node_log and 'fail 0' in node_log
questions={r['question'] for r in json.loads((ROOT/'evidence/before_snapshot/all_results.json').read_text())}
bad_order=[]
for row in runs:
    if not row['expected_targets']:continue
    events=[r['event'] for r in row['events']]
    last_validation=max((i for i,e in enumerate(events) if e['type']=='Validation' and e['verdict']=='합격'),default=-1)
    final=next((i for i,e in enumerate(events) if e['type']=='FinalAnswer'),-1)
    if last_validation<0 or final<=last_validation or row['draft_token_exposed']:bad_order.append(row['request_id'])
tracked=subprocess.check_output(['git','ls-files','-z'],cwd=ROOT,text=True).strip('\0').split('\0')
(OUT/'source_manifest.json').write_text(json.dumps({n:hashlib.sha256((ROOT/n).read_bytes()).hexdigest() for n in tracked},ensure_ascii=False,indent=2))
verification={'checked_at':datetime.now().astimezone().isoformat(),
    'original_files_checked':len(original['files']),'original_changed':changed,
    'credential_matches':leaks,'remote_credential_match':traces['credential_value_detected'],
    'broken_report_links':broken,'python_46_passed':python_ok,'node_4_passed':node_ok,
    'actual_requests':len(runs),'all_requests_completed':all(r['status']=='ok' for r in runs),
    'same_baseline_questions':all(r['question'] in questions for r in runs),
    'request_ids_match':all(r['event_ids_match'] for r in runs),'invalid_final_event_order':bad_order,
    'missing_langsmith_runs':traces['missing_measured_requests'],
    'tested_application_commit':subprocess.check_output(['git','rev-parse','b58c688'],cwd=ROOT,text=True).strip(),
    'delivery_commit':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
    'human_semantic_review':'pending','stored_new_screenshots':'not_acquired'}
verification['passed']=not any([changed,leaks,broken,bad_order,traces['credential_value_detected'],
    traces['missing_measured_requests']]) and python_ok and node_ok and len(runs)==35 and verification['all_requests_completed'] and verification['same_baseline_questions'] and verification['request_ids_match']
(OUT/'verification.json').write_text(json.dumps(verification,ensure_ascii=False,indent=2))
print(json.dumps(verification,ensure_ascii=False,indent=2))
if not verification['passed']:raise SystemExit(1)
destination=ROOT.parent/'AI_Agent_RAG_근거검수_20261020.zip'
files.extend([OUT/'verification.json',OUT/'source_manifest.json'])
with zipfile.ZipFile(destination,'w',zipfile.ZIP_DEFLATED) as archive:
    for file in sorted(set(files)):archive.write(file,str(Path(ROOT.name)/file.relative_to(ROOT)))
with zipfile.ZipFile(destination) as archive:
    assert archive.testzip() is None
    assert not any(set(Path(n).parts)&excluded for n in archive.namelist())
    assert not any(Path(n).name=='.env' or Path(n).name.startswith('session.db') for n in archive.namelist())
package={'path':str(destination),'bytes':destination.stat().st_size,'files':len(set(files)),
         'crc_ok':True,'local_keys_environments_indexes_sessions_excluded':True}
(OUT/'package_verification.json').write_text(json.dumps(package,ensure_ascii=False,indent=2))
print(json.dumps(package,ensure_ascii=False,indent=2))
