"""仓外安装验收：acceptance.py <全新输出目录>。"""
from pathlib import Path
import argparse, json, subprocess, shutil, sys
root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / 'scripts'))
from acceptance_tests import INSTALLED_COPIES, INSTALLED_ONLY, INSTALLED_SUPPORT
from acceptance_copy import rewrite_installed
from acceptance_pack import dependencies, pack
from acceptance_consumer import prepare_installed_assets, profile_lock_command
from acceptance_execution import run_command, write_test_receipt
command_records = []
from check_tests import assert_clean
REQUIRED = {'pr03-config.test.mjs': 'host', 'pr04.test.mjs': 'core', 'execution-fence.test.mjs': 'core', 'pr04-cordis.test.mjs': 'core', 'pr04-directory.test.mjs': 'core', 'pr11-random.test.mjs': 'random-native', 'pr11-random-bootstrap.test.mjs': 'random-native', 'pr11-random-order.test.mjs': 'random-native', 'pr08-public-display-error.test.mjs': 'core', 'pr08-slow-persistence.test.mjs': 'core', 'pr08-recorded-result.test.mjs': 'core', 'pr07-exploration.test.mjs': 'extras', 'pr11-policy-history.test.mjs': 'core', 'pr10-history-pages.test.mjs': 'social', 'platform-worker-loss.test.mjs': 'core', 'pr05-history-gates.test.mjs': 'core'}
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('output', type=Path, help='仓外全新消费者输出目录')
args = parser.parse_args()
out = args.output.resolve()
assert not out.is_relative_to(root), 'consumer must be outside repository'
assert not out.exists(), 'use a fresh evidence directory'
assert_clean(root / 'tests')
out.mkdir(parents=True)
tar = out / 'tarballs'; tar.mkdir()
consumer = out / 'consumer'; consumer.mkdir()
def write(path, value):
    path.write_text(value if isinstance(value, str) else json.dumps(value, indent=2) + '\n')
def run(args, cwd=consumer):
    print('+', *map(str, args), flush=True)
    return run_command(args, cwd, out, command_records)
release = json.loads((root / '.build/release.json').read_text())
mode = 'dirty-development' if release['implementation']['dirty'] else 'development'
write(out / 'release.json', release)
shutil.copytree(root / '.build/acceptance', out / 'inputs')
test_root = out / 'inputs/tests'
fixture_root = test_root / 'fixtures'
assert test_root.is_dir() and fixture_root.is_dir(), 'build first to freeze acceptance inputs'
prepare_installed_assets(out / 'inputs', consumer)
packages = [pack(root, root / '.build' / folder, tar) for folder in release['packages']]
packages += dependencies(root, tar)
write(consumer / 'package.json', {'name': 'society0-outside-consumer', 'version': '1.0.0', 'private': True, 'type': 'module'})
run(['npm', 'install', '--ignore-scripts', *packages])
for name in ['provider', 'consumer', 'jsdoc']:
    folder = consumer / 'fixture-build' / name
    shutil.copytree(fixture_root / name, folder / 'src')
    manifest = {'name': '@fixtures/' + name, 'version': '1.0.0', 'type': 'module', 'files': ['dist'], 'exports': {'.': {'types': './dist/index.d.ts', 'default': './dist/index.js'}}, 'peerDependencies': {'@society0/host': '^0.1.0'}}
    if name == 'provider': manifest['exports']['./service'] = {'types': './dist/service.d.ts', 'default': './dist/service.js'}
    else: manifest['peerDependencies']['@fixtures/provider'] = '1.0.0'
    write(folder / 'package.json', manifest)
    write(folder / 'tsconfig.json', {'compilerOptions': {'target': 'ES2024', 'module': 'NodeNext', 'strict': True, 'isolatedModules': True, 'skipLibCheck': False, 'declaration': True, 'allowJs': True, 'checkJs': True, 'rootDir': 'src', 'outDir': 'dist', 'types': ['node']}, 'include': ['src/**/*']})
    run(['node', root / 'node_modules/typescript/bin/tsc', '-p', folder / 'tsconfig.json'])
    run(['npm', 'install', '--ignore-scripts', pack(root, folder, tar)])
run(['npm', 'ls', '--all'])
run(profile_lock_command(consumer, mode))
run(['python3', '-B', test_root / 'artifacts.test.py', out, release['implementation']['gitCommit'], '--consumer-root', consumer], out)
shutil.copyfile(test_root / 'consumer-types.ts', consumer / 'consumer-types.ts')
shutil.copyfile(test_root / 'pr02-types.ts', consumer / 'pr02-types.ts')
shutil.copyfile(test_root / 'include-types.ts', consumer / 'include-types.ts')
consumer_types = ['consumer-types.ts', 'pr02-types.ts', 'include-types.ts']
if 'core' in release['packages']:
    for filename in ['ajv-consumer.ts', 'ripgrep-consumer.ts']:
        shutil.copyfile(test_root / filename, consumer / filename)
        consumer_types.append(filename)
write(consumer / 'tsconfig.json', {'compilerOptions': {'target': 'ES2024', 'module': 'NodeNext', 'strict': True, 'isolatedModules': True, 'skipLibCheck': False, 'noEmit': True, 'types': ['node']}, 'include': consumer_types})
run(['node', root / 'node_modules/typescript/bin/tsc', '-p', 'tsconfig.json'])
base_tests = [name for name in sorted(INSTALLED_ONLY) if name != 'module-sidecars.test.mjs']
for name in base_tests:
    shutil.copyfile(test_root / name, consumer / name)
shutil.copytree(test_root / 'pr02', consumer / 'pr02')
write(consumer / 'empty-boot.json', '[]\n')
shutil.copytree(test_root / 'native', consumer / 'native-tests')
extra_tests = []
def installed_copy(name, rules):
    write(consumer / name, rewrite_installed(name, (test_root / name).read_text(), rules))
    return name
for name in INSTALLED_COPIES:
    required = REQUIRED.get(name)
    if required and required not in release['packages']: continue
    if name == 'pr03-config.test.mjs' and 'host' not in release['packages']: continue
    extra_tests.append(installed_copy(name, INSTALLED_COPIES[name]))
for name, rules in INSTALLED_SUPPORT.items(): installed_copy(name, rules)
if 'core' in release['packages']:
    shutil.copyfile(test_root / 'module-sidecars.test.mjs', consumer / 'module-sidecars.test.mjs')
    extra_tests.extend(['module-sidecars.test.mjs', 'ajv-consumer.ts', 'ripgrep-consumer.ts'])
native_test_targets = [str(path.relative_to(consumer)) for path in (consumer / 'native-tests').glob('*.test.ts')]
run(['node', '--test', '--test-concurrency=1', *base_tests, *extra_tests, *native_test_targets])
run(['node', 'node_modules/@society0/cli/dist/main.js', '--boot', 'empty-boot.json'])
write_test_receipt(out, {'scope': 'installed-tests', 'node': subprocess.check_output(['node', '--version'], text=True).strip(), 'consumer': str(consumer), 'release': release, 'plugins': ['@fixtures/provider', '@fixtures/consumer', '@fixtures/jsdoc'], 'mode': mode}, command_records)
