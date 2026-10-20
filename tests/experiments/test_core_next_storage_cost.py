"""成本计数须跟随实际调用，并在失败/异步拒绝后释放进程级仪器。"""
import asyncio
import json
from pathlib import Path

import apsw
import pytest

from society0.kernel import storage
from benchmarks.core_next_storage_cost import CostProbe, storage_cost


def test_counts_follow_actual_schema_and_stream_calls(tmp_path):
    small = storage_cost(tmp_path / 'small', mode='profile', history_rows=3,
                         steps=2, delta_rows=2, extra_tables=0)
    wide = storage_cost(tmp_path / 'wide', mode='profile', history_rows=3,
                        steps=2, delta_rows=2, extra_tables=7)
    for result in (small, wide):
        complete = result['costs']['complete']
        restore = result['costs']['restore']
        assert complete['session_create_count'] == 2
        assert complete.get('tables_count', 0) == 0
        assert complete.get('schema_count', 0) == 0
        assert complete.get('table_info_count', 0) == 0
        assert result['costs']['cold_open']['tables_count'] == 1
        assert result['costs']['cold_open']['schema_count'] == 2
        assert complete['export_count'] == 2
        assert complete['export_bytes'] > 0
        assert complete['session_memory_samples'] == 2
        assert complete['session_memory_max_bytes'] > 0
        assert complete['python_fsync_count'] == 8
        assert complete['directory_sync_count'] == 4
        assert complete['descriptor_publish_count'] == 2
        assert complete['artifact_references'] == 0
        assert complete['compression_write_count'] >= 4
        assert complete['compression_finish_count'] == 2
        assert result['costs']['cold_open']['descriptor_read_count'] == 4
        assert restore['descriptor_read_count'] == 6
        assert restore['apply_count'] == 2
        assert restore['apply_read_bytes'] == complete['export_bytes']
        assert restore['apply_read_count'] >= 2
        assert restore['compressed_read_bytes'] == result['source_disk']['changesets']['logical_bytes']
        assert restore['backup_count'] == 2
        assert restore['backup_bytes'] > 0
        assert result['verification']['restore_matches']
        assert result['verification']['continued_complete_step'] == 3
    assert wide['costs']['complete']['session_attach_count'] - small['costs']['complete']['session_attach_count'] == 14
    assert wide['costs']['cold_open']['table_info_count'] - small['costs']['cold_open']['table_info_count'] == 7
    assert 'body' not in small['costs']


def test_table_info_counter_observes_extra_native_sql(tmp_path, monkeypatch):
    original = storage._tables
    def extra_query(connection):
        connection.execute('PRAGMA table_info("current_positions")').fetchall()
        return original(connection)
    monkeypatch.setattr(storage, '_tables', extra_query)
    result = storage_cost(tmp_path / 'extra-query', mode='profile', history_rows=2, steps=2)
    opened = result['costs']['cold_open']
    assert opened['tables_returned'] == 3
    assert opened['table_info_count'] == 4
    assert result['costs']['complete'].get('table_info_count', 0) == 0


def test_disabled_export_is_visible_in_counts(tmp_path, monkeypatch):
    original = apsw.Session
    class NoExport:
        def __init__(self, *args):
            self.native = original(*args)
        def __getattr__(self, name):
            return getattr(self.native, name)
        def changeset_stream(self, sink):
            pass
    monkeypatch.setattr(apsw, 'Session', NoExport)
    with pytest.raises(AssertionError):
        storage_cost(tmp_path / 'broken', mode='profile', history_rows=2, steps=1)


def test_large_unchanged_inline_body_is_charged_to_native_session(tmp_path):
    results = {layout: storage_cost(tmp_path / layout, mode='profile', history_rows=2,
                                   steps=2, delta_rows=2, active_rows=2,
                                   position_body_bytes=65536, position_body_layout=layout)
               for layout in ('inline', 'detached')}
    inline, detached = (results[layout]['costs']['complete'] for layout in ('inline', 'detached'))
    assert inline['session_memory_max_bytes'] > detached['session_memory_max_bytes'] + 2 * 65536
    # 未修改的正文由 Session 保存旧行，但不进入净列变更导出；允许表头的列掩码差异。
    assert abs(inline['export_bytes'] - detached['export_bytes']) < 32
    assert all(result['verification']['restore_matches'] for result in results.values())


def test_timing_run_has_no_instruments(tmp_path):
    result = storage_cost(tmp_path / 'plain', mode='timing', history_rows=2, steps=1)
    assert result['costs'] == {}
    assert result['instrumentation']['enabled'] is False
    assert 'payload' not in json.dumps(result)


@pytest.mark.parametrize('failure', ['fault', 'async'])
def test_patch_and_default_vfs_exit_on_failure(tmp_path, failure):
    def instruments():
        return (apsw.Session, apsw.Changeset, apsw.Connection, storage._tables,
                storage.StageStore._artifacts, storage.os.fsync, Path.read_text,
                storage._json, storage.zstd.ZstdFile, apsw.vfs_names()[0])
    original = instruments()
    with storage.StageStore.create(tmp_path / 'store', ['CREATE TABLE item(id INTEGER PRIMARY KEY)']) as store:
        with pytest.raises((RuntimeError, TypeError)):
            with CostProbe() as probe:
                with probe.stage('complete'):
                    if failure == 'fault':
                        def fail(phase):
                            raise RuntimeError('injected publication fault')
                        store._fault = fail
                        store.complete(1)
                    else:
                        async def callback(writer):
                            await asyncio.sleep(0)
                        store.transaction(callback)
        assert instruments() == original
        assert 'core-next-sync-probe' not in apsw.vfs_names()
