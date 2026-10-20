"""Request-scoped evidence. Alert text is never relabelled as a current metric."""
import re

from .target_scope import monitoring_error


ALERT_PATTERNS={
    'p95_seconds':r'p95 latency\s+([\d.]+)s',
    'pool_percent':r'connection pool\s+([\d.]+)%',
    'disk_percent':r'disk usage\s+([\d.]+)%',
}


def build_evidence(collected,request_id):
    latest={}
    for row in collected:
        data=row.get('data')
        if row.get('source')=='monitoring' and isinstance(data,dict):
            name=data.get('system_name')
            if monitoring_error(data,name) is None:latest[name]=data
    evidence={}
    for target,data in latest.items():
        for field in ['status','cpu_percent','memory_percent']:
            key=f'{request_id}:observation:{target}:{field}'
            evidence[key]={'id':key,'target':target,'field':field,'value':data[field],
                'unit':'%' if field.endswith('_percent') else '', 'origin':'measurement',
                'source':'MCP get_monitoring_status (실습 샘플)',
                'observed_at':data.get('last_checked'),'retrieved_at':data.get('last_checked')}
        for i,text in enumerate(data.get('alerts',[])):
            key=f'{request_id}:alert:{target}:{i}'
            evidence[key]={'id':key,'target':target,'field':'alert','value':text,'unit':'',
                'origin':'alert_text','source':'MCP alerts (실습 샘플)',
                'observed_at':None,'retrieved_at':data.get('last_checked')}
            for field,pattern in ALERT_PATTERNS.items():
                match=re.search(pattern,text,re.IGNORECASE)
                if match:
                    derived=key+':'+field
                    evidence[derived]={**evidence[key],'id':derived,'field':field,
                        'value':float(match[1]),'unit':'s' if field=='p95_seconds' else '%',
                        'origin':'alert_extract','parent_id':key}
    return evidence
