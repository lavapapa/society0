"""当前 StageStore 的规模基线：原生完整点、工件和新运行继续写入。"""
import json
from pathlib import Path
import subprocess
import sys
import pytest

import apsw
from society0.kernel.storage import StageStore
from benchmarks.core_next_checkpoint_baseline import checkpoint_baseline


def test_restored_cold_body_corruption_is_rejected(tmp_path, monkeypatch):
    original = StageStore.restore
    def corrupted(source, destination, **kwargs):
        store = original(source, destination, **kwargs)
        store.transaction(lambda writer: writer.execute(
            'UPDATE cold_history SET body=? WHERE id=0', (b'corrupted',)))
        return store
    monkeypatch.setattr(StageStore, 'restore', corrupted)
    with pytest.raises(AssertionError):
        checkpoint_baseline(tmp_path / 'corrupt-history', history_rows=5, steps=2)


def test_continued_materialized_balance_corruption_is_rejected(tmp_path, monkeypatch):
    original = StageStore.prepare_readonly
    def corrupted(source, destination, **kwargs):
        reader = original(source, destination, **kwargs)
        if Path(destination).name == 'continued':
            with apsw.Connection(str(Path(destination) / 'current.sqlite')) as connection:
                connection.execute('UPDATE current_positions SET value=999 WHERE id=0')
        return reader
    monkeypatch.setattr(StageStore, 'prepare_readonly', corrupted)
    with pytest.raises(AssertionError):
        checkpoint_baseline(tmp_path / 'corrupt-continued', history_rows=5, steps=2)


def test_checkpoint_baseline_measures_real_complete_restore_and_readonly(tmp_path):
    result = checkpoint_baseline(tmp_path / 'case', history_rows=19, active_rows=3,
                                 steps=3, delta_rows=2, body_bytes=73,
                                 artifacts_per_step=2)
    assert result['verification'] == {
        'history_rows': 19, 'fact_rows': 6, 'active_rows': 3,
        'complete_step': 3, 'artifacts': 6,
        'readonly_matches': True, 'restore_matches': True,
        'continued_complete_step': 4,
    }
    assert len(result['steps']) == 3
    assert [row['facts_appended'] for row in result['steps']] == [2, 2, 2]
    assert all(row['changeset_bytes'] > 0 for row in result['steps'])
    for name in ('create', 'close', 'cold_open', 'readonly', 'restore', 'continue'):
        assert result['stages'][name]['wall_seconds'] >= 0
        assert result['stages'][name]['cpu_seconds'] >= 0
    assert result['source_disk']['root']['logical_bytes'] > 0
    assert result['source_disk']['changesets']['files'] == 3
    assert result['source_disk']['artifacts']['files'] == 6
    assert result['max_rss_bytes'] > 0
    # 报告只含数字和短标识，不把正文、业务对象或完整状态复制进去。
    encoded = json.dumps(result, ensure_ascii=False)
    assert '"body"' not in encoded
    assert '"payload"' not in encoded


def test_more_unrelated_history_does_not_change_step_work_or_restore_values(tmp_path):
    results = [checkpoint_baseline(tmp_path / str(n), history_rows=n, active_rows=2,
                                  steps=2, delta_rows=1, artifacts_per_step=0)
               for n in (8, 80)]
    assert [r['verification']['fact_rows'] for r in results] == [2, 2]
    assert [r['verification']['continued_complete_step'] for r in results] == [3, 3]
    assert [r['steps'][0]['projection_updates'] for r in results] == [1, 1]
    assert results[1]['source_disk']['root']['logical_bytes'] > results[0]['source_disk']['root']['logical_bytes']


def test_cli_runs_each_history_size_in_its_own_process_and_keeps_report(tmp_path):
    output = tmp_path / 'cli'
    command = [sys.executable, '-m', 'benchmarks.core_next_checkpoint_baseline',
               '--output', str(output), '--history', '5', '50', '--steps', '2']
    process = subprocess.run(command, cwd=Path(__file__).resolve().parents[2],
                             capture_output=True, text=True, timeout=60)
    assert process.returncode == 0, process.stdout + process.stderr
    receipt = json.loads(process.stdout)
    assert receipt['samples'] == 2
    report = json.loads(Path(receipt['report']).read_text())
    assert len(report['source']['commit']) == 40
    assert report['source']['module']
    assert [row['workload']['history_rows'] for row in report['samples']] == [5, 50]
    assert all(row['verification']['restore_matches'] for row in report['samples'])
    assert all('--worker' in row['command'] for row in report['samples'])


@pytest.mark.parametrize('setting', [
    {'history_rows': -1}, {'active_rows': 0}, {'steps': 0},
    {'delta_rows': 0}, {'body_bytes': 0}, {'artifacts_per_step': -1},
])
def test_invalid_baseline_workload_fails_before_creating_a_run(tmp_path, setting):
    path = tmp_path / 'invalid'
    with pytest.raises(ValueError):
        checkpoint_baseline(path, **setting)
    assert not path.exists()
