# cases: RV3-01-T01
"""真实Git检验工作树身份与可恢复差异；不调用编译器。"""
from pathlib import Path
import importlib.util, json, os, subprocess, tempfile, unittest

ROOT = Path(__file__).resolve().parents[1]


class BuildIdentity(unittest.TestCase):
    def setUp(self):
        self.fixture = Path(tempfile.mkdtemp(prefix='g18-build-identity-', dir=os.environ['TMPDIR']))
        self.repo = self.fixture / 'repo'; self.project = self.repo / 'typescript'
        (self.project / 'scripts').mkdir(parents=True)
        (self.project / 'packages/demo').mkdir(parents=True)
        (self.project / 'scripts/input.py').write_text('VALUE = 1\n')
        (self.project / 'packages/demo/原 名.txt').write_text('tracked input\n')
        (self.repo / '.gitignore').write_text('/typescript/node_modules\n__pycache__/\n/typescript/packages/*/build/\n')
        self.git('init', '-q'); self.git('add', '.')
        self.git('-c', 'user.name=Identity probe', '-c', 'user.email=probe@invalid', '-c', 'commit.gpgsign=false', 'commit', '-qm', 'identity input')
        print(json.dumps({'case':'RV3-01-T01','fixture':str(self.fixture)}), flush=True)

    def git(self, *args):
        return subprocess.check_output(['git', *args], cwd=self.repo)

    def collect(self):
        path = ROOT / 'scripts/build_identity.py'
        self.assertTrue(path.is_file(), 'worktree identity collector is missing')
        spec = importlib.util.spec_from_file_location('build_identity', path)
        module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        return module.worktree_identity(self.project)

    def test_clean_commit_and_ignored_dependency_link(self):
        dependencies = self.fixture / 'dependencies'; dependencies.mkdir()
        (self.project / 'node_modules').symlink_to(dependencies, target_is_directory=True)
        value = self.collect()
        self.assertEqual(value['gitCommit'], self.git('rev-parse', 'HEAD').decode().strip())
        self.assertFalse(value['dirty']); self.assertEqual(value['dirtyPaths'], [])
        self.assertEqual(value['ignoredInputs'], []); self.assertEqual(value['diff'], b'')
        self.assertEqual(value['untracked'], [])

    def test_tracked_diff_and_untracked_unicode_are_recoverable(self):
        (self.project / 'scripts/input.py').write_text('VALUE = 2\n')
        path = self.project / 'packages/demo/新 文件.bin'; path.write_bytes(b'actual\0bytes\xff')
        value = self.collect()
        self.assertTrue(value['dirty'])
        self.assertEqual(set(value['dirtyPaths']), {'typescript/scripts/input.py', 'typescript/packages/demo/新 文件.bin'})
        self.assertEqual(value['diff'], self.git('diff', 'HEAD', '--binary'))
        self.assertEqual(value['untracked'], [{'path':'typescript/packages/demo/新 文件.bin','content':path.read_bytes()}])

    def test_rename_retains_both_complete_paths(self):
        self.git('mv', 'typescript/packages/demo/原 名.txt', 'typescript/packages/demo/新 名.txt')
        raw = self.git('status', '--porcelain=v1', '-z', '--untracked-files=all')
        self.assertIn(b'R ', raw)
        value = self.collect()
        self.assertTrue(value['dirty'])
        self.assertEqual(set(value['dirtyPaths']), {'typescript/packages/demo/原 名.txt', 'typescript/packages/demo/新 名.txt'})
        self.assertEqual(value['diff'], self.git('diff', 'HEAD', '--binary'))

    def test_ignored_generated_inputs_are_separate_from_dirty(self):
        output = self.project / 'packages/demo/build'; output.mkdir(); (output / 'old.node').write_bytes(b'ignored input')
        cache = self.project / 'scripts/__pycache__'; cache.mkdir(); (cache / 'old.pyc').write_bytes(b'old cache')
        value = self.collect()
        self.assertFalse(value['dirty'])
        self.assertEqual(set(value['ignoredInputs']), {'typescript/packages/demo/build/', 'typescript/scripts/__pycache__/'})

    def test_untracked_symlink_preserves_link_text(self):
        (self.project / 'packages/demo/链接 文件').symlink_to('原 名.txt')
        value = self.collect()
        self.assertEqual(value['untracked'], [{'path':'typescript/packages/demo/链接 文件','symlink':'原 名.txt'}])


if __name__ == '__main__':
    unittest.main()
