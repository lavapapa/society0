# cases: RV3-01-T01
"""发行前置条件必须在创建.build之前拒绝；使用真实Git和根CLI。"""
from pathlib import Path
import json, os, shutil, subprocess, sys, tempfile, unittest

ROOT = Path(__file__).resolve().parents[1]


class ReleaseGates(unittest.TestCase):
    def setUp(self):
        self.probe = Path(tempfile.mkdtemp(prefix='g18-release-gate-', dir=os.environ['TMPDIR']))
        for name in ['scripts', 'patches', 'vendor', 'vendor-baseline']:
            (self.probe / name).mkdir()
        for source in (ROOT / 'scripts').glob('*.py'):
            shutil.copyfile(source, self.probe / 'scripts' / source.name)
        for name in ['vendor', 'vendor-baseline']:
            shutil.copyfile(ROOT / name / 'source-manifest.json', self.probe / name / 'source-manifest.json')
        series = json.loads((ROOT / 'patches/series.json').read_text())
        for name in ['series.json', *[item['file'] for item in series['patches']]]:
            shutil.copyfile(ROOT / 'patches' / name, self.probe / 'patches' / name)
        shutil.copyfile(ROOT / 'package-lock.json', self.probe / 'package-lock.json')
        (self.probe / '.gitignore').write_text('.build/\n__pycache__/\n/packages/*/build/\n')
        (self.probe / 'scripts/probe-input.txt').write_text('tracked input\n')
        subprocess.run(['git', 'init', '-q'], cwd=self.probe, check=True)
        subprocess.run(['git', 'add', '.'], cwd=self.probe, check=True)
        subprocess.run(['git', '-c', 'user.name=Release probe', '-c', 'user.email=probe@invalid', '-c', 'commit.gpgsign=false', 'commit', '-qm', 'release gate input'], cwd=self.probe, check=True)
        print(json.dumps({'case':'RV3-01-T01','fixture':str(self.probe)}), flush=True)

    def rejected(self, message, extra=(), bytecode=False):
        command = [sys.executable, *([] if bytecode else ['-B']), 'scripts/build.py', '--release', *extra]
        env = dict(os.environ); env.pop('PYTHONDONTWRITEBYTECODE', None)
        result = subprocess.run(command, cwd=self.probe, env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        (self.probe / 'result.log').write_text(result.stdout)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(message, result.stdout)
        self.assertFalse((self.probe / '.build').exists())

    def test_dirty_worktree(self):
        (self.probe / 'scripts/probe-input.txt').write_text('modified input\n')
        self.rejected('RELEASE_DIRTY_WORKTREE')

    def test_ignored_input(self):
        output = self.probe / 'packages/demo/build'; output.mkdir(parents=True)
        (output / 'old.node').write_bytes(b'ignored input')
        self.rejected('RELEASE_IGNORED_INPUT')

    def test_partial_build(self):
        self.rejected('RELEASE_REQUIRES_FULL_BUILD', ['--package', 'host'])

    def test_reuse(self):
        self.rejected('RELEASE_REQUIRES_FULL_BUILD', ['--reuse-built-dependencies'])

    def test_bytecode_writes(self):
        self.rejected('RELEASE_BYTECODE_WRITES', bytecode=True)


if __name__ == '__main__':
    unittest.main()
