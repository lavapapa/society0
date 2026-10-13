"""独立进程公开规则计划与只读目录/工件验收。"""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]
FACTORY = 'examples.core_next.composed_directory_artifacts:build'
MODULE = 'examples.core_next.composed_directory_artifacts'


def execute(command, log):
    env = {key: value for key, value in os.environ.items() if key.lower() not in
           ('all_proxy', 'http_proxy', 'https_proxy', 'no_proxy')}
    env.update(PYTHONPATH=f'{ROOT / "src"}:{ROOT}', PYTHONDONTWRITEBYTECODE='1',
               SOCIETY0_RUN_REAL_E2E='0', SOCIETY0_RUN_CORE_REAL='0')
    result = subprocess.run([sys.executable, *command], cwd=ROOT, env=env,
                            text=True, capture_output=True, timeout=90)
    log.write_text(result.stdout + result.stderr, encoding='utf-8')
    assert result.returncode == 0, log.read_text(encoding='utf-8')
    return result.stdout


@pytest.mark.e2e
@pytest.mark.parametrize(('case', 'variant'), [
    ('actor-directory', 'default'), ('actor-directory', 'readonly'),
    ('actor-directory', 'persona'), ('artifact-read', 'default'),
])
def test_public_directory_and_artifact_consumers_restore(tmp_path, case, variant):
    commit = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=ROOT,
                            text=True, capture_output=True, check=True).stdout.strip()
    config = tmp_path / 'config.json'
    config.write_text(json.dumps({'release': {'commit': commit}, 'case': case,
                                 'variant': variant, 'provider': 'scripted',
                                 'start': 1, 'end': 2}), encoding='utf-8')
    continuous, restored = tmp_path / 'continuous', tmp_path / 'restored'
    base = ['-m', 'society0.kernel.runner', '--factory', FACTORY, '--config', str(config)]
    execute([*base, '--output', str(continuous)], tmp_path / 'continuous.log')
    execute([*base, '--output', str(restored), '--source', str(continuous), '--step', '1'],
            tmp_path / 'restored.log')
    for name, path in [('continuous', continuous), ('restored', restored)]:
        script = (f'from {MODULE} import validate; import json; '
                  f'print(json.dumps(validate({str(path)!r}), ensure_ascii=False))')
        checks = json.loads(execute(['-c', script], tmp_path / f'{name}-validation.log'))
        assert checks and all(check['passed'] for check in checks), checks
        assert {'run.completed', 'run.complete-step', 'run.no-model-requests'} <= {
            check['id'] for check in checks}
    script = (f'from {MODULE} import compare; import json; '
              f'print(json.dumps(compare({str(continuous)!r}, {str(restored)!r}), ensure_ascii=False))')
    checks = json.loads(execute(['-c', script], tmp_path / 'comparison.log'))
    assert checks and all(check['passed'] for check in checks), checks
    first = json.loads((continuous / 'runner.json').read_text(encoding='utf-8'))
    second = json.loads((restored / 'runner.json').read_text(encoding='utf-8'))
    assert second['run_id'] != first['run_id']
    assert second['source'] == {'path': str(continuous.resolve()), 'step': 1}
    assert second['contract']['configuration']['case'] == case
    assert second['contract']['configuration']['variant'] == variant
