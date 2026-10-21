"""Resolve supported service references from user text; never carry old measurements."""
import re


def named_targets(text, known_systems):
    matches=[]
    for name in known_systems:
        match=re.search(r'(?<![A-Za-z0-9_-])'+re.escape(name)+r'(?![A-Za-z0-9_-])',text,re.I)
        if match:matches.append((match.start(),name))
    return [name for _,name in sorted(matches)]


def resolve_followup(question, history, known_systems):
    explicit=named_targets(question,known_systems)
    result={'effective_query':question,'targets':explicit,'needs_clarification':False,
            'clarification':'','status':'explicit' if explicit else 'unchanged'}
    # An explicit new name, including an unsupported one, must not inherit an old target.
    system_name=r'\b[A-Za-z][A-Za-z0-9_-]*-(?:service|\d+)\b'
    if explicit or re.search(system_name,question,re.I):return result
    reference=r'(?:그|해당|이전|방금|아까)\s*(?:(?:두|세|모든)\s*)?(?:서비스|시스템)(?:들)?|둘\s*다|두\s*(?:서비스|시스템)'
    operation=r'확인|점검|분석|비교|조회|상태|cpu|메모리|디스크|장애|응답|지연|타임아웃|로그|모니터'
    if not re.search(reference,question) or not re.search(operation,question,re.I):return result
    targets=[]
    for message in reversed(history[-10:]):
        if message.get('role')!='user':continue
        content=message.get('content','')
        targets=named_targets(content,known_systems)
        if targets or re.search(system_name,content,re.I):break
    plural=bool(re.search(r'서비스들|시스템들|두\s*(?:서비스|시스템)|둘\s*다|모두',question))
    if not targets or (len(targets)>1 and not plural):
        choices=' / '.join(targets) if targets else '예: payments-service'
        return {**result,'needs_clarification':True,'status':'ambiguous',
                'clarification':'어느 서비스를 확인할까요? 대상 서비스명을 알려주세요. ('+choices+')'}
    return {**result,'targets':targets,'status':'resolved',
            'effective_query':'대상 서비스: '+', '.join(targets)+'\n현재 요청: '+question}
