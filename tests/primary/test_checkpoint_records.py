import json
import pytest
from society0.checkpoint_records import write_records, iter_records, read_page, iter_record_bytes


def test_streaming_records_page_and_large_reference(tmp_path):
    path = tmp_path / 'records.sqlite'
    records = ({'sequence': i, 'path': ['x', str(i)], 'operation': 'set', 'value': '中' * (100_000 if i == 2 else 3)} for i in range(4))
    assert write_records(path, records) == 4
    page = read_page(path, limit=4, max_bytes=1000)
    assert page['total'] == 4
    assert [r['sequence'] for r in page['records']] == [0, 1, 2, 3]
    assert page['records'][2]['record_ref']['sequence'] == 2
    full = json.loads(b''.join(iter_record_bytes(path, 2)))
    assert len(full['value']) == 100_000
    assert list(iter_records(path))[2] == full
    assert read_page(path, after_sequence=1, limit=1)['next_sequence'] == 2
    assert read_page(path, path_filter=['x', '3'])['total'] == 1


def test_read_missing_never_creates_database(tmp_path):
    missing = tmp_path / 'missing.sqlite'
    with pytest.raises(Exception):
        read_page(missing)
    assert not missing.exists()


def test_store_records_are_indexed_and_queryable(tmp_path):
    from society0.incremental_checkpoint import V4CheckpointStore, SealedTickDelta
    store = V4CheckpointStore(tmp_path)
    marker = store.publish(SealedTickDelta(step=1, replacements=({'sequence': 0, 'path': ['x'], 'operation': 'set', 'value': 9},), appends=()))
    assert store.read_operations(marker['checkpoint_id'])['records'][0]['value'] == 9
    assert store.restore(1) == {'x': 9}


def test_root_entries_are_consumed_lazily(tmp_path):
    from society0.incremental_checkpoint import V4CheckpointStore
    store = V4CheckpointStore(tmp_path)
    def entries():
        yield {'sequence': 0, 'path': ['x'], 'operation': 'set', 'value': 9}
    marker = store.publish_root(entries(), metadata={'run_id': 'test'})
    assert store.restore(0) == {'x': 9}


def test_staged_records_merge_preserves_global_sequence(tmp_path):
    from society0.checkpoint_records import merge_records
    first, second, merged = (tmp_path / name for name in ('first.db', 'second.db', 'merged.db'))
    write_records(first, [{'sequence': 3, 'path': ['x'], 'operation': 'set', 'value': 1}])
    write_records(second, [{'sequence': 0, 'path': ['x'], 'operation': 'set', 'value': 2}], sequence_offset=4)
    assert merge_records(merged, [first, second]) == 2
    assert [row['sequence'] for row in iter_records(merged)] == [3, 4]


def test_base_checkpoint_root_reuses_history_and_applies_initialization_changes(tmp_path):
    from society0.incremental_checkpoint import V4CheckpointStore
    source = V4CheckpointStore(tmp_path / 'source')
    marker = source.publish_root(({'sequence': 0, 'path': ['x'], 'operation': 'set', 'value': 8},), metadata={'run_id': 'a'})
    target = V4CheckpointStore(tmp_path / 'target')
    target.publish_root(({'sequence': 0, 'path': ['y'], 'operation': 'set', 'value': 9},),
                        metadata={'run_id': 'b', 'base_checkpoint': {'source_root': '../source', 'step': 0,
                                  'branch_id': 'main', 'checkpoint_id': marker['checkpoint_id']}})
    assert target.restore(0) == {'x': 8, 'y': 9}


def test_large_record_range_reads_only_intersecting_chunks(tmp_path, monkeypatch):
    import society0.checkpoint_records as records
    path = tmp_path / 'range.sqlite'
    write_records(path, [{'sequence': 0, 'path': ['x'], 'operation': 'set', 'value': 'z' * 2_000_000}])
    whole = b''.join(iter_record_bytes(path, 0))
    calls = []
    original = records.gzip.decompress
    def decompress(data):
        calls.append(len(data))
        return original(data)
    monkeypatch.setattr(records.gzip, 'decompress', decompress)
    tail = b''.join(iter_record_bytes(path, 0, offset=len(whole)-50, max_bytes=50))
    assert tail == whole[-50:]
    assert len(calls) == 1


def test_bundle_rewrites_dependency_and_restores_without_sources(tmp_path):
    from society0.incremental_checkpoint import V4CheckpointStore
    source = V4CheckpointStore(tmp_path / 'source')
    marker = source.publish_root(({'sequence': 0, 'path': ['x'], 'operation': 'set', 'value': 8},), metadata={'run_id': 'a'})
    target = V4CheckpointStore(tmp_path / 'target')
    target.publish_root((), metadata={'run_id': 'b', 'base_checkpoint': {'source_root': '../source', 'step': 0,
                       'branch_id': 'main', 'checkpoint_id': marker['checkpoint_id']}})
    bundle = tmp_path / 'bundle'
    target.export_bundle(bundle)
    (tmp_path / 'source').rename(tmp_path / 'source-offline')
    (tmp_path / 'target').rename(tmp_path / 'target-offline')
    assert V4CheckpointStore(bundle).restore(0) == {'x': 8}


def test_restore_bundle_requires_offline_memory_writer(tmp_path, monkeypatch):
    from society0.incremental_checkpoint import V4CheckpointStore
    import os
    store = V4CheckpointStore(tmp_path / 'source')
    store.publish_root((), metadata={'run_id': 'a'}, memory_view={'write_epoch_ids': ['memory-1']})
    chroma = store.root / 'chroma_store'
    chroma.mkdir()
    (chroma / 'database').write_bytes(b'closed memory fixture')
    with pytest.raises(RuntimeError, match='finished offline'):
        store.export_bundle(tmp_path / 'rejected', mode='restore')
    (store.root / 'runtime-status.json').write_text(json.dumps({'phase': 'run_completed', 'producer_pid': 987654}))
    monkeypatch.setattr(os, 'kill', lambda *_: (_ for _ in ()).throw(ProcessLookupError()))
    bundle = store.export_bundle(tmp_path / 'restorable', mode='restore')
    assert (bundle / 'chroma_store' / 'database').read_bytes() == b'closed memory fixture'
    assert json.loads((bundle / 'bundle.json').read_text())['full_restore'] is True


def test_fork_does_not_materialize_world(tmp_path, monkeypatch):
    from society0.incremental_checkpoint import V4CheckpointStore
    store = V4CheckpointStore(tmp_path)
    store.publish_root((), metadata={'run_id': 'a'})
    monkeypatch.setattr(store, 'restore', lambda *_: (_ for _ in ()).throw(AssertionError('fork materialized World')))
    assert store.fork('other', step=0).restore(0) == {}


def test_json_validation_never_encodes_container_or_large_string(monkeypatch):
    from society0.incremental_checkpoint import _validate_json_value
    original = json.dumps
    def reject_large(value, *args, **kwargs):
        if isinstance(value, (dict, list, tuple)) or (isinstance(value, str) and len(value) > 100):
            raise AssertionError('validator materialized full JSON')
        return original(value, *args, **kwargs)
    monkeypatch.setattr(json, 'dumps', reject_large)
    _validate_json_value({'large': 'x' * 1_000_000, 'values': [None, True, 3, 4.5]})
    for invalid in ({'v': float('nan')}, {('tuple',): 1}, {'v': object()}):
        with pytest.raises(TypeError):
            _validate_json_value(invalid)
    cycle = []
    cycle.append(cycle)
    with pytest.raises(TypeError):
        _validate_json_value(cycle)
