"""独立交叉审查：仅覆盖未参与实现的观察索引及运行状态接入。"""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from society0.incremental_checkpoint import V4CheckpointStore, SealedTickDelta
from society0.observation import ObservationReader


def publish(store, step, operations):
    return store.publish(SealedTickDelta(step, tuple({'sequence': i, **op} for i, op in enumerate(operations)), ()))


def test_review_default_cursor_continues_original_checkpoint(tmp_path):
    store = V4CheckpointStore(tmp_path)
    first = publish(store, 1, [{'operation': 'set', 'path': [key], 'value': key} for key in ['a', 'b']])
    with ObservationReader(tmp_path) as reader:
        reader.sync()
        page = reader.state_page(limit=1)
        publish(store, 2, [{'operation': 'set', 'path': ['c'], 'value': 'c'}])
        reader.sync()
        following = reader.state_page(cursor=page['next_cursor'], limit=1)
        assert following['checkpoint_id'] == first['checkpoint_id']
        assert following['items'][0]['path'] == ['b']
        assert following['total'] == 2


def test_review_runtime_status_write_failure_does_not_change_business_run(tmp_path, monkeypatch):
    from society0 import Society0
    import society0.observation as observation
    engine = Society0(save_dir=str(tmp_path), base_config={})
    engine.current_world_state = SimpleNamespace(step=1)
    monkeypatch.setattr(observation, 'write_runtime_status', lambda *a, **kw: (_ for _ in ()).throw(OSError('observer status unavailable')))
    engine._write_jsonl(tmp_path / 'events.jsonl', {'event': 'tick_completed', 'step': 1})
    assert json.loads((tmp_path / 'events.jsonl').read_text())['event'] == 'tick_completed'



def test_review_fixed_delta_index_work_does_not_scan_inactive_history(tmp_path):
    from society0.observation import _key
    def work(history, directory):
        store = V4CheckpointStore(directory)
        publish(store, 1, [{'operation': 'set', 'path': ['hot'], 'value': 1}])
        with ObservationReader(directory) as reader:
            reader.sync()
            # 同一热键的历史版本；当前记录与本次增量保持不变。
            with reader.db:
                scope = 0
                for i in range(1, history + 1):
                    reader.db.execute('INSERT INTO entries SELECT path_id,?,0,source,sequence,raw_bytes,ordinal,structural FROM entries WHERE start=1', (-i,))
                    entry_id = reader.db.execute('SELECT last_insert_rowid()').fetchone()[0]
                    reader.db.execute('INSERT INTO scopes VALUES (?,0,?,0,?)', (scope, -i, entry_id))
            publish(store, 2, [{'operation': 'set', 'path': ['hot'], 'value': 2}])
            instructions = 0
            def count():
                nonlocal instructions
                instructions += 1
                return 0
            reader.db.set_progress_handler(count, 1)
            reader.sync()
            reader.db.set_progress_handler(None, 0)
            return instructions
    small = work(10, tmp_path / 'small')
    large = work(3000, tmp_path / 'large')
    assert large < small * 3, (small, large)


def test_review_status_reader_not_blocked_by_index_cache_spill(tmp_path):
    import sqlite3
    import threading
    write_locked = threading.Event()
    release = threading.Event()
    index_dir = tmp_path / 'index'
    with ObservationReader(tmp_path, index_dir=index_dir) as reader:
        def writer():
            db = sqlite3.connect(index_dir / 'observation.sqlite')
            db.execute('PRAGMA cache_size=8')
            db.execute('BEGIN')
            for i in range(100):
                db.execute('INSERT INTO meta VALUES (?,?)', (f'spill-{i}', 'x' * 10000))
            write_locked.set()
            release.wait(2)
            db.rollback()
            db.close()
        thread = threading.Thread(target=writer)
        thread.start()
        try:
            assert write_locked.wait(2)
            reader.db.execute('PRAGMA busy_timeout=100')
            reader.status()
        finally:
            release.set()
            thread.join(3)


def test_review_source_dataset_remains_readable_through_base_fork(tmp_path):
    from society0.result_datasets import write_dataset
    source_dir, target_dir = tmp_path / 'source', tmp_path / 'target'
    source = V4CheckpointStore(source_dir)
    source.publish_root((), metadata={'run_id': 'source'})
    ref = write_dataset(source_dir, [{'answer': 42}], step=0, name='answer')
    committed = source.publish(SealedTickDelta(1, (), (), annotations={'dataset:' + ref['path']: ref}))
    target = V4CheckpointStore(target_dir)
    target.publish_root((), metadata={'run_id': 'target', 'base_checkpoint': {
        'source_root': '../source', 'step': 1, 'checkpoint_id': committed['checkpoint_id'], 'branch_id': 'main'}})
    with ObservationReader(target_dir) as reader:
        reader.sync()
        page = reader.dataset_page(ref)
        assert page['records'][0]['value'] == {'answer': 42}


def test_review_committed_intermediate_checkpoint_reports_index_pending(tmp_path):
    store = V4CheckpointStore(tmp_path)
    first = publish(store, 1, [{'operation': 'set', 'path': ['a'], 'value': 1}])
    publish(store, 2, [{'operation': 'set', 'path': ['a'], 'value': 2}])
    with ObservationReader(tmp_path) as reader:
        with pytest.raises(ValueError, match='index_pending'):
            reader.state_page(first['checkpoint_id'])


def test_review_missing_source_returns_explicit_dependency_error(tmp_path):
    source = V4CheckpointStore(tmp_path / 'source')
    first = source.publish_root(({'sequence': 0, 'path': ['a'], 'operation': 'set', 'value': 1},), metadata={'run_id': 'source'})
    target = V4CheckpointStore(tmp_path / 'target')
    target.publish_root((), metadata={'run_id': 'target', 'base_checkpoint': {
        'source_root': '../source', 'step': 0, 'checkpoint_id': first['checkpoint_id'], 'branch_id': 'main'}})
    with ObservationReader(tmp_path / 'target') as reader:
        reader.sync()
        ref = reader.state_page()['items'][0]['content_ref']
        (tmp_path / 'source').rename(tmp_path / 'moved-source')
        with pytest.raises((ValueError, FileNotFoundError), match='source_missing'):
            reader.read_content(ref)


def test_review_replaced_run_directory_rejects_old_index(tmp_path):
    run = tmp_path / 'run'
    index = tmp_path / 'index'
    first = V4CheckpointStore(run)
    first.publish_root(({'sequence': 0, 'path': ['old'], 'operation': 'set', 'value': 1},), metadata={'run_id': 'old-run'})
    with ObservationReader(run, index_dir=index) as reader:
        reader.sync()
    run.rename(tmp_path / 'archive')
    second = V4CheckpointStore(run)
    second.publish_root(({'sequence': 0, 'path': ['new'], 'operation': 'set', 'value': 2},), metadata={'run_id': 'new-run'})
    with pytest.raises(ValueError, match='index_source_mismatch'):
        with ObservationReader(run, index_dir=index) as reader:
            reader.sync()


def test_review_dataset_reference_metadata_respects_page_byte_budget(tmp_path):
    from society0.result_datasets import write_dataset
    ref = write_dataset(tmp_path, [{'text': 'x' * 10000} for _ in range(4)], step=None, name='diagnostic')
    ref['publication'] = 'run_diagnostics'
    with ObservationReader(tmp_path) as reader:
        page = reader.dataset_page(ref, max_bytes=512)
        encoded = json.dumps(page['records'], ensure_ascii=False, separators=(',', ':')).encode()
        assert len(encoded) <= 512


def test_review_normalized_scopes_preserve_ancestor_versions_and_same_epoch(tmp_path):
    store = V4CheckpointStore(tmp_path)
    old = publish(store, 1, [
        {'operation': 'set', 'path': ['parent'], 'value': {}},
        {'operation': 'set', 'path': ['parent', 'a'], 'value': 1},
        {'operation': 'set', 'path': ['parent', 'b'], 'value': 2},
    ])
    with ObservationReader(tmp_path) as reader:
        reader.sync()
        initial = reader.state_page(old['checkpoint_id'], ['parent'], limit=1)
        new = publish(store, 2, [
            {'operation': 'delete', 'path': ['parent']},
            {'operation': 'set', 'path': ['parent'], 'value': {}},
            {'operation': 'set', 'path': ['parent', 'a'], 'value': 3},
            {'operation': 'set', 'path': ['parent', 'a'], 'value': 4},
            {'operation': 'set', 'path': ['parent', 'c'], 'value': 5},
            {'operation': 'delete', 'path': ['parent', 'c']},
        ])
        reader.sync()
        def collect(checkpoint, cursor=None):
            items = []
            while True:
                page = reader.state_page(checkpoint, ['parent'], cursor=cursor, limit=1)
                items.extend(page['items'])
                cursor = page['next_cursor']
                if cursor is None:
                    return items, page['total']
        remaining, total = collect(old['checkpoint_id'], initial['next_cursor'])
        historical = initial['items'] + remaining
        assert total == len(historical) == 3
        assert [(x['path'], x['value']) for x in historical] == [(['parent'], {}), (['parent', 'a'], 1), (['parent', 'b'], 2)]
        current, total = collect(new['checkpoint_id'])
        assert total == len(current) == 2
        assert [(x['path'], x['value']) for x in current] == [(['parent'], {}), (['parent', 'a'], 4)]


def test_review_current_page_snapshot_survives_concurrent_index_commit(tmp_path, monkeypatch):
    store = V4CheckpointStore(tmp_path)
    first = publish(store, 1, [{'operation': 'set', 'path': ['hot'], 'value': 'old'}])
    with ObservationReader(tmp_path, index_dir=tmp_path / 'shared-index') as reader, ObservationReader(tmp_path, index_dir=tmp_path / 'shared-index') as writer:
        reader.sync()
        publish(store, 2, [{'operation': 'set', 'path': ['hot'], 'value': 'new'}])
        original_status = reader.status
        def status_then_concurrent_commit():
            result = original_status()
            writer.sync()
            return result
        monkeypatch.setattr(reader, 'status', status_then_concurrent_commit)
        page = reader.state_page()
        assert page['checkpoint_id'] == first['checkpoint_id']
        assert page['items'][0]['value'] == 'old'


def test_review_same_epoch_typed_paths_delete_recreate_counts(tmp_path):
    store = V4CheckpointStore(tmp_path)
    first = publish(store, 1, [
        {'operation': 'set', 'path': ['rows'], 'value': {}},
        {'operation': 'set', 'path': ['rows', 1], 'value': 'integer'},
        {'operation': 'set', 'path': ['rows', '1'], 'value': 'string'},
    ])
    with ObservationReader(tmp_path) as reader:
        reader.sync()
        second = publish(store, 2, [
            {'operation': 'delete', 'path': ['rows', 1]},
            {'operation': 'set', 'path': ['rows', 1], 'value': 'new integer'},
            {'operation': 'set', 'path': ['rows', '1'], 'value': 'new string'},
            {'operation': 'set', 'path': ['rows', '1'], 'value': 'last string'},
        ])
        reader.sync()
        current = reader.state_page(path=['rows'])
        old = reader.state_page(first['checkpoint_id'], path=['rows'])
        assert current['total'] == old['total'] == 3
        assert {json.dumps(i['path']): i['value'] for i in current['items']} == {
            '["rows"]': {}, '["rows", 1]': 'new integer', '["rows", "1"]': 'last string'}
        assert {json.dumps(i['path']): i['value'] for i in old['items']} == {
            '["rows"]': {}, '["rows", 1]': 'integer', '["rows", "1"]': 'string'}
        reader.prepare_state(first['checkpoint_id'], path=['rows'])
        assert reader.state_page(first['checkpoint_id'], path=['rows'])['items'] == old['items']
