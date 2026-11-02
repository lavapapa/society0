# cases: NEW-2-T01 RV3-01-T01
"""真实编译参照与真实Git输入的既有行为回归。"""
from pathlib import Path
import json, os, shutil, subprocess, sys, tempfile, unittest
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from build_native import copy_inputs, verify_native, NATIVE_OUTPUTS


class BuildInputs(unittest.TestCase):
    def test_git_inputs_exclude_ignored_deleted_and_materialize_links(self):
        fixture = Path(tempfile.mkdtemp(prefix='g18-git-inputs-', dir=os.environ['TMPDIR']))
        source = fixture / 'source'; source.mkdir(); package = source / 'packages/demo'; package.mkdir(parents=True)
        (source / 'scripts').mkdir(); (source / 'scripts/helper.py').write_text('actual helper\n')
        (package / 'helper.py').symlink_to('../../scripts/helper.py')
        (package / 'kept.txt').write_text('tracked\n'); removed = package / 'removed.txt'; removed.write_text('deleted later\n')
        (source / '.gitignore').write_text('ignored/\n')
        subprocess.run(['git', 'init', '-q'], cwd=source, check=True); subprocess.run(['git', 'add', '.'], cwd=source, check=True)
        assert removed.resolve() == package / 'removed.txt'; removed.unlink()
        (package / '新 文件.txt').write_text('untracked input\n')
        (package / 'ignored').mkdir(); (package / 'ignored/old.node').write_bytes(b'ignored generated input')
        output = fixture / 'output'; output.mkdir(); copy_inputs(source, 'packages/demo', ['.'], output)
        self.assertEqual({p.name for p in output.iterdir()}, {'helper.py', 'kept.txt', '新 文件.txt'})
        self.assertFalse((output / 'helper.py').is_symlink()); self.assertEqual((output / 'helper.py').read_bytes(), (source / 'scripts/helper.py').read_bytes())
        print(json.dumps({'case':'RV3-01-T01','fixture':str(fixture)}), flush=True)

    def test_real_native_reference_rejects_corruption_missing_and_wrong_declaration(self):
        fixture = Path(tempfile.mkdtemp(prefix='g18-native-reference-', dir=os.environ['TMPDIR']))
        build = fixture / 'build'; reference = build / 'native-work/random-native'; package = build / 'random-native'
        process = subprocess.run([sys.executable, '-B', str(ROOT / 'packages/random-native/build.py'), '--out-dir', str(reference)], text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        (fixture / 'compiler.log').write_text(process.stdout); self.assertEqual(process.returncode, 0, process.stdout)
        for relative in NATIVE_OUTPUTS['random-native']:
            target = package / relative; target.parent.mkdir(parents=True, exist_ok=True); shutil.copyfile(reference / relative, target)
        verify_native(build, 'random-native')
        binary = package / 'build/random_native.node'; original = binary.read_bytes()
        try:
            binary.write_bytes(bytes([original[0] ^ 1]) + original[1:])
            with self.assertRaisesRegex(AssertionError, 'built dependency identity mismatch'): verify_native(build, 'random-native')
        finally: binary.write_bytes(original)
        witness = reference / 'build/random_native.node'; original = witness.read_bytes()
        try:
            assert witness.resolve() == build.resolve() / 'native-work/random-native/build/random_native.node'; witness.unlink()
            with self.assertRaisesRegex(AssertionError, 'missing artifact or reference'): verify_native(build, 'random-native')
        finally: witness.write_bytes(original)
        metadata = package / 'NATIVE-BUILD.json'; saved = metadata.read_bytes(); record = json.loads(saved); record['outputs'] = []
        try:
            for parent in [package, reference]: (parent / 'NATIVE-BUILD.json').write_text(json.dumps(record))
            with self.assertRaisesRegex(AssertionError, 'output declaration'): verify_native(build, 'random-native')
        finally:
            for parent in [package, reference]: (parent / 'NATIVE-BUILD.json').write_bytes(saved)
        verify_native(build, 'random-native'); print(json.dumps({'case':'NEW-2-T01','fixture':str(fixture)}), flush=True)


if __name__ == '__main__': unittest.main()
