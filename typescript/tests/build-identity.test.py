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

    def collector(self):
        path = ROOT / 'scripts/build_identity.py'
        self.assertTrue(path.is_file(), 'worktree identity collector is missing')
        spec = importlib.util.spec_from_file_location('build_identity', path)
        module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        return module

    def collect(self):
        return self.collector().worktree_identity(self.project)

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

    def test_saved_evidence_retains_actual_diff_bytes_and_links(self):
        (self.project / 'scripts/input.py').write_text('VALUE = 3\n')
        data = self.project / 'packages/demo/新 文件.bin'; data.write_bytes(b'actual\0bytes\xff')
        (self.project / 'packages/demo/链接 文件').symlink_to('原 名.txt')
        value = self.collect(); before = self.git('status', '--porcelain=v1', '-z')
        build = self.fixture / 'build'
        self.collector().save_worktree_evidence(build, value)
        evidence = build / 'worktree-evidence'
        self.assertEqual((evidence / 'tracked.patch').read_bytes(), value['diff'])
        self.assertEqual((evidence / 'untracked/typescript/packages/demo/新 文件.bin').read_bytes(), data.read_bytes())
        manifest = json.loads((evidence / 'identity.json').read_text())
        self.assertEqual(manifest['gitCommit'], value['gitCommit'])
        self.assertTrue(manifest['dirty'])
        self.assertEqual(manifest['dirtyPaths'], value['dirtyPaths'])
        link = next(row for row in manifest['untracked'] if row['path'].endswith('链接 文件'))
        self.assertEqual(link['symlink'], '原 名.txt')
        stale = evidence / 'stale.txt'; stale.write_text('old evidence')
        self.collector().save_worktree_evidence(build, value)
        self.assertFalse(stale.exists())
        self.assertEqual(self.git('status', '--porcelain=v1', '-z'), before)

    def test_reuse_requires_current_commit_and_dirty_state(self):
        package = json.loads((ROOT / 'packages/core/package.json').read_text())
        upstream = json.loads((ROOT / 'vendor/source-manifest.json').read_text())['commit']
        state = self.collect()
        provenance = {'commit': upstream, 'implementation': {'gitCommit': state['gitCommit'], 'dirty': False}}
        validate = self.collector().validate_dependency
        validate(package, provenance, 'core', upstream, state)
        (self.project / 'scripts/input.py').write_text('VALUE = 2\n')
        with self.assertRaisesRegex(AssertionError, 'built dependency identity mismatch'):
            validate(package, provenance, 'core', upstream, self.collect())
        provenance['implementation']['dirty'] = True
        validate(package, provenance, 'core', upstream, self.collect())
        # 同为dirty的内容变化遵循既定合同，不增加内容新鲜度门禁。
        (self.project / 'scripts/input.py').write_text('VALUE = 3\n')
        validate(package, provenance, 'core', upstream, self.collect())
        self.git('add', '.'); self.git('-c', 'user.name=Identity probe', '-c', 'user.email=probe@invalid', '-c', 'commit.gpgsign=false', 'commit', '-qm', 'new identity')
        provenance['implementation']['dirty'] = False
        with self.assertRaisesRegex(AssertionError, 'built dependency identity mismatch'):
            validate(package, provenance, 'core', upstream, self.collect())

    def test_reuse_rejects_missing_identity_and_wrong_package(self):
        package = json.loads((ROOT / 'packages/core/package.json').read_text())
        upstream = json.loads((ROOT / 'vendor/source-manifest.json').read_text())['commit']
        state = self.collect(); validate = self.collector().validate_dependency
        with self.assertRaisesRegex(AssertionError, 'built dependency identity mismatch'):
            validate(package, {'commit': upstream}, 'core', upstream, state)
        provenance = {'commit': upstream, 'implementation': {'gitCommit': state['gitCommit'], 'dirty': False}}
        with self.assertRaisesRegex(AssertionError, 'built dependency identity mismatch'):
            validate(package, provenance, 'host', upstream, state)
        provenance['implementation']['dirty'] = 'false'
        with self.assertRaisesRegex(AssertionError, 'built dependency identity mismatch'):
            validate(package, provenance, 'core', upstream, state)


if __name__ == '__main__':
    unittest.main()
