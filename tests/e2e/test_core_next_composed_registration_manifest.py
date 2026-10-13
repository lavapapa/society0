"""独立进程公开 CLI：登记生命周期、实际展开图与启动失败。"""
import json
import os
from pathlib import Path
import subprocess
import shutil
import sys

import pytest

from society0.kernel.storage import StageReader

ROOT = Path(__file__).resolve().parents[2]
FACTORY = 'examples.core_next.composed_registration_manifest:build'
COMMIT = '704b8fcce9a75133f3ec8ca44ab9846d367aa02a'
pytestmark = pytest.mark.e2e


def run_cli(tmp_path, case, variant, name, *, source=None):
    config = {'release': {'commit': COMMIT}, 'provider': 'scripted',
              'case': case, 'variant': variant, 'start': 1, 'end': 2}
    config_path = tmp_path / (name + '.json')
    config_path.write_text(json.dumps(config), encoding='utf-8')
    target = tmp_path / name
    command = [sys.executable, '-m', 'society0.kernel.runner', '--factory', FACTORY,
               '--config', str(config_path), '--output', str(target)]
    if source:
        command += ['--source', str(source), '--step', '1']
    result = subprocess.run(command, cwd=ROOT, env={**os.environ,
        'PYTHONPATH': str(ROOT/'src') + os.pathsep + str(ROOT),
        'SOCIETY0_RUN_REAL_E2E': '0', 'SOCIETY0_RUN_CORE_REAL': '0',
        'PYTHONDONTWRITEBYTECODE': '1'}, text=True, capture_output=True, timeout=60)
    (tmp_path / (name + '.log')).write_text(result.stdout + result.stderr, encoding='utf-8')
    if target.is_dir():
        (target/'cli-result.json').write_text(json.dumps({
            'returncode': result.returncode, 'stdout': result.stdout, 'stderr': result.stderr}), encoding='utf-8')
    return target, result


def assert_checks(checks):
    assert checks, '验收检查缺失'
    assert all(set(row) == {'id', 'passed', 'expected', 'actual', 'location'} for row in checks)
    assert all(row['passed'] for row in checks), [row for row in checks if not row['passed']]


@pytest.mark.parametrize(('case', 'variant'), [
    ('registration', 'default'), ('registration', 're-register'), ('registration', 'owned'),
    ('manifest', 'default'), ('manifest', 'alternate-tree')])
def test_public_cli_continuous_and_step_one_restore(tmp_path, case, variant):
    continuous, result = run_cli(tmp_path, case, variant, 'continuous')
    assert result.returncode == 0, result.stdout + result.stderr
    with StageReader(continuous) as reader:
        assert reader.read(lambda r: r.query('SELECT step FROM composition_observations ORDER BY step')) == [(1,), (2,)]
    from examples.core_next.composed_registration_manifest import validate, compare
    assert_checks(validate(continuous))
    restored, result = run_cli(tmp_path, case, variant, 'restored', source=continuous)
    assert result.returncode == 0, result.stdout + result.stderr
    assert_checks(validate(restored))
    assert_checks(compare(continuous, restored))
    manifest = json.loads((restored/'runner.json').read_text())
    assert manifest['source'] == {'path': str(continuous.resolve()), 'step': 1}
    assert [json.loads(line)['step'] for line in (restored/'timings.jsonl').read_text().splitlines()] == [2]
    # 比较器必须拒绝真实事实损坏，不能把同一最终状态当作全部证据。
    tampered = tmp_path/'tampered-source'
    shutil.copytree(restored, tampered)
    changed = json.loads((tampered/'runner.json').read_text())
    changed['source']['step'] = 2
    (tampered/'runner.json').write_text(json.dumps(changed))
    assert any(not row['passed'] for row in compare(continuous, tampered))


@pytest.mark.parametrize(('case', 'variant', 'error'), [
    ('registration', 'install-failure', 'ValueError'),
    ('manifest', 'write-failure', 'FileExistsError')])
def test_public_cli_precise_startup_failure_and_readable_root(tmp_path, case, variant, error):
    target, result = run_cli(tmp_path, case, variant, 'failed')
    assert result.returncode == 1, result.stdout + result.stderr
    assert json.loads(result.stdout)['error']['code'] == error
    with StageReader(target) as reader:
        assert reader.read(lambda r: r.complete_step) == 0
        assert reader.read(lambda r: r.query('SELECT step FROM composition_observations')) == []
    from examples.core_next.composed_registration_manifest import validate
    assert_checks(validate(target))
    # 任意非零及错误异常类别都必须被拒绝。
    tampered = tmp_path/'tampered-error'
    shutil.copytree(target, tampered)
    evidence = json.loads((tampered/'cli-result.json').read_text())
    evidence['stdout'] = json.dumps({'error': {'code': 'ImportError'}})
    (tampered/'cli-result.json').write_text(json.dumps(evidence))
    assert any(not row['passed'] for row in validate(tampered))


def test_same_root_different_actual_tree_and_restore(tmp_path):
    one, result = run_cli(tmp_path, 'manifest', 'default', 'one')
    assert result.returncode == 0, result.stdout
    two, result = run_cli(tmp_path, 'manifest', 'alternate-tree', 'two', source=one)
    assert result.returncode == 0, result.stdout
    left, right = [json.loads((path/'runner.json').read_text()) for path in (one, two)]
    assert left['plugin_roots'] == right['plugin_roots'] == ['composition.recipe']
    assert left['plugins'] != right['plugins']
    from examples.core_next.composed_registration_manifest import validate
    assert_checks(validate(two))
