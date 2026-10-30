"""逐文件运行当前.build层测试并输出JSON。用法：test-build.py [build|performance|live]。"""
from pathlib import Path
import json, re, subprocess, sys, time
root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / 'scripts'))
from acceptance_tests import BUILD_EXCLUDED, INSTALLED_ONLY, INSTALLED_SCRIPTS_PREFIX, LIVE, PERFORMANCE
BUILD = (root / '.build').resolve()
def test_files():
    return [file for file in sorted((root / 'tests').glob('*.mjs')) if "from 'node:test'" in file.read_text() or 'from "node:test"' in file.read_text()]
def build_layer():
    return [file for file in test_files() if file.name not in INSTALLED_ONLY | LIVE | PERFORMANCE | BUILD_EXCLUDED and not file.name.startswith(INSTALLED_SCRIPTS_PREFIX)]
def resolves_in_build(files):
    names = ['host', 'core', 'schemastery', 'cordis']
    code = "const r=require('node:module').createRequire(process.cwd()+'/x.js'),fs=require('node:fs');for(const n of %s)console.log(n+' '+fs.realpathSync(r.resolve('@society0/'+n+'/package.json')))" % json.dumps(names)
    out = subprocess.run(['node', '-e', code], cwd=root / '.build', check=True, text=True, capture_output=True).stdout.splitlines()
    bad = [line for line in out if not Path(line.split(' ', 1)[1]).is_relative_to(BUILD)]
    assert len(out) == len(names) and not bad, 'TEST_RESOLVES_OUTSIDE_BUILD ' + ';'.join(bad)
    bare = [file.name for file in files if re.search(r"from '@society0/", file.read_text())]
    assert not bare, 'TEST_BARE_IMPORT ' + ';'.join(bare)
def main(kind):
    files = build_layer() if kind == 'build' else [file for file in test_files() if file.name in {'performance': PERFORMANCE, 'live': LIVE}[kind]]
    resolves_in_build(files)
    results = []
    for file in files:
        started = time.monotonic()
        process = subprocess.run(['node', '--test', '--test-concurrency=1', '--test-timeout=900000', str(file)], cwd=root, text=True, capture_output=True)
        results.append({'file': file.name, 'exitCode': process.returncode, 'seconds': round(time.monotonic() - started, 1), 'tail': process.stdout[-4000:] + process.stderr[-4000:]})
        print(json.dumps(results[-1]), flush=True)
    failed = [result['file'] for result in results if result['exitCode']]
    print(json.dumps({'summary': {'files': len(results), 'failed': failed}}))
    return 1 if failed else 0
if __name__ == '__main__':
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else 'build'))
