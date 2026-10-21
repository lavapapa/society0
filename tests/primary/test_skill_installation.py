"""安装文档规格：独立分发、离线初始化、完整点恢复与明确的付费边界。"""
import asyncio
import json
import shutil
import os
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
REFERENCES = ROOT / 'skill' / 'references'


def test_documented_offline_commands_create_and_restore_complete_point(tmp_path):
    text = (REFERENCES / 'installation.md').read_text()
    block = re.search(r'<!-- offline-recovery-start -->\s*```sh\n(.*?)\n```', text, re.S)
    assert block, '安装指南须含可执行且无需模型的恢复验收'
    env = {**os.environ, 'SOCIETY0_REPO': str(ROOT), 'SOCIETY0_PYTHON': sys.executable,
           'SOCIETY0_STUDY': str(tmp_path / 'study'), 'SOCIETY0_RELEASE': 'documentation-test',
           'PYTHONPATH': str(ROOT / 'src')}
    result = subprocess.run(['bash', '-eu', '-c', block.group(1)], cwd=ROOT, env=env,
                            text=True, capture_output=True)
    assert result.returncode == 0, result.stdout + result.stderr
    for name, step in [('check', 0), ('first', 1), ('resumed', 2)]:
        status = json.loads((tmp_path / 'study' / 'environment-check' / name / 'runner-status.json').read_text())
        assert status['complete_step'] == step
        assert status['status'] == 'completed'


def test_quickstart_python_blocks_compile():
    text = (REFERENCES / 'runtime-quickstart.md').read_text()
    blocks = re.findall(r"<<'PY'\n(.*?)\nPY\n", text, re.S)
    assert blocks
    for block in blocks:
        compile(block, 'runtime-quickstart.md', 'exec')


def test_documented_starter_check_uses_real_services_without_credentials(tmp_path):
    root = tmp_path / 'versions' / 'v001'
    root.mkdir(parents=True)
    shutil.copyfile(ROOT / 'skill/assets/minimal_experiment.py', root / 'experiment.py')
    text = (REFERENCES / 'runtime-quickstart.md').read_text()
    block = next(block for block in re.findall(r'```sh\n(.*?)\n```', text, re.S)
                 if "export SOCIETY0_LLM_MODEL='offline-check'" in block)
    env = {key: value for key, value in os.environ.items() if not key.startswith('SOCIETY0_')}
    env.update(SOCIETY0_PYTHON=sys.executable, SOCIETY0_STUDY=str(tmp_path),
               SOCIETY0_RELEASE='documentation-test', PYTHONPATH=str(ROOT / 'src'))
    result = subprocess.run(['bash', '-eu', '-c', block], cwd=ROOT, env=env,
                            text=True, capture_output=True)
    assert result.returncode == 0, result.stdout + result.stderr
    from society0.kernel.storage import StageReader
    with StageReader(root / 'runs' / 'check-001') as reader:
        assert reader.read(lambda view: view.complete_step) == 0
        assert reader.read(lambda view: view.query('SELECT count(*) FROM resource_calls')) == [(0,)]
        assert reader.read(lambda view: view.query('SELECT count(*) FROM memory_jobs')) == [(0,)]


def test_documented_restore_code_preserves_memory_and_skips_completed_browse(tmp_path, monkeypatch):
    import runpy
    from society0.kernel.runner import run_plan
    from society0.kernel.storage import StageReader
    from tests.primary.test_skill_starter import load_starter, resources
    starter = load_starter()
    root = tmp_path / 'versions' / 'v001' / 'runs'
    root.mkdir(parents=True)
    asyncio.run(run_plan(root / 'pilot-001', starter.build_plan(
        release={'commit': 'documentation-test'}, resource_plugins=resources([]))))
    requests = []
    def build_plan(**kwargs):
        assert kwargs['moments'] == (1, 2)
        kwargs.pop('model')
        kwargs.pop('embedding')
        return starter.build_plan(**kwargs, resource_plugins=resources(requests))
    monkeypatch.setattr(runpy, 'run_path', lambda path: {'build_plan': build_plan})
    monkeypatch.setenv('SOCIETY0_STUDY', str(tmp_path))
    monkeypatch.setenv('SOCIETY0_RELEASE', 'documentation-test')
    monkeypatch.setenv('SOCIETY0_LLM_OPTIONS', '{}')
    for prefix in ('SOCIETY0_LLM', 'SOCIETY0_EMBED'):
        for field, value in [('MODEL', 'unused'), ('BASE_URL', 'http://unused.invalid/v1'),
                             ('API_KEY', 'unused'), ('DIMENSIONS', '2')]:
            monkeypatch.setenv(prefix + '_' + field, value)
    text = (REFERENCES / 'runtime-quickstart.md').read_text()
    block = re.findall(r"<<'PY'\n(.*?)\nPY\n", text, re.S)[0]
    exec(compile(block, 'runtime-quickstart.md', 'exec'), {})
    with StageReader(root / 'resumed-001') as reader:
        assert reader.read(lambda view: view.complete_step) == 2
        assert reader.read(lambda view: view.query('SELECT actor FROM news_views')) == [('alice',)]
        assert reader.read(lambda view: view.query('SELECT actor,timestamp,state FROM memory_rows')) == [('alice', 1, 'ready')]
    assert len(requests) == 1
    assert '我读到尚无官方确认的地铁消息' in str(requests[0])
