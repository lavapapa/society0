"""实验合同先行：这些用例不导入正式产品。"""
import importlib.util
import sys
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location('core_next_storage', Path(__file__).parents[2] / 'benchmarks/core_next_storage.py')
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
Store = module.Store


def test_scope_page_is_lazy_and_revision_is_fixed(tmp_path):
    with Store(tmp_path) as store:
        store.seed(1000)
        with store.view('alice') as view:
            first = view.page(limit=3)
            assert first['total'] == 32
            assert [r['id'] for r in first['rows']] == [0, 1, 2]
            store.stage([(0, 999)], [{'kind': 'paid', 'id': 0}])
            assert view.page(limit=3)['rows'][0]['value'] == 0
            with store.view('alice') as newer:
                assert newer.page(limit=3)['rows'][0]['value'] == 999
                with pytest.raises(ValueError, match='cursor'):
                    newer.page(limit=3, cursor=first['next'])
            with store.view('bob') as other:
                with pytest.raises(ValueError, match='cursor'):
                    other.page(cursor=first['next'])
            cursor, seen = None, []
            while True:
                page = view.page(limit=7, cursor=cursor)
                seen.extend(r['id'] for r in page['rows'])
                cursor = page['next']
                if cursor is None:
                    break
            assert seen == list(range(32))


@pytest.mark.parametrize('failure', ['after_update', 'after_file', 'before_commit'])
def test_failed_stage_keeps_complete_sql_and_jsonl_boundary(tmp_path, failure):
    with Store(tmp_path) as store:
        store.seed(1000)
        store.stage([(0, 10)], [{'id': 0, 'amount': 10}])
        complete = store.complete()
        with pytest.raises(RuntimeError, match=failure):
            store.stage([(0, 20)], [{'id': 0, 'amount': 20}], fail_at=failure)
        assert store.complete() == complete
    with Store(tmp_path) as restored:
        assert restored.complete() == complete
        assert list(restored.facts()) == [{'id': 0, 'amount': 10}]
        with restored.view('alice') as view:
            assert view.page(limit=1)['rows'][0]['value'] == 10


def test_history_growth_does_not_scan_history_or_enlarge_delta(tmp_path):
    measurements = []
    for n in (1000, 10000):
        with Store(tmp_path / str(n)) as store:
            store.seed(n)
            measurements.append(store.measure_activity())
    assert measurements[1]['query_vm_steps'] < measurements[0]['query_vm_steps'] * 1.2
    assert measurements[1]['stage_vm_steps'] < measurements[0]['stage_vm_steps'] * 1.2
    assert measurements[1]['delta_bytes'] == measurements[0]['delta_bytes']
    assert measurements[1]['rows_returned'] == 16


def test_compression_is_ordered_correct_and_bounded(tmp_path):
    path = tmp_path / 'blocks'
    module.make_blocks(path, blocks=12, block_bytes=65536)
    serial = module.compress_probe(path, workers=0, block_bytes=65536, window=4)
    parallel = module.compress_probe(path, workers=2, block_bytes=65536, window=4)
    assert serial['compressed_bytes'] == parallel['compressed_bytes']
    assert serial['verified_blocks'] == parallel['verified_blocks'] == 12
    assert parallel['max_pending_raw_bytes'] <= 4 * 65536
    assert parallel['max_pending_tasks'] <= 4


def test_cursor_is_bound_to_store_identity(tmp_path):
    with Store(tmp_path / 'one') as first, Store(tmp_path / 'two') as second:
        first.seed(1000)
        second.seed(1000)
        with first.view('alice') as a, second.view('alice') as b:
            with pytest.raises(ValueError, match='cursor'):
                b.page(cursor=a.page(limit=1)['next'])


def test_active_index_and_count_change_in_same_stage(tmp_path):
    with Store(tmp_path) as store:
        store.seed(1000)
        store.stage([], [], active_changes=[(0, False), (100, True)])
        with store.view('alice') as view:
            page = view.page(limit=40)
            assert page['total'] == 32
            assert [r['id'] for r in page['rows']] == list(range(1, 32)) + [100]
        with pytest.raises(RuntimeError):
            store.stage([], [], active_changes=[(1, False)], fail_at='before_commit')
        with store.view('alice') as view:
            assert view.page()['total'] == 32


def test_commit_ack_failure_does_not_undo_completed_revision(tmp_path):
    with Store(tmp_path) as store:
        store.seed(1000)
        with pytest.raises(RuntimeError, match='after_commit'):
            store.stage([(0, 99)], [{'id': 0}], fail_at='after_commit')
    with Store(tmp_path) as store:
        assert store.complete()[0] == 1
        assert list(store.facts()) == [{'id': 0}]


def test_native_transaction_visibility_and_long_reader_cost():
    result = module.transaction_probe()
    assert result['uncommitted_reader_value'] == 0
    assert result['writer_value_before_rollback'] == 20
    assert result['value_after_rollback'] == 0
    assert result['current_after_stage_commit'] == 21
    assert result['recoverable_value_not_in_current'] is True
    assert result['pinned_checkpoint'][1] > result['pinned_checkpoint'][2]
    assert result['released_wal_bytes'] == 0


@pytest.mark.parametrize('phase,revision', [('after_file', 0), ('before_commit', 0), ('after_commit', 1)])
def test_process_exit_boundary(tmp_path, phase, revision):
    import subprocess
    with Store(tmp_path) as store:
        store.seed(1000)
    code = '''import importlib.util,sys
s=importlib.util.spec_from_file_location('probe',sys.argv[1]); m=importlib.util.module_from_spec(s); s.loader.exec_module(m)
with m.Store(sys.argv[2]) as store:
 store.stage([(0,99)],[{'id':0}],fail_at='exit_'+sys.argv[3])
'''
    child = subprocess.run([sys.executable, '-c', code, module.__file__, str(tmp_path), phase], capture_output=True)
    assert child.returncode == 73, child.stderr.decode()
    with Store(tmp_path) as store:
        assert store.complete()[0] == revision
        with store.view('alice') as view:
            assert view.page(limit=1)['rows'][0]['value'] == (99 if revision else 0)
        if revision:
            assert list(store.facts()) == [{'id': 0}]


@pytest.mark.skipif(importlib.util.find_spec('apsw') is None, reason='Session研究使用独立/tmp/core-next-session-venv，正式依赖未变')
def test_native_session_across_transactions_rollback_and_streaming(tmp_path):
    result = module.session_probe('split', body_bytes=32768, rows=4)
    assert result['equal_after_apply']
    assert result['rolled_back_row_value'] == 0
    assert result['committed_updates'] == 2
    assert result['session_survives_transactions']
    assert result['changeset_rows'] == 1
    assert result['second_step_equal']


@pytest.mark.skipif(importlib.util.find_spec('apsw') is None, reason='Session研究使用独立临时venv')
def test_native_session_wide_row_capture_is_not_bounded_by_small_change():
    wide = module.session_probe('wide', body_bytes=65536, rows=4)
    split = module.session_probe('split', body_bytes=65536, rows=4)
    assert wide['capture_memory_bytes'] > split['capture_memory_bytes'] + 65536
    assert wide['changeset_bytes'] < 1024
    assert split['changeset_bytes'] < 1024
    assert wide['equal_after_apply'] and split['equal_after_apply']


@pytest.mark.skipif(importlib.util.find_spec('apsw') is None, reason='Session研究使用独立临时venv')
def test_native_session_json_cell_update_and_append_payload():
    huge = module.session_probe('json', body_bytes=65536, rows=4)
    append = module.session_probe('append', body_bytes=65536, rows=4)
    assert huge['changeset_bytes'] > 2 * 65536
    assert append['changeset_bytes'] > 65536
    assert huge['equal_after_apply'] and append['equal_after_apply']


@pytest.mark.skipif(importlib.util.find_spec('apsw') is None, reason='Session研究使用独立临时venv')
@pytest.mark.parametrize('phase,restored', [('before_export', 2), ('after_export', 2), ('after_marker', 3)])
def test_session_process_crash_rebuilds_only_complete_chain(phase, restored):
    result = module.session_recovery_probe(phase)
    assert result['child_exit'] == 73
    assert result['dirty_current'] == 3
    assert result['restored'] == restored
    assert result['fresh_session_empty']


@pytest.mark.skipif(importlib.util.find_spec('apsw') is None, reason='Session研究使用独立临时venv')
def test_session_captures_only_attached_connection_and_schema_is_required(tmp_path):
    import apsw
    path = str(tmp_path / 'current.sqlite')
    writer = apsw.Connection(path)
    writer.execute('CREATE TABLE records(id INTEGER PRIMARY KEY,value INTEGER); INSERT INTO records VALUES(1,0)')
    session = apsw.Session(writer, 'main')
    session.attach()
    other = apsw.Connection(path)
    other.execute('UPDATE records SET value=1 WHERE id=1')
    assert session.changeset() == b''
    writer.execute('UPDATE records SET value=2 WHERE id=1')
    missing = apsw.Connection(':memory:')
    apsw.Changeset.apply(session.changeset(), missing)
    assert missing.execute('SELECT count(*) FROM sqlite_master').fetchone()[0] == 0
    session.close()
    other.close()
    missing.close()
    writer.close()
