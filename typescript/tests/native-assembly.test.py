# cases: NEW-2-T01
"""使用真实Git输入和编译器，从无预编译源产物的仓库装配Native包。"""
from pathlib import Path
import json, os, shutil, subprocess, sys, tempfile, unittest

ROOT = Path(__file__).resolve().parents[1]
PACKAGES = ['sqlite-native', 'random-native']
OUTPUTS = {'sqlite-native': ['build/sqlite_native.node', 'vendor/libsqlite3.so'],
           'random-native': ['build/random_native.node']}


def probe_tree():
    fixture = Path(tempfile.mkdtemp(prefix='g18-native-assembly-', dir=os.environ['TMPDIR']))
    probe = fixture / 'source'; probe.mkdir()
    scopes = ['scripts', 'patches', 'vendor', 'vendor-baseline', 'tests', 'package.json', 'package-lock.json',
              *['packages/' + name for name in PACKAGES]]
    names = subprocess.check_output(['git', 'ls-files', '-z', '--cached', '--others', '--exclude-standard', '--', *scopes], cwd=ROOT).decode().split('\0')
    for name in sorted(set(names) - {''}):
        source = ROOT / name; target = probe / name; target.parent.mkdir(parents=True, exist_ok=True)
        if source.is_symlink(): target.symlink_to(os.readlink(source))
        else: shutil.copyfile(source, target)
    (probe / '.gitignore').write_text('.build/\nnode_modules\n__pycache__/\n')
    (probe / 'node_modules').symlink_to(ROOT / 'node_modules', target_is_directory=True)
    subprocess.run(['git', 'init', '-q'], cwd=probe, check=True)
    subprocess.run(['git', 'add', '.'], cwd=probe, check=True)
    subprocess.run(['git', '-c', 'user.name=Native probe', '-c', 'user.email=probe@invalid', '-c', 'commit.gpgsign=false',
                    'commit', '-qm', 'native assembly input'], cwd=probe, check=True)
    print(json.dumps({'case': 'NEW-2-T01', 'fixture': str(fixture), 'producer': str(probe)}), flush=True)
    return fixture, probe


def source_bytes(probe):
    return {str(p.relative_to(probe)): (p.stat().st_mtime_ns, p.read_bytes())
            for p in sorted((probe / 'packages').rglob('*')) if p.is_file()}


class NativeAssembly(unittest.TestCase):
    def test_closed_producer_and_packaged_builder(self):
        fixture, probe = probe_tree()
        for package in PACKAGES:
            for relative in OUTPUTS[package]:
                self.assertFalse((probe / 'packages' / package / relative).exists())
        before = source_bytes(probe)
        process = subprocess.run([sys.executable, '-B', 'scripts/build.py', '--package', *PACKAGES[:1], '--package', PACKAGES[1]],
                                 cwd=probe, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        (fixture / 'producer.log').write_text(process.stdout)
        self.assertEqual(process.returncode, 0, process.stdout)
        self.assertEqual(source_bytes(probe), before)
        self.assertEqual(subprocess.check_output(['git', 'status', '--porcelain'], cwd=probe), b'')
        for package in PACKAGES:
            built = probe / '.build' / package; reference = probe / '.build/native-work' / package
            self.assertFalse((built / 'native_build.py').is_symlink())
            for relative in OUTPUTS[package] + ['NATIVE-BUILD.json']:
                self.assertTrue((built / relative).read_bytes() == (reference / relative).read_bytes(), package + '/' + relative)
            identity = json.loads((built / 'SOURCE.json').read_text())
            self.assertEqual(identity['native']['build'], json.loads((built / 'NATIVE-BUILD.json').read_text()))
        consumer = fixture / 'consumer'; shutil.copytree(probe / '.build/random-native', consumer)
        result = subprocess.run([sys.executable, '-B', str(consumer / 'build.py'), '--out-dir', str(fixture / 'rebuilt')],
                                cwd=fixture, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        (fixture / 'consumer-build.log').write_text(result.stdout)
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertTrue((consumer / 'build/random_native.node').read_bytes() == (fixture / 'rebuilt/build/random_native.node').read_bytes())
        sqlite = (probe / '.build/sqlite-native/src/index.mjs').as_uri()
        random = (consumer / 'src/index.js').as_uri()
        code = f"""import assert from 'node:assert/strict';
const {{openConnection}}=await import({json.dumps(sqlite)});
const db=openConnection({{path:{json.dumps(str(fixture / 'smoke.sqlite'))}}});
try{{db.execSchema('CREATE TABLE probe(id INTEGER PRIMARY KEY,value TEXT)');db.begin();db.execute('INSERT INTO probe VALUES(1,?)',['ok']);db.commit();assert.equal(db.query('SELECT value FROM probe')[0].value,'ok')}}finally{{db.close()}}
const {{at}}=await import({json.dumps(random)});const address={{run:'r',actor:'a',operation:'o',stream:'s',index:'0'}};
assert.deepEqual(at('seed',address),at('seed',address));assert.ok(at('seed',address).uniform>=0&&at('seed',address).uniform<1);
console.log('NATIVE_ASSEMBLED_CONSUMERS_OK');"""
        result = subprocess.run(['node', '--input-type=module', '-e', code], cwd=fixture, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        (fixture / 'consumer.log').write_text(result.stdout)
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn('NATIVE_ASSEMBLED_CONSUMERS_OK', result.stdout)


if __name__ == '__main__':
    unittest.main()
