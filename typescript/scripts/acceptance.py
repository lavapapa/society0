"""仓外 tarball 验收；传入一个新的绝对输出目录。"""
from pathlib import Path
import json, re, subprocess, shutil, sys
root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / 'scripts'))
from acceptance_tests import INSTALLED_COPIES, INSTALLED_ONLY
from acceptance_pack import dependencies, pack
from check_tests import assert_clean
REQUIRED = {'pr03-config.test.mjs': 'host', 'pr04.test.mjs': 'core', 'execution-fence.test.mjs': 'core', 'pr04-cordis.test.mjs': 'core', 'pr04-directory.test.mjs': 'core', 'pr11-random.test.mjs': 'random-native', 'pr11-random-bootstrap.test.mjs': 'random-native'}
out = Path(sys.argv[1]).resolve()
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
    subprocess.run(list(map(str, args)), cwd=cwd, check=True)
release = json.loads((root / '.build/release.json').read_text())
write(out / 'release.json', release)
shutil.copytree(root / '.build/acceptance', out / 'inputs')
test_root = out / 'inputs/tests'
fixture_root = test_root / 'fixtures'
assert test_root.is_dir() and fixture_root.is_dir(), 'build first to freeze acceptance inputs'
packages = [pack(root, root / '.build' / folder, tar) for folder in release['packages']]
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
def installed_copy(name):
    rules = INSTALLED_COPIES[name]
    text = (test_root / name).read_text()
    if rules == 'regex-host-core':
        text = text.replace('../.build/host/dist/profile-loader/compiler.js', '@society0/host/profile-loader')
        text = text.replace('../.build/core/dist/sqlite-history/history.js', '@society0/core/sqlite-history')
        text = re.sub(r"\.\./\.build/(host|core)/dist/([^\"']+)\.js", lambda match: '@society0/' + match[1] + ('' if match[2] == 'index' else '/' + match[2].removesuffix('/index')), text)
    elif rules == 'random-native':
        text = text.replace("fileURLToPath(new URL('../.build',import.meta.url))", "resolve('.')")
        text = re.sub(r"new URL\('\.\./\.build/random-native/([^']+)',import.meta.url\)", lambda match: "new URL('./" + match[1] + "',pathToFileURL(require.resolve('@society0/random-native/package.json')))", text)
    else:
        for source, target in rules.items():
            assert source in text, (name, source)
            text = text.replace(source, target)
    assert '../.build/' not in text, name
    write(consumer / name, text)
    return name
for name in INSTALLED_COPIES:
    required = REQUIRED.get(name)
    if required and required not in release['packages']: continue
    if name == 'pr03-config.test.mjs' and 'host' not in release['packages']: continue
    extra_tests.append(installed_copy(name))
if 'core' in release['packages']:
    shutil.copyfile(test_root / 'module-sidecars.test.mjs', consumer / 'module-sidecars.test.mjs')
    extra_tests.extend(['module-sidecars.test.mjs', 'ajv-consumer.ts', 'ripgrep-consumer.ts'])
run(['node', '--test', '--test-concurrency=1', *base_tests, *extra_tests, *[str(path.relative_to(consumer)) for path in (consumer / 'native-tests').glob('*.test.ts')]])
run(['node', 'node_modules/@society0/cli/dist/main.js', '--boot', 'empty-boot.json'])
write(out / 'result.json', {'status': 'passed', 'node': subprocess.check_output(['node', '--version'], text=True).strip(), 'consumer': str(consumer), 'release': release, 'plugins': ['@fixtures/provider', '@fixtures/consumer', '@fixtures/jsdoc']})
