"""Check measurement accounting, real examples, local links and product preservation."""
import ast
import hashlib
import json
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'evidence/reviewer_comparison_20261021'


class Document(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links, self.headings, self.scripts = [], [], []
        self.tables = 0

    def handle_starttag(self, tag, attrs):
        props = dict(attrs)
        if tag == 'a':
            self.links.append(props.get('href', ''))
        if tag in ('h1', 'h2', 'h3'):
            self.headings.append(tag)
        if tag == 'table':
            self.tables += 1
        if tag == 'script':
            self.scripts.append(props)


def main():
    summary = json.loads((OUT / 'summary.json').read_text())
    manifest = json.loads((OUT / 'manifest.json').read_text())
    rows = json.loads((OUT / 'all_results.json').read_text())
    for name in ('benchmark_reviewer_comparison', 'report_reviewer_comparison',
                 'render_reviewer_walkthrough', 'verify_reviewer_comparison'):
        ast.parse((ROOT / 'tools' / (name + '.py')).read_text())
    checks = []
    for file in (ROOT / 'doc/reviewer_code_walkthrough.html', OUT / 'report.html'):
        parser = Document(); parser.feed(file.read_text())
        broken = []
        for link in parser.links:
            if not link or '://' in link:
                continue
            target = (file.parent / unquote(link.split('#')[0])).resolve()
            if not target.exists():
                broken.append(link)
        assert not broken, (str(file), broken)
        assert parser.tables >= 1 and parser.headings.count('h1') == 1 and not parser.scripts
        checks.append({'file': str(file.relative_to(ROOT)), 'links': len(parser.links),
                       'tables': parser.tables, 'broken_links': []})
    telemetry = [json.loads(x) for x in (OUT / 'telemetry/latest.jsonl').read_text().splitlines()]
    reviews = [t for t in telemetry if t['kind'] == 'provider' and (t.get('messages') or [{}])[0]
               .get('content', '').startswith('당신은 독립적인 내용 검토 Reviewer Agent다.')]
    event_tokens = sum(e['event'].get('tokens', 0) for r in rows if r['variant'] == 'latest'
                       for e in r['events'] if e['event']['type'] == 'ReviewerCompleted')
    assert sum(t['usage']['total_tokens'] for t in reviews) == event_tokens == summary['results']['latest']['reviewer_tokens']
    q06 = next(r for r in rows if r['request_id'] == 'cmp-latest-Q06-r01')
    assert q06['status'] == 'ok' and set(q06['actual_runbooks']) == {'cpu-spike', 'memory-leak'}
    assert any(i['event']['type'] == 'ReviewerCompleted' and i['event']['tokens'] == 2527 for i in q06['events'])
    product_changes = [file for file, digest in manifest['sources']['latest'].items()
        if (file.startswith('agent/') or file in ('server.py', 'request_ui.js', 'web_chat.html', 'data/runbook_rules.json'))
        and hashlib.sha256((ROOT / file).read_bytes()).hexdigest() != digest]
    assert not product_changes, product_changes
    examples = [{k: r.get(k) for k in ('request_id','variant','case_id','round','question','expected_targets',
        'actual_targets','actual_runbooks','total_s','status','answer')} |
        {'reviewer_events': r.get('reviewer_events', [])} for r in rows if r['round'] == 1]
    (ROOT / 'doc/reviewer_comparison_examples.json').write_text(json.dumps({
        'source_commit': summary['latest_commit'], 'sample': 'fixed class data; not live operations',
        'examples': examples}, ensure_ascii=False, indent=2))
    secrets = [v for k,v in dotenv_values(ROOT / '.env').items() if v and len(v)>=8
               and any(w in k.upper() for w in ('API_KEY','SECRET','TOKEN','PASSWORD'))]
    files = list((ROOT / 'doc').glob('reviewer_*')) + list((ROOT / 'tools').glob('*reviewer*comparison*.py'))
    assert not any(secret in p.read_text(errors='replace') for p in files for secret in secrets)
    verification = {'requests': len(rows), 'langsmith_graph_runs': summary['langsmith_graph_runs'],
        'accounting_reconciled': True, 'source_fixture_provider_usage_errors': summary['verification_errors'],
        'document_structure_and_links': checks, 'credentials_detected': False,
        'browser_visual_preview': 'not_performed_file_protocol_blocked',
        'existing_product_tests_reference': {'python':76, 'ui':5, 'path':'evidence/conditional_reviewer_20261021/'},
        'product_source_changed_this_task': False, 'streaming_specific_tests_added': False}
    (OUT / 'delivery_verification.json').write_text(json.dumps(verification, ensure_ascii=False, indent=2))
    print(json.dumps(verification, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
