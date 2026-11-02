# cases: NEW-2-T01
"""真实Native编译验证输出封闭、输入不变与同工具链字节复现。"""
from pathlib import Path
import json, os, shutil, subprocess, sys, tempfile, unittest

ROOT = Path(__file__).resolve().parents[1]
OUTPUTS = {'sqlite-native': ['build/sqlite_native.node', 'vendor/libsqlite3.so'],
           'random-native': ['build/random_native.node']}


def snapshot(directory):
    return {str(p.relative_to(directory)): (p.stat().st_mtime_ns, p.read_bytes() if p.is_file() else None)
            for p in [directory, *sorted(directory.rglob('*'))]}


def source_fixture(package):
    fixture = Path(tempfile.mkdtemp(prefix='g18-native-' + package + '-', dir=os.environ['TMPDIR']))
    source = fixture / 'source' / 'packages' / package
    shutil.copytree(ROOT / 'packages' / package, source)
    shutil.copytree(ROOT / 'scripts', fixture / 'source/scripts', ignore=shutil.ignore_patterns('__pycache__'))
    print(json.dumps({'case': 'NEW-2-T01', 'package': package, 'fixture': str(fixture)}), flush=True)
    return fixture, source


def git_state(package):
    return subprocess.check_output(['git', 'status', '--porcelain', '--ignored', '-z',
                                    '--', 'packages/' + package], cwd=ROOT)


def node_header():
    node = Path(subprocess.check_output(['node', '-p', 'process.execPath'], text=True).strip())
    return Path(os.environ.get('NODE_INCLUDE_DIR', str(node.parent.parent / 'include/node'))) / 'node_api.h'


class NativeBuild(unittest.TestCase):
    def check_metadata(self, output, expected):
        record = json.loads((output / 'NATIVE-BUILD.json').read_text())
        self.assertEqual(record['format'], 'society-native-build/v1')
        self.assertEqual(record['outputs'], expected)
        self.assertEqual(record['flags']['prefixMaps'],
                         ['<package>=.', '<out>=.', '<node-headers>=node-headers'])
        self.assertTrue(record['flags']['randomSeed'])
        self.assertTrue(record['compiler']['cxx'])
        self.assertEqual(record['node']['version'],
                         subprocess.check_output(['node', '--version'], text=True).strip())

    def check_binary(self, file, fixture):
        data = file.read_bytes()
        for forbidden in [str(fixture), str(ROOT), os.environ['HOME'], '/mnt/' + 'data/']:
            self.assertFalse(forbidden.encode() in data, 'absolute path in ' + file.name + ': ' + forbidden)

    def check_package(self, package):
        original = ROOT / 'packages' / package
        original_bytes, original_git = snapshot(original), git_state(package)
        header = node_header(); header_bytes = header.read_bytes()
        fixture, source = source_fixture(package)
        if package == 'random-native':
            probe = source / 'source/addon.cc'
            text = probe.read_text()
            probe.write_text(text + '\nextern "C" { const char* society0_build_path_probe = __FILE__; }\n')
        before = snapshot(fixture / 'source')
        for side in ['a', 'b']:
            output = fixture / side
            process = subprocess.run([sys.executable, '-B', str(source / 'build.py'), '--out-dir', str(output)],
                                     cwd=ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            (fixture / (side + '.build.log')).write_text(process.stdout)
            self.assertEqual(process.returncode, 0, process.stdout)
            for relative in OUTPUTS[package]:
                self.assertTrue((output / relative).is_file(), 'missing output ' + relative)
                self.check_binary(output / relative, fixture)
            self.check_metadata(output, OUTPUTS[package])
            self.assertEqual(snapshot(fixture / 'source'), before, 'build changed its input tree')
        for relative in OUTPUTS[package] + ['NATIVE-BUILD.json']:
            self.assertTrue((fixture / 'a' / relative).read_bytes() == (fixture / 'b' / relative).read_bytes(),
                            'different bytes: ' + relative)
        self.assertEqual(snapshot(original), original_bytes)
        self.assertEqual(git_state(package), original_git)
        self.assertEqual(header.read_bytes(), header_bytes)

    def test_source_output_is_rejected(self):
        for package in OUTPUTS:
            fixture, source = source_fixture(package)
            before = snapshot(fixture / 'source')
            process = subprocess.run([sys.executable, '-B', str(source / 'build.py'), '--out-dir', str(source / 'rejected-output')],
                                     cwd=ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            (fixture / 'rejected.build.log').write_text(process.stdout)
            self.assertNotEqual(process.returncode, 0, package + ' accepted output inside its source tree')
            self.assertEqual(snapshot(fixture / 'source'), before, 'rejected build wrote source files/directories')

    def test_random(self):
        self.check_package('random-native')

    def test_sqlite(self):
        self.check_package('sqlite-native')


if __name__ == '__main__':
    unittest.main()
