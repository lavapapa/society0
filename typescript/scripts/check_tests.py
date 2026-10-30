"""tests只能导入当前.build、node内置、同目录支持文件或安装态@society0包；禁止冻结快照、源码、主机绝对路径与env兜底。"""
from pathlib import Path
import json, re, sys
root = Path(__file__).resolve().parents[1]
FORBIDDEN = [
    (re.compile(r"evidence/"), 'TEST_EVIDENCE_REFERENCE'),
    (re.compile(r"\.\./packages/[^'\"]+/src/"), 'TEST_SOURCE_IMPORT'),
    (re.compile(r"\.\./packages/[^'\"]+/build/"), 'TEST_SOURCE_TREE_WRITE'),
    (re.compile(r"/mnt/data/"), 'TEST_HOST_PATH'),
    (re.compile(r"process\.env\.[A-Z0-9_]+\s*\?\?"), 'TEST_ENV_FALLBACK'),
]
SUFFIXES = {'.mjs', '.js', '.ts', '.py'}
def violations(base):
    for file in sorted(base.rglob('*')):
        if not file.is_file() or file.suffix not in SUFFIXES or 'fixtures' in file.parts: continue
        for number, line in enumerate(file.read_text().splitlines(), 1):
            for pattern, code in FORBIDDEN:
                if pattern.search(line): yield {'code': code, 'file': str(file.relative_to(base)), 'line': number}
def assert_clean(base):
    found = list(violations(base))
    assert not found, f'TEST_BASELINE {found[:5]}'
if __name__ == '__main__':
    found = list(violations(Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else root / 'tests'))
    print(json.dumps({'violations': found}, indent=1))
    sys.exit(1 if found else 0)
