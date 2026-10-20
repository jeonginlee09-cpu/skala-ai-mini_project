"""Deterministic grounding checks. Missing evidence is never a passed condition."""
import json
from decimal import Decimal,InvalidOperation
from pathlib import Path

from pydantic import ValidationError

from .grounded_report import GroundedReport
from .runbook_retriever import digest

RULES_PATH=Path(__file__).resolve().parents[1]/'data/runbook_rules.json'


def condition_result(condition,target,evidence):
    row=next((e for e in evidence.values() if e['target']==target and e['field']==condition['field']
              and e['origin']=='measurement'),None)
    if row is None:return {'status':'unknown','evidence_id':None,**condition}
    value=row['value'];op=condition['op']
    if op=='is_true':met=value is True
    elif op=='gt':met=value>condition['value']
    elif op=='ge':met=value>=condition['value']
    else:return {'status':'unknown','evidence_id':row['id'],**condition}
    return {'status':'met' if met else 'not_met','evidence_id':row['id'],**condition}


def prepare_actions(runbooks,evidence,rules_path=RULES_PATH):
    rules=json.loads(Path(rules_path).read_text())['runbooks'];actions=[]
    measured={e['target'] for e in evidence.values() if e['origin']=='measurement'}
    for book in runbooks:
        key=book['runbook_id'];rule=rules.get(key)
        if not rule or rule['content_hash']!=book.get('content_hash'):continue
        if digest(book.get('text',book.get('data','')))!=rule['content_hash']:continue
        for target in book.get('targets',[]):
            if target not in measured:continue
            for step in rule['steps']:
                checks=[condition_result(c,target,evidence) for c in step['conditions']]
                statuses={c['status'] for c in checks}
                status='not_met' if 'not_met' in statuses else ('unknown' if 'unknown' in statuses else 'met')
                actions.append({**step,'action_id':f'{target}:{key}:{step["step_id"]}',
                    'target':target,'runbook_id':key,'conditions':checks,'condition_status':status,
                    'missing_conditions':[c['label'] for c in checks if c['status']=='unknown'],
                    'hypotheses':rule['hypotheses'],
                    **{field:book[field] for field in ['title','file','uri','content_hash']}})
    return actions


def _same_value(claim,evidence):
    if isinstance(evidence['value'],(int,float)) and not isinstance(evidence['value'],bool):
        try:return Decimal(claim).is_finite() and Decimal(claim)==Decimal(str(evidence['value']))
        except InvalidOperation:return False
    return claim==str(evidence['value'])


def validate_report(raw,evidence,actions,required_targets):
    errors=[]
    def fail(code,message):errors.append({'code':code,'message':message})
    try:report=GroundedReport.model_validate(raw).model_dump()
    except ValidationError as exc:
        return {'passed':False,'errors':[{'code':'REPORT_SCHEMA_INVALID',
            'message':'구조화 초안 필드·타입·허용 상태를 확인하세요: '+', '.join('.'.join(map(str,e['loc'])) for e in exc.errors())}]}
    seen=set();covered=set()
    for fact in report['facts']:
        key=fact['evidence_id'];row=evidence.get(key)
        if not row:fail('EVIDENCE_NOT_FOUND','존재하지 않는 근거: '+key);continue
        if key in seen:fail('DUPLICATE_FACT','중복 근거: '+key)
        seen.add(key)
        if fact['target']!=row['target'] or fact['target'] not in required_targets:
            fail('FACT_TARGET_MISMATCH','조회 대상과 주장 대상이 다릅니다: '+key)
        if not _same_value(fact['value'],row):fail('FACT_VALUE_MISMATCH','원문 수치·상태와 다른 값: '+key)
        if row['origin']=='measurement':covered.add((row['target'],row['field']))
    for target in required_targets:
        for field in ['status','cpu_percent','memory_percent']:
            if (target,field) not in covered:fail('FACT_COVERAGE_MISSING',target+' '+field+' 근거를 포함하세요.')
    action_map={a['action_id']:a for a in actions};recommended=set()
    for rec in report['recommendations']:
        a=action_map.get(rec['action_id'])
        if not a:fail('ACTION_NOT_FOUND','조회한 런북에 없는 절차 ID: '+rec['action_id']);continue
        recommended.add(a['target'])
        if a['target'] not in required_targets:fail('ACTION_TARGET_MISMATCH','요청 범위 밖 권고입니다.')
        if a['kind']=='check' and rec['mode']!='check':fail('ACTION_MODE_INVALID','점검 절차는 점검 권고로 표시하세요.')
        if a['kind']=='action':
            if rec['mode']=='check':fail('ACTION_MODE_INVALID','조치 절차를 점검으로 우회할 수 없습니다.')
            if rec['mode']=='recommend' and a['condition_status']!='met':
                fail('CONDITION_UNVERIFIED',a['action_id']+' 조건이 미충족 또는 미확인입니다. conditional로 작성하세요.')
    for target in {a['target'] for a in actions}-recommended:
        fail('RECOMMENDATION_MISSING',target+'의 검색된 매뉴얼에서 최소 한 가지 점검/조건부 권고를 선택하세요.')
    for h in report['hypotheses']:
        a=action_map.get(h['action_id'])
        if not a or h['candidate_id'] not in {x['id'] for x in a['hypotheses']}:
            fail('HYPOTHESIS_NOT_SUPPORTED','조회한 런북에 없는 원인 가설입니다.')
    return {'passed':not errors,'errors':errors}
