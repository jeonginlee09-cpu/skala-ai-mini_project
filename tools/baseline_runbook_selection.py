"""Historical assignment selector, used only for retrieval comparison."""
RULES=[('latency','high-latency'),('pool','high-latency'),('cpu','cpu-spike'),
       ('memory','memory-leak'),('heap','memory-leak'),('timeout','network-timeout'),
       ('network','network-timeout'),('disk','disk-full'),('down','db-down')]


def classify_incident_type(collected):
    text=' '.join(str(row['data'].get('status',''))+' '+' '.join(row['data'].get('alerts',[]))
                  for row in collected if row.get('source')=='monitoring').lower()
    return next((name for keyword,name in RULES if keyword in text),'db-down')
