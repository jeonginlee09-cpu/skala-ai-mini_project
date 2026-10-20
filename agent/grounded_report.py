"""Typed report selection plus deterministic rendering of trusted evidence/actions."""
from typing import Literal

from pydantic import BaseModel,ConfigDict


class StrictModel(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)


class FactClaim(StrictModel):
    evidence_id:str
    target:str
    value:str


class Recommendation(StrictModel):
    action_id:str
    mode:Literal['check','conditional','recommend']
    execution_status:Literal['proposed']


class Hypothesis(StrictModel):
    action_id:str
    candidate_id:str


class GroundedReport(StrictModel):
    facts:list[FactClaim]
    hypotheses:list[Hypothesis]
    recommendations:list[Recommendation]


def default_report(evidence,actions):
    """Deterministic fixture/backend report, never described as an LLM result."""
    return {'facts':[{'evidence_id':e['id'],'target':e['target'],'value':str(e['value'])}
        for e in evidence.values() if e['origin']=='measurement'], 'hypotheses':[],
        'recommendations':[{'action_id':a['action_id'],
            'mode':'check' if a['kind']=='check' else ('recommend' if a['condition_status']=='met' else 'conditional'),
            'execution_status':'proposed'} for a in actions]}


def render_report(report,evidence,actions,search_status='ok',uncovered_targets=None):
    action_map={a['action_id']:a for a in actions}
    lines=['실습 샘플 자료에 근거한 안내입니다. 실제 조치는 실행하지 않았습니다.','1) 확인한 사실']
    for fact in report['facts']:
        e=evidence[fact['evidence_id']]
        label={'status':'상태','cpu_percent':'CPU','memory_percent':'메모리'}.get(e['field'],e['field'])
        provenance='현재 조회' if e['origin']=='measurement' else '알림 인용·발생 시각 미확인'
        lines.append(f"- {e['target']} {label}: {e['value']}{e['unit']} ({provenance}) [근거 {e['id']}]")
    # Include original alert text separately, even if the model did not select it.
    for e in evidence.values():
        if e['field']=='alert':lines.append(f"- {e['target']} 알림 인용: {e['value']} (발생 시각 미확인) [근거 {e['id']}]")
    lines.append('2) 원인 가설·권고')
    if not report['hypotheses']:lines.append('- 현재 자료만으로 실제 원인은 확정하지 않았습니다.')
    for h in report['hypotheses']:
        a=action_map[h['action_id']];text=next(c['text'] for c in a['hypotheses'] if c['id']==h['candidate_id'])
        lines.append(f"- {a['target']} 점검 가설: {text}. 런북이 제시하는 가능성이며 원인 확인은 필요합니다.")
    unknowns=[];sources={}
    for r in report['recommendations']:
        a=action_map[r['action_id']];sources[a['runbook_id']]=a
        prefix={'check':'점검 권고','recommend':'런북 조건 충족에 따른 조치 검토','conditional':'조건부 권고'}[r['mode']]
        conditions=' / '.join(c['label'] for c in a['conditions'])
        suffix=f" (조건: {conditions}; 판정={a['condition_status']})" if conditions else ''
        lines.append(f"- {a['target']} {prefix}: {a['text']}{suffix} [런북 {a['runbook_id']} 절차 {a['step_id']}]")
        unknowns.extend(a['missing_conditions'])
    lines.append('3) 미확인·근거')
    for text in dict.fromkeys(unknowns):lines.append('- 미확인: '+text)
    if search_status!='ok':lines.append('- 관련 매뉴얼을 확보하지 못했습니다. 상태: '+search_status+'; 임의 조치 안내는 보류합니다.')
    if uncovered_targets:lines.append('- 매뉴얼 근거가 부족한 대상: '+', '.join(uncovered_targets))
    if not unknowns and search_status=='ok':lines.append('- 추가 원인 확인과 조치 후 결과 확인이 필요합니다.')
    for a in sources.values():lines.append(f"- {a['title']}: {a['file']} · {a['uri']} · 해시 {a['content_hash'][:12]}")
    return '\n'.join(lines)
