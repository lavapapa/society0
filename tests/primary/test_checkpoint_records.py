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


def test_small_record_uses_one_bounded_c_encoding(monkeypatch):
    import society0.checkpoint_records as records
    calls = []
    original = json.dumps
    def encode(value, *args, **kwargs):
        calls.append(value)
        return original(value, *args, **kwargs)
    monkeypatch.setattr(records.json, 'dumps', encode)
    value = {'sequence': 1, 'path': ['world', 'x'], 'operation': 'set', 'value': {'汉': [1, 2, None]}}
    assert json.loads(b''.join(records._json_parts(value))) == value
    assert len(calls) == 1
    calls.clear()
    large = {'value': '汉' * 100_000}
    assert json.loads(b''.join(records._json_parts(large))) == large
    assert all(not isinstance(v, dict) and len(v) <= 8192 for v in calls)


def test_bulk_record_writer_bounds_python_sql_calls(tmp_path, monkeypatch):
    import sqlite3
    import society0.checkpoint_records as records
    original = sqlite3.connect
    calls = []
    class CountedConnection(sqlite3.Connection):
        def execute(self, statement, *args, **kwargs):
            calls.append(statement)
            return super().execute(statement, *args, **kwargs)
        def executemany(self, statement, *args, **kwargs):
            calls.append(statement)
            return super().executemany(statement, *args, **kwargs)
    monkeypatch.setattr(records.sqlite3, 'connect', lambda *a, **k: original(*a, factory=CountedConnection, **k))
    path = tmp_path / 'bulk.sqlite'
    write_records(path, ({'sequence': i, 'path': ['state', str(i)], 'operation': 'set', 'value': i} for i in range(4096)))
    assert len(calls) < 100
    assert read_page(path, path_filter=['state', '4095'])['records'][0]['value'] == 4095


def test_bulk_metadata_has_byte_budget(tmp_path, monkeypatch):
    import sqlite3
    import society0.checkpoint_records as records
    original = sqlite3.connect
    sizes = []
    class CountedConnection(sqlite3.Connection):
        def executemany(self, statement, rows):
            rows = list(rows)
            if statement.startswith('INSERT INTO staged_records'):
                sizes.append(sum(len(item.encode('utf-8')) for row in rows for item in row if isinstance(item, str)))
            return super().executemany(statement, rows)
    monkeypatch.setattr(records.sqlite3, 'connect', lambda *a, **k: original(*a, factory=CountedConnection, **k))
    write_records(tmp_path / 'bounded.sqlite', ({'sequence': i, 'path': ['state', '汉' * 1000 + str(i)],
        'operation': 'set', 'value': i} for i in range(200)))
    assert max(sizes) <= 65536


def test_pending_records_build_query_index_once_at_merge(tmp_path, monkeypatch):
    import society0.checkpoint_records as records
    calls = []
    original = records._finish_index
    monkeypatch.setattr(records, '_finish_index', lambda db: (calls.append(1), original(db))[1])
    sources = [tmp_path / 'first.sqlite', tmp_path / 'second.sqlite']
    for i, path in enumerate(sources):
        records.write_records(path, [{'sequence': i, 'path': ['same'], 'operation': 'set', 'value': i}], pending=True)
    assert calls == []
    target = tmp_path / 'merged.sqlite'
    records.merge_records(target, sources)
    assert len(calls) == 1
    page = records.read_page(target, path_filter=['same'])
    assert page['total'] == 2
    assert [row['value'] for row in page['records']] == [0, 1]


def test_private_sqlite_build_uses_one_final_durability_boundary(tmp_path, monkeypatch):
    import sqlite3
    import society0.checkpoint_records as records
    original = sqlite3.connect
    statements = []
    class TracedConnection(sqlite3.Connection):
        def execute(self, statement, *args, **kwargs):
            statements.append(statement)
            return super().execute(statement, *args, **kwargs)
    monkeypatch.setattr(records.sqlite3, 'connect', lambda *a, **k: original(*a, factory=TracedConnection, **k))
    path = tmp_path / 'private.sqlite'
    records.write_records(path, [{'path': ['x'], 'operation': 'set', 'value': 1}])
    assert 'PRAGMA journal_mode=OFF' in statements
    assert 'PRAGMA synchronous=OFF' in statements
    path.unlink()
    def fail_final_sync(fd):
        raise OSError('final fsync failed')
    monkeypatch.setattr(records.os, 'fsync', fail_final_sync)
    with pytest.raises(OSError, match='final fsync'):
        records.write_records(path, [{'path': ['x'], 'operation': 'set', 'value': 1}])
    assert not path.exists()
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize('phase', ['index', 'fsync', 'disk_full'])
def test_private_build_failure_preserves_previous_complete_checkpoint(tmp_path, monkeypatch, phase):
    import sqlite3
    import society0.checkpoint_records as records
    from society0.incremental_checkpoint import V4CheckpointStore, SealedTickDelta
    store = V4CheckpointStore(tmp_path)
    marker = store.publish_root([{'sequence': 0, 'path': ['x'], 'operation': 'set', 'value': 'old'}])
    if phase == 'index':
        monkeypatch.setattr(records, '_finish_index', lambda db: (_ for _ in ()).throw(OSError('index failed')))
    elif phase == 'fsync':
        monkeypatch.setattr(records.os, 'fsync', lambda fd: (_ for _ in ()).throw(OSError('fsync failed')))
    else:
        original = sqlite3.connect
        class FullConnection(sqlite3.Connection):
            def executemany(self, *args, **kwargs):
                raise sqlite3.OperationalError('database or disk is full')
        monkeypatch.setattr(records.sqlite3, 'connect', lambda *a, **k: original(*a, factory=FullConnection, **k))
    with pytest.raises((OSError, sqlite3.OperationalError)):
        store.publish(SealedTickDelta(1, ({'sequence': 0, 'path': ['x'], 'operation': 'set', 'value': 'new'},), ()))
    assert store.restore(0) == {'x': 'old'}
    assert json.loads((tmp_path / 'checkpoints/v4/latest.json').read_text())['checkpoint_id'] == marker['checkpoint_id']
    assert not (tmp_path / 'checkpoints/v4/complete/step_000001.json').exists()


def test_process_exit_during_private_index_does_not_publish_checkpoint(tmp_path):
    import os
    import subprocess
    import sys
    from society0.incremental_checkpoint import V4CheckpointStore
    store = V4CheckpointStore(tmp_path)
    store.publish_root([{'sequence': 0, 'path': ['x'], 'operation': 'set', 'value': 'old'}])
    code = '''import os,sys
from society0 import checkpoint_records as r
from society0.incremental_checkpoint import V4CheckpointStore,SealedTickDelta
r._finish_index=lambda db: os._exit(91)
V4CheckpointStore(sys.argv[1]).publish(SealedTickDelta(1,({'sequence':0,'path':['x'],'operation':'set','value':'new'},),()))
'''
    result = subprocess.run([sys.executable, '-c', code, str(tmp_path)], env=os.environ.copy())
    assert result.returncode == 91
    assert store.restore(0) == {'x': 'old'}
    assert not (tmp_path / 'checkpoints/v4/complete/step_000001.json').exists()


def test_page_policy_uses_known_counts_and_merge_final_count(tmp_path):
    import sqlite3
    import society0.checkpoint_records as records
    def page_size(path):
        with sqlite3.connect(path) as db:
            return db.execute('PRAGMA page_size').fetchone()[0]
    paths = []
    for part in range(2):
        path = tmp_path / f'part-{part}.sqlite'
        records.write_records(path, ({'sequence': part*2500+i, 'path': ['x'], 'operation': 'set', 'value': i}
                                    for i in range(2500)), record_count=2500, pending=True)
        assert page_size(path) == 1024
        paths.append(path)
    merged = tmp_path / 'merged.sqlite'
    records.merge_records(merged, paths)
    assert page_size(merged) == 4096
    assert records.read_page(merged, path_filter=['x'], limit=1)['total'] == 5000
    root = tmp_path / 'root.sqlite'
    records.write_records(root, iter([{'path': ['x'], 'operation': 'set', 'value': 1}]))
    assert page_size(root) == 4096


def test_root_generator_identity_does_not_depend_on_optional_metadata(tmp_path):
    from society0.incremental_checkpoint import V4CheckpointStore
    store = V4CheckpointStore(tmp_path)
    store.publish_root(({'sequence': i, 'path': [str(i)], 'operation': 'set', 'value': i} for i in range(5000)))
    assert store.restore(0)['4999'] == 4999


def test_identity_resolution_never_restores_world_and_keeps_corruption_fallback(tmp_path, monkeypatch):
    from society0.incremental_checkpoint import V4CheckpointStore, SealedTickDelta
    store = V4CheckpointStore(tmp_path)
    first = store.publish_root([{'sequence': 0, 'path': ['x'], 'operation': 'set', 'value': 'old'}], metadata={})
    second = store.publish(SealedTickDelta(1, ({'sequence': 0, 'path': ['x'], 'operation': 'set', 'value': 'new'},), ()))
    monkeypatch.setattr(V4CheckpointStore, 'restore', lambda *a, **k: (_ for _ in ()).throw(AssertionError('identity lookup materialized World')))
    assert store.resolve()['checkpoint_id'] == second['checkpoint_id']
    manifest = json.loads((tmp_path / second['manifest_file']).read_text())
    (tmp_path / manifest['replacement_file']).write_bytes(b'damaged')
    assert store.resolve()['checkpoint_id'] == first['checkpoint_id']
    with pytest.raises(ValueError, match='hash mismatch'):
        store.resolve(1)


def test_restoring_resolver_reads_manifest_chain_once(tmp_path, monkeypatch):
    from society0.incremental_checkpoint import V4CheckpointStore
    store = V4CheckpointStore(tmp_path)
    store.publish_root([{'sequence': 0, 'path': ['x'], 'operation': 'set', 'value': 1}], metadata={})
    original = store._manifest_chain
    calls = []
    def chain(step):
        calls.append(step)
        return original(step)
    monkeypatch.setattr(store, '_manifest_chain', chain)
    assert store.resolve(0, include_restored_state=True)['_restored_state'] == {'x': 1}
    assert calls == [0]


def test_v3_dictionary_stores_long_typed_keys_once_and_seeks_fences(tmp_path):
    import sqlite3
    import society0.checkpoint_records as records
    rows = [{'sequence': i, 'path': ['state', ('共同前缀-' * 30) + f'{i:05d}'], 'operation': 'set', 'value': i}
            for i in range(5000)]
    path = tmp_path / 'dictionary.sqlite'
    records.write_records(path, rows)
    with sqlite3.connect(path) as db:
        assert 'leaf' not in [row[1] for row in db.execute('PRAGMA table_info(records)')]
        assert db.execute('SELECT count(*) FROM key_blocks').fetchone()[0] > 1
    assert path.stat().st_size < 1_000_000
    for i in [0, 1, 511, 512, 1023, 1024, 4999]:
        page = records.read_page(path, path_filter=rows[i]['path'])
        assert page['total'] == 1 and page['records'] == [rows[i]]
    assert records.read_page(path, path_filter=['state', 'missing'])['total'] == 0


def test_v3_dictionary_preserves_extreme_key_and_sequence_order(tmp_path):
    import society0.checkpoint_records as records
    paths = [['scope', True], ['scope', 1], ['scope', '1'], ['scope', '汉😀' * 50000], ['other', 'a']]
    rows = [{'sequence': i, 'path': paths[i % len(paths)], 'operation': 'set', 'value': i} for i in range(15)]
    path = tmp_path / 'extreme.sqlite'
    records.write_records(path, rows)
    assert [r['path'] for r in records.iter_metadata(path)] == [r['path'] for r in rows]
    for path_filter in paths:
        page = records.read_page(path, path_filter=path_filter, max_bytes=2_000_000)
        assert page['records'] == [r for r in rows if json.dumps(r['path']) == json.dumps(path_filter)]


def test_metadata_random_key_order_decodes_each_sequential_block_once(tmp_path, monkeypatch):
    import random
    import society0.checkpoint_records as records
    keys = list(range(6000)); random.Random(71).shuffle(keys)
    rows = [{'sequence': i * 2, 'path': ['scope', str(key) + 'x' * 300], 'operation': 'set', 'value': i}
            for i, key in enumerate(keys)]
    path = tmp_path / 'metadata.sqlite'; records.write_records(path, rows)
    original = records.gzip.decompress; calls = []
    def decode(value):
        calls.append(len(value)); return original(value)
    monkeypatch.setattr(records.gzip, 'decompress', decode)
    assert [item['path'] for item in records.iter_metadata(path)] == [r['path'] for r in rows]
    assert len(calls) < 50
    assert [item['sequence'] for item in records.iter_metadata(path, after_sequence=11777)] == list(range(11778,12000,2))


def test_record_reader_reuses_one_connection_and_payload_block(tmp_path, monkeypatch):
    import society0.checkpoint_records as records
    path = tmp_path / 'reader.sqlite'
    records.write_records(path, ({'sequence': i, 'path': ['x', i], 'operation': 'set', 'value': i} for i in range(100)))
    original_open = records._open; opens=[]; decodes=[]; original_decode=records.gzip.decompress
    monkeypatch.setattr(records, '_open', lambda path: (opens.append(path), original_open(path))[1])
    monkeypatch.setattr(records.gzip, 'decompress', lambda payload: (decodes.append(len(payload)),original_decode(payload))[1])
    with records.RecordReader(path) as reader:
        for i in range(100):
            raw=b''.join(reader.iter_bytes(i))
            assert json.loads(raw)['value']==i
            assert reader.raw_bytes(i)==len(raw)
        assert reader.cached_bytes <= records.CHUNK
    assert len(opens)==len(decodes)==1


def test_read_page_stops_metadata_cursor_at_byte_budget(tmp_path, monkeypatch):
    import society0.checkpoint_records as records
    path = tmp_path / 'page.sqlite'
    records.write_records(path, ({'sequence':i,'path':['rows',str(i)],'operation':'set','value':'x'*500} for i in range(10000)))
    original = records._open
    visited = []
    class Cursor:
        def __init__(self, cursor): self.cursor=cursor
        def fetchall(self): raise AssertionError('page metadata materialized before byte budget')
        def __iter__(self):
            for row in self.cursor:
                visited.append(row[0]); yield row
    class Connection:
        def __init__(self, db): self.db=db
        def close(self): self.db.close()
        def execute(self, sql, args=()):
            cursor=self.db.execute(sql,args)
            return Cursor(cursor) if sql.startswith('SELECT sequence,path_id,operation,raw_bytes') else cursor
    monkeypatch.setattr(records, '_open', lambda path: Connection(original(path)))
    page = records.read_page(path, limit=1000000, max_bytes=512)
    assert page['next_sequence'] is not None
    assert len(visited) <= 5
