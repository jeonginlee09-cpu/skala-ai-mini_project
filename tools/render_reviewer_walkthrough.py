"""Render the checked-in code guide without external fonts, scripts, or CDN."""
import re
import argparse
from pathlib import Path

from markdown_it import MarkdownIt

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / 'doc/reviewer_code_walkthrough.md'


def main():
    global DOC
    parser=argparse.ArgumentParser()
    parser.add_argument('--document',default='doc/reviewer_code_walkthrough.md')
    parser.add_argument('--comparison',default='reviewer_comparison')
    args=parser.parse_args()
    DOC=ROOT/args.document
    source = DOC.read_text()
    broken = []
    for target in re.findall(r'\]\(([^)]+)\)', source):
        if '://' not in target and not (DOC.parent / target.split('#')[0]).resolve().exists():
            broken.append(target)
    assert not broken, broken
    body = MarkdownIt('commonmark', {'html':False}).enable('table').render(source)
    style = '''
    :root{color-scheme:light}*{box-sizing:border-box}body{margin:0;background:#f2f5fa;color:#24334a;
    font:16px/1.8 Pretendard,-apple-system,"Apple SD Gothic Neo",sans-serif}main{max-width:1100px;
    margin:32px auto;padding:36px 48px;background:white;border:1px solid #dce4ef;border-radius:16px}
    h1{font-size:30px;line-height:1.5;margin:0 0 22px}h2{font-size:23px;margin:42px 0 16px;
    padding-top:14px;border-top:2px solid #e4ebf5}h3{font-size:19px;margin-top:28px;color:#1e4a83}
    a{color:#245bb1;text-underline-offset:3px}p{margin:12px 0}blockquote{margin:20px 0;padding:12px 20px;
    border-left:4px solid #376cc3;background:#eef4ff}pre{overflow:auto;background:#13243c;color:#e9f0fa;
    border-radius:10px;padding:18px;font:14px/1.7 ui-monospace,monospace}code{font-size:.9em}
    p code,td code,li code{background:#eef2f8;color:#314b72;padding:2px 5px;border-radius:4px}
    table{border-collapse:collapse;width:100%;font-size:14px;display:block;overflow:auto;margin:16px 0}
    th,td{border:1px solid #dce4ef;padding:10px 12px;vertical-align:top;min-width:125px}
    th{background:#edf3fc;text-align:left}ul,ol{padding-left:24px}li{margin:7px 0}
    .nav{display:flex;gap:20px;flex-wrap:wrap;background:#eaf1fc;padding:14px 18px;border-radius:8px;margin-bottom:25px}
    @media(max-width:700px){main{margin:0;padding:22px 18px;border:0;border-radius:0}h1{font-size:25px}}
    @media print{main{margin:0;max-width:none;border:0;padding:10px}pre,table,blockquote{break-inside:avoid}}
    '''
    page = '<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>기존·최신 코드 설명 자료</title><style>'+style+'</style></head><body><main><nav class="nav"><a href="'+args.comparison+'.md">66건 실측·실제 답변 비교</a><a href="'+args.comparison+'.csv">집계 CSV</a></nav>'+body+'</main></body></html>'
    dest = DOC.with_suffix('.html'); dest.write_text(page)
    print('Rendered:', dest)


if __name__ == '__main__':
    main()
