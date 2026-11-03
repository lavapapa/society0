"""仓外安装验收：acceptance.py [--candidate <发行目录>] <全新输出目录>。"""
from pathlib import Path
import argparse, json, subprocess, shutil, sys
root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / 'scripts'))
from acceptance_tests import INSTALLED_COPIES, INSTALLED_ONLY, INSTALLED_SUPPORT
from acceptance_copy import rewrite_installed
from acceptance_pack import dependencies, pack
from acceptance_candidate import load_candidate, prepare_installed_assets, profile_lock_command, artifact_command
from acceptance_execution import run_command, write_test_receipt
from acceptance_ledger_bridge import preflight, write_installed_fragments
command_records = []
from check_tests import assert_clean
REQUIRED = {'pr03-config.test.mjs': 'host', 'pr04.test.mjs': 'core', 'execution-fence.test.mjs': 'core', 'pr04-cordis.test.mjs': 'core', 'pr04-directory.test.mjs': 'core', 'pr11-random.test.mjs': 'random-native', 'pr11-random-bootstrap.test.mjs': 'random-native', 'pr11-random-order.test.mjs': 'random-native', 'pr08-public-display-error.test.mjs': 'core', 'pr08-slow-persistence.test.mjs': 'core', 'pr08-recorded-result.test.mjs': 'core', 'pr07-exploration.test.mjs': 'extras', 'pr11-policy-history.test.mjs': 'core'}
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--candidate', type=Path, help='安装已冻结的发行包和验收输入；不重打当前.build生产包')
parser.add_argument('output', type=Path, help='仓外全新消费者输出目录')
args = parser.parse_args()
selected = load_candidate(args.candidate) if args.candidate is not None else None
out = args.output.resolve()
assert not out.is_relative_to(root), 'consumer must be outside repository'
assert not out.exists(), 'use a fresh evidence directory'
assert_clean(selected['inputs'] / 'tests' if selected is not None else root / 'tests')
ledger_readiness = None
if selected is not None:
    planned_names = INSTALLED_ONLY | set(INSTALLED_COPIES) | {'ajv-consumer.ts', 'ripgrep-consumer.ts'}
    planned_paths = {'typescript/tests/' + name for name in planned_names}
    planned_paths.update('typescript/tests/native/' + path.name for path in (selected['inputs'] / 'tests/native').glob('*.test.ts'))
    ledger_readiness = preflight(root.parents[1], selected['candidate'], out, planned_paths)
out.mkdir(parents=True)
tar = out / 'tarballs'; tar.mkdir()
consumer = out / 'consumer'; consumer.mkdir()
def write(path, value):
    path.write_text(value if isinstance(value, str) else json.dumps(value, indent=2) + '\n')
def run(args, cwd=consumer):
    print('+', *map(str, args), flush=True)
    return run_command(args, cwd, out, command_records)
release = selected['release'] if selected is not None else json.loads((root / '.build/release.json').read_text())
mode = 'candidate' if selected is not None else ('dirty-development' if release['implementation']['dirty'] else 'development')
write(out / 'release.json', release)
if ledger_readiness is not None:
    write(out / 'ledger-preflight.json', {'status': 'ready', 'remainingInstalledTemplateErrors': ledger_readiness['remainingErrors']})
shutil.copytree(selected['inputs'] if selected is not None else root / '.build/acceptance', out / 'inputs')
test_root = out / 'inputs/tests'
fixture_root = test_root / 'fixtures'
assert test_root.is_dir() and fixture_root.is_dir(), 'build first to freeze acceptance inputs'
prepare_installed_assets(out / 'inputs', consumer)
if selected is not None: run(artifact_command(selected, out / 'inputs'))
packages = list(selected['packages']) if selected is not None else [pack(root, root / '.build' / folder, tar) for folder in release['packages']]
packages += dependencies(root, tar)
write(consumer / 'package.json', {'name': 'society0-outside-consumer', 'version': '1.0.0', 'private': True, 'type': 'module'})
run(['npm', 'install', '--offline', '--ignore-scripts', *packages])
for name in ['provider', 'consumer', 'jsdoc']:
    folder = consumer / 'fixture-build' / name
    shutil.copytree(fixture_root / name, folder / 'src')
    manifest = {'name': '@fixtures/' + name, 'version': '1.0.0', 'type': 'module', 'files': ['dist'], 'exports': {'.': {'types': './dist/index.d.ts', 'default': './dist/index.js'}}, 'peerDependencies': {'@society0/host': '^0.1.0'}}
    if name == 'provider': manifest['exports']['./service'] = {'types': './dist/service.d.ts', 'default': './dist/service.js'}
    else: manifest['peerDependencies']['@fixtures/provider'] = '1.0.0'
    write(folder / 'package.json', manifest)
    write(folder / 'tsconfig.json', {'compilerOptions': {'target': 'ES2024', 'module': 'NodeNext', 'strict': True, 'isolatedModules': True, 'skipLibCheck': False, 'declaration': True, 'allowJs': True, 'checkJs': True, 'rootDir': 'src', 'outDir': 'dist', 'types': ['node']}, 'include': ['src/**/*']})
    run(['node', root / 'node_modules/typescript/bin/tsc', '-p', folder / 'tsconfig.json'])
    run(['npm', 'install', '--offline', '--ignore-scripts', pack(root, folder, tar)])
run(['npm', 'ls', '--all'])
run(profile_lock_command(consumer, mode))
if selected is not None: run(artifact_command(selected, out / 'inputs', consumer))
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
installed_test_command = run(['node', '--test', '--test-concurrency=1', *base_tests, *extra_tests, *native_test_targets])
executed_targets = {'typescript/tests/' + name: name for name in [*base_tests, *extra_tests]}
executed_targets.update({'typescript/tests/native/' + Path(name).name: name for name in native_test_targets})
run(['node', 'node_modules/@society0/cli/dist/main.js', '--boot', 'empty-boot.json'])
write_test_receipt(out, {'scope': 'installed-tests', 'node': subprocess.check_output(['node', '--version'], text=True).strip(), 'consumer': str(consumer), 'release': release, 'plugins': ['@fixtures/provider', '@fixtures/consumer', '@fixtures/jsdoc'], 'mode': mode, 'candidate': str(selected['candidate']) if selected is not None else None}, command_records)

if selected is not None:
    write_installed_fragments(root.parents[1], selected['candidate'], out, ledger_readiness['ledger'], installed_test_command, executed_targets)
    run(['python3', '-B', root / 'scripts/acceptance_ledger.py', 'apply', root.parents[1]])
    run(['python3', '-B', root / 'scripts/acceptance_ledger.py', 'check', root.parents[1], '--final'])
