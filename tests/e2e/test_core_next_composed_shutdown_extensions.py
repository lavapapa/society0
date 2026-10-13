"""E2E-18/20：公开 CLI、独立进程、原生工件及完整点恢复。"""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from examples.core_next.composed_shutdown_extensions import compare, validate

pytestmark = pytest.mark.e2e
ROOT = Path(__file__).resolve().parents[2]
FACTORY = 'examples.core_next.composed_shutdown_extensions:build'


def launch(tmp_path, case, variant, *, source=None, end=2, mode='decision'):
    label = f'{case}-{variant}-{mode}-' + ('restored' if source else 'continuous')
    config = tmp_path / (label + '.json')
    config.write_text(json.dumps({'release': {'commit': '704b8fcce9a75133f3ec8ca44ab9846d367aa02a'},
        'provider': 'scripted', 'case': case, 'variant': variant, 'start': 1, 'end': end, 'mode': mode}))
    output = tmp_path / label
    command = [sys.executable, '-m', 'society0.kernel.runner', '--factory', FACTORY,
        '--config', str(config), '--output', str(output)]
    if source:
        command += ['--source', str(source), '--step', '1']
    env = {**os.environ, 'PYTHONPATH': f'{ROOT}/src:{ROOT}', 'PYTHONDONTWRITEBYTECODE': '1',
        'SOCIETY0_RUN_REAL_E2E': '0', 'SOCIETY0_RUN_CORE_REAL': '0', 'ANONYMIZED_TELEMETRY': 'False'}
    for key in ('ALL_PROXY', 'HTTP_PROXY', 'HTTPS_PROXY', 'NO_PROXY', 'all_proxy', 'http_proxy', 'https_proxy', 'no_proxy'):
        env.pop(key, None)
    result = subprocess.run(command, env=env, cwd=ROOT, capture_output=True, text=True, timeout=90)
    (tmp_path / (label + '.log')).write_text(result.stdout + result.stderr)
    (tmp_path / (label + '-exit.json')).write_text(json.dumps({'exit_code': result.returncode}))
    return output, result


@pytest.mark.parametrize('variant', ['runtime-first', 'worker-first', 'runtime-error', 'worker-error'])
def test_shutdown_public_cli_cancels_pending_step_then_closes_all(tmp_path, variant):
    output, result = launch(tmp_path, 'shutdown', variant)
    assert result.returncode != 0, result.stdout
    checks = validate(output)
    assert checks and all(check['passed'] for check in checks), checks
    events = json.loads((output / 'shutdown-exit.json').read_text())
    assert events[-1]['event'] == 'journal.close'
    assert all(event['event'] != 'phase.done' or event['detail']['number'] != 2 for event in events)


@pytest.mark.parametrize('variant', ['default', 'memory-write', 'memory-recall', 'memory-tools', 'memory-all', 'runtime-replacement'])
def test_extensions_public_cli_and_cross_process_restore(tmp_path, variant):
    continuous, result = launch(tmp_path, 'extensions', variant)
    assert result.returncode == 0, result.stdout + result.stderr
    checks = validate(continuous)
    assert all(check['passed'] for check in checks), checks
    restored, result = launch(tmp_path, 'extensions', variant, source=continuous)
    assert result.returncode == 0, result.stdout + result.stderr
    checks = validate(restored) + compare(continuous, restored)
    assert all(check['passed'] for check in checks), checks
    left = json.loads((continuous / 'runner.json').read_text())
    right = json.loads((restored / 'runner.json').read_text())
    assert left['run_id'] != right['run_id'] and right['source']['step'] == 1


def test_interview_memory_write_boundary_uses_real_driver(tmp_path):
    output, result = launch(tmp_path, 'extensions', 'memory-all', mode='interview')
    assert result.returncode == 0, result.stdout + result.stderr
    assert all(check['passed'] for check in validate(output))


def test_readonly_validator_rejects_damaged_cleanup_artifact(tmp_path):
    output, result = launch(tmp_path, 'shutdown', 'runtime-first')
    assert result.returncode != 0
    artifact = output / 'shutdown-exit.json'
    rows = json.loads(artifact.read_text())
    rows = [row for row in rows if row['event'] != 'worker.drained']
    artifact.write_text(json.dumps(rows))
    checks = validate(output)
    assert any(not check['passed'] for check in checks)
