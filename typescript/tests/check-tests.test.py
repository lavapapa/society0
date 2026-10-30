"""NEW-5-T01：tests 基线门禁拒绝冻结快照、源码导入、主机路径与 env 兜底，fixtures 豁免。"""
from pathlib import Path
import json, subprocess, sys, tempfile
root = Path(__file__).resolve().parents[1]
gate = root / 'scripts/check_tests.py'
FROZEN = 'evi' + 'dence/'
HOST = '/mnt/' + 'data/'
CASES = {
    'TEST_EVIDENCE_REFERENCE': "import x from '../" + FROZEN + "pr05/build/x.js';\n",
    'TEST_SOURCE_IMPORT': "import x from '../packages/" + "core/src/y.ts';\n",
    'TEST_SOURCE_TREE_WRITE': "import x from '../packages/" + "core/build/y.js';\n",
    'TEST_HOST_PATH': "const p = '" + HOST + "x';\n",
    'TEST_ENV_FALLBACK': "const s = process.env.PR11_STAGE " + "?? '.build';\n",
}
def run(directory):
    return subprocess.run([sys.executable, '-B', str(gate), str(directory)], text=True, capture_output=True)
def write(directory, name, text):
    file = directory / name
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(text)
    return file
for code, text in CASES.items():
    with tempfile.TemporaryDirectory() as temporary:
        directory = Path(temporary)
        write(directory, 'bad.test.mjs', text)
        result = run(directory)
        assert result.returncode == 1, (code, result.stdout, result.stderr)
        assert [item['code'] for item in json.loads(result.stdout)['violations']] == [code], (code, result.stdout)
        assert json.loads(result.stdout)['violations'][0]['line'] == 1, code
with tempfile.TemporaryDirectory() as temporary:
    directory = Path(temporary)
    write(directory, 'good.test.mjs', "import test from 'node:test';\n")
    write(directory, 'fixtures/ignored.mjs', "const p = '../" + FROZEN + "x';\n")
    result = run(directory)
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert json.loads(result.stdout)['violations'] == []
print(json.dumps({'case': 'NEW-5-T01', 'status': 'passed'}))
