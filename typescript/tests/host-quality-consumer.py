"""host 质量消费者：host-quality-consumer.py <新的绝对输出目录> --candidate <release 输出目录>。"""
from pathlib import Path
import json, subprocess, shutil, sys
root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / 'scripts'))
from acceptance_pack import dependencies, pack
args = sys.argv[1:]
assert len(args) == 3 and args[1] == '--candidate', 'usage: host-quality-consumer.py <output-dir> --candidate <release-out-dir>'
out = Path(args[0]).resolve()
candidate = Path(args[2]).resolve()
assert not out.is_relative_to(root), 'consumer must be outside repository'
assert not out.exists(), 'use a fresh output directory'
out.mkdir(parents=True)
tar = out / 'tarballs'; tar.mkdir()
consumer = out / 'consumer'; consumer.mkdir()
(consumer / 'package.json').write_text(json.dumps({'name': 'host-quality-consumer', 'private': True, 'type': 'module'}))
archives = sorted((candidate / 'tarballs').glob('*.tgz'))
assert archives, 'candidate tarballs missing'
packages = [*archives, *dependencies(root, tar)]
subprocess.run(['npm', 'install', '--ignore-scripts', *map(str, packages)], cwd=consumer, check=True)
for name in ['consumer.test.mjs', 'exports.test.mjs', 'peer-contract.test.mjs', 'pr02.test.mjs', 'cli.test.mjs']:
    text = (root / 'tests' / name).read_text().replace('../.build/host/dist/index.js', '@society0/host')
    assert '../.build/' not in text, name
    (consumer / name).write_text(text)
shutil.copytree(root / 'tests/pr02', consumer / 'pr02')
subprocess.run(['node', str(root / 'node_modules/c8/bin/c8.js'), '--reporter=lcov', '--reporter=json-summary', '--exclude-node-modules=false', '--include=node_modules/@society0/host/**', '--report-dir=' + str(out / 'coverage'), 'node', '--test', '--test-concurrency=1', 'consumer.test.mjs', 'exports.test.mjs', 'peer-contract.test.mjs', 'pr02.test.mjs', 'cli.test.mjs'], cwd=consumer, check=True)
print(json.dumps({'status': 'passed', 'candidate': str(candidate), 'consumer': str(consumer)}))
