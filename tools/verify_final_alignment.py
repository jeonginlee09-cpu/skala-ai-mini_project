"""Check the final source, test receipts, report links and editable slide evidence."""
import ast
import hashlib
import json
import re
import subprocess
import zipfile
from html.parser import HTMLParser
from pathlib import Path

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'evidence/final_alignment_verified_20261021'


def main():
    summary = json.loads((OUT / 'summary.json').read_text())
    manifest = json.loads((OUT / 'manifest.json').read_text())
    assert summary['requests'] == 66 and not summary['failed_requests']
    assert summary['features_passed'] == summary['features_total'] == 10
    assert summary['langsmith_graph_runs'] == 84 and not summary['missing_langsmith_requests']
    assert not summary['remote_secret_detected'] and not any(summary['verification_errors'].values())
    names = subprocess.check_output(['git', 'ls-files', '--cached', '--others', '--exclude-standard'], cwd=ROOT, text=True).splitlines()
    names = [name for name in names if (ROOT / name).is_file()]
    for name in names:
        if name.endswith('.py'):
            ast.parse((ROOT / name).read_text(), filename=name)
        elif name.endswith('.json'):
            json.loads((ROOT / name).read_text())
        elif name.endswith('.sh'):
            subprocess.run(['bash', '-n', str(ROOT / name)], check=True)
    product_files = [name for name in manifest['sources']['latest'] if name.startswith('agent/') or name in
        {'server.py', 'mcp_server.py', 'mcp_server_docs.py', 'request_ui.js', 'web_chat.html', 'web_demo_server.py',
         'requirements.txt', 'requirements-rag.lock.txt', 'data/monitoring.json', 'data/runbook_rules.json'}
        or name.startswith('data/runbooks/')]
    changed_runtime = sorted(name for name in product_files
                             if hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
                             != manifest['sources']['latest'][name])
    # The paired benchmark remains bound to its recorded commit. The later UI
    # change has separate source hashes and receipts; it is not a new benchmark.
    assert changed_runtime == ['request_ui.js', 'web_chat.html'], changed_runtime
    submission = json.loads((ROOT / 'doc/evidence/submission_validation.json').read_text())
    readability = submission['ui_readability']
    assert readability['measured_product_commit'] == manifest['latest_commit']
    assert sorted(readability['source_sha256']) == changed_runtime
    for name, digest in readability['source_sha256'].items():
        assert hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == digest
    for name, digest in readability['receipt_sha256'].items():
        assert hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == digest
    python_log = (OUT / 'green_tests.log').read_text()
    ui_log = (OUT / 'green_ui.log').read_text()
    assert re.search(r'Ran 90 tests', python_log) and python_log.rstrip().endswith('OK')
    assert re.search(r'(?m)^[#ℹ] pass 5$', ui_log) and re.search(r'(?m)^[#ℹ] fail 0$', ui_log)
    current_python = (ROOT / readability['python_test_log']).read_text()
    current_ui = (ROOT / readability['ui_test_log']).read_text()
    assert re.search(r'Ran 90 tests', current_python) and current_python.rstrip().endswith('OK')
    assert re.search(r'(?m)^[#ℹ] pass 11$', current_ui) and re.search(r'(?m)^[#ℹ] fail 0$', current_ui)
    live_demo = json.loads((ROOT / readability['live_demo_path']).read_text())
    assert live_demo['full_detail_expansion_checked'] and live_demo['full_evidence_id_in_detail']
    assert not live_demo['full_evidence_id_in_compact']
    assert live_demo['sources_visible'] and live_demo['disclaimer_visible']
    assert live_demo['console_errors'] == live_demo['console_warnings'] == 0
    checked_docs = ['README.md', 'doc/final_code_guide.md', 'doc/final_comparison.md',
                    'doc/report.md', 'doc/README.md', 'doc/presentation_script.md', 'doc/original_final_feature_audit.md']
    broken = []
    for name in checked_docs:
        document = ROOT / name
        for link in re.findall(r'\]\(([^)]+)\)', document.read_text()):
            if '://' in link or link.startswith('#'):
                continue
            if not (document.parent / link.split('#')[0]).resolve().exists():
                broken.append(name + ':' + link)
    assert not broken, broken
    for name in ['evidence/final_alignment_verified_20261021/report.html']:
        parser = HTMLParser(); parser.feed((ROOT / name).read_text()); parser.close()
    deck = ROOT / 'output/AI_Agent_개선_최종발표.pptx'
    receipt = json.loads((ROOT / 'evidence/submission_final_refresh_20261021/validation.json').read_text())
    assert hashlib.sha256(deck.read_bytes()).hexdigest() == receipt['finalSha256']
    assert receipt['packageIntegrity']['status'] == 'pass'
    assert receipt['presentationLayout']['finding_count'] == 0
    assert receipt['firstPartyImport']['passed'] and receipt['nativeTableArithmetic']['native_table_count'] == 1
    pdf = ROOT / 'output/pdf/AI_Agent_개선보고서_3반_G092_이정인.pdf'
    assert hashlib.sha256(pdf.read_bytes()).hexdigest() == submission['pdf_sha256']
    assert submission['pdf_pages'] == 9 and submission['all_pdf_pages_visually_reviewed']
    screenshots = json.loads((ROOT / 'doc/evidence/screenshot_manifest.json').read_text())
    for capture in screenshots['screenshots']:
        image = ROOT / 'doc/evidence' / capture['file']
        assert hashlib.sha256(image.read_bytes()).hexdigest() == capture['sha256']
    assert screenshots['before_after_same_input'] and screenshots['before_after_sample_memory_percent'] == 89.0
    with zipfile.ZipFile(deck) as archive:
        slide_names = [name for name in archive.namelist() if re.fullmatch(r'ppt/slides/slide\d+\.xml', name)]
        note_names = [name for name in archive.namelist() if re.fullmatch(r'ppt/notesSlides/notesSlide\d+\.xml', name)]
        assert len(slide_names) == len(note_names) == 9
        visible_text = '\n'.join(archive.read(name).decode() for name in slide_names + note_names)
        assert '화면 11개' in visible_text and '현재 p95' in visible_text
        assert not any(word in visible_text for word in ['직전', '중간 버전', '최대 두 문서', '두 개 제한'])
        table = archive.read('ppt/slides/slide4.xml').decode()
        for name in ['original', 'latest']:
            row = summary['results'][name]
            assert f"{row['core_tokens_all']:,}" in table and f"{row['mean_s']:.3f}" in table
    secrets = [v for k, v in dotenv_values(ROOT / '.env').items() if v and len(v) >= 8
               and any(word in k.upper() for word in ('API_KEY', 'SECRET', 'TOKEN', 'PASSWORD'))]
    leaked = []
    for name in names:
        file = ROOT / name
        if name.endswith(('.py', '.md', '.html', '.json', '.csv', '.js', '.cjs', '.mjs', '.sh', '.txt')):
            if any(secret in file.read_text(errors='replace') for secret in secrets):
                leaked.append(name)
    assert not leaked, 'Credential detected in submission source'
    assert not any(name == '.env' or name.startswith(('evidence/', '.runbook_index/', '.venv-rag/'))
                   or name.startswith('session.db') for name in names)
    audit = json.loads((ROOT / 'doc/evidence/original_final_feature_audit.json').read_text())
    assert audit['comparison_scope'] == ['기존 과제', '최종 개선본'] and len(audit['features']) == 19
    assert audit['final_product_commit'] == manifest['latest_commit']
    for feature in audit['features']:
        for key in ['original_evidence', 'final_evidence']:
            for evidence in feature[key]:
                commit = audit['original_commit'] if key == 'original_evidence' else audit['final_product_commit']
                raw = subprocess.check_output(['git', 'show', commit + ':' + evidence['path']], cwd=ROOT)
                assert hashlib.sha256(raw).hexdigest() == evidence['sha256']
                assert evidence['needle'] in raw.decode().splitlines()[evidence['line']-1]
    for name in audit['unchanged_files']:
        assert (ROOT / name).read_bytes() == subprocess.check_output(['git', 'show', audit['original_commit']+':'+name], cwd=ROOT)
    for name in checked_docs:
        assert not any(word in (ROOT / name).read_text() for word in ['직전', '중간 버전', '최대 두 문서', '두 개 제한'])
    payload = {'measured_product_commit': manifest['latest_commit'],
        'measured_runtime_files_unchanged': len(product_files) - len(changed_runtime),
        'current_ui_files_changed_since_measurement': changed_runtime,
        'backend_runtime_unchanged_since_measurement': True,
        'python_tests_passed': 90, 'ui_tests_passed': 11, 'measured_product_ui_tests_passed': 5,
        'ui_readability': readability, 'current_ui_performance_remeasured': False,
        'api_comparison_requests': 66,
        'feature_checks_passed': 10, 'warmup_requests_excluded': 8, 'langsmith_runs_verified': 84,
        'pptx_slides': 9, 'pptx_native_tables': 1, 'pptx_notes': 9,
        'pptx_sha256': receipt['finalSha256'], 'all_slides_visually_reviewed': True,
        'native_powerpoint_execution_verified': False, 'html_visual_reviewed': False,
        'new_streaming_specific_tests': False, 'human_semantic_accuracy': 'not_measured',
        'actual_invoice': 'not_measured', 'presentation_delivered': False,
        'broken_submission_links': broken, 'credential_scan': 'no configured secret found',
        'comparison_scope': audit['comparison_scope'], 'source_features_audited': 19,
        'runtime_changed_in_document_revision': False, 'comparison_66_rerun_in_document_revision': False,
        'separate_screenshot_demo_executed': True, 'report_student': '3반 G092 이정인',
        'report_pdf_pages': 9, 'presentation_timing': '2분 기능·결과 + 3분 코드 설명'}
    payload['submission_cleanup_files'] = submission['cleanup_obsolete_submission_files']
    payload['current_screenshots_verified'] = len(screenshots['screenshots'])
    (OUT / 'verification.json').write_text(json.dumps(payload, ensure_ascii=False, indent=2))
    (ROOT / 'doc/evidence/final_verification.json').write_text(json.dumps(payload, ensure_ascii=False, indent=2))
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
