"""Thread 实现者对独立存储实现的交叉审查复现。"""
import pytest

from society0.incremental_checkpoint import V4CheckpointStore


def test_review_export_rejects_changed_base_checkpoint_identity(tmp_path):
    source_path = tmp_path / 'source'
    source = V4CheckpointStore(source_path)
    original = source.publish_root(({'sequence': 0, 'path': ['x'], 'operation': 'set', 'value': 1},), metadata={'run_id': 'original'})
    target = V4CheckpointStore(tmp_path / 'target')
    target.publish_root((), metadata={'run_id': 'target', 'base_checkpoint': {
        'source_root': '../source', 'step': 0, 'checkpoint_id': original['checkpoint_id'], 'branch_id': 'main'}})
    source_path.rename(tmp_path / 'old-source')
    substituted = V4CheckpointStore(source_path)
    substituted.publish_root(({'sequence': 0, 'path': ['x'], 'operation': 'set', 'value': 2},), metadata={'run_id': 'different'})
    with pytest.raises(ValueError, match='identity'):
        target.restore(0)
    with pytest.raises(ValueError, match='identity'):
        target.export_bundle(tmp_path / 'bundle')
    assert not (tmp_path / 'bundle').exists()


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['staging', 'publish'])
async def test_review_failed_pending_epoch_revokes_memory_and_keeps_last_marker(tmp_path, monkeypatch, failure):
    from society0.core_data import World
    from society0.persistence import PersistenceManager
    from society0.incremental_checkpoint import PersistenceSchema, SealedTickDelta
    from society0.state_persistence import persistent_state_schema, replaceable
    from society0 import checkpoint_records
    world = World(event_log_path=str(tmp_path / 'events.jsonl'))
    world.environment_data.update(type='plain', state={'x': 0})
    manager = PersistenceManager(str(tmp_path))
    manager.configure_v4(world, PersistenceSchema.compile(persistent_state_schema(x=replaceable()), root_path=('environment', 'state')), checkpoint_every=2)
    try:
        root = await manager.publish_root(world, object())
        await manager.publish_delta(SealedTickDelta(1, ({'sequence': 0, 'operation': 'set', 'path': ['environment', 'state', 'x'], 'value': 1},), (), write_epoch_ids=('pending-first',)), object())
        assert 'pending-first' in world._committed_memory_epoch_ids
        def fail(*args, **kwargs):
            raise OSError('injected pending epoch failure')
        if failure == 'staging':
            monkeypatch.setattr(checkpoint_records, 'write_records', fail)
        else:
            monkeypatch.setattr(manager._v4_store, 'publish', fail)
        with pytest.raises(OSError, match='injected pending'):
            await manager.publish_delta(SealedTickDelta(2, (), (), write_epoch_ids=('pending-second',)), object())
        assert 'pending-first' not in world._committed_memory_epoch_ids
        assert 'pending-second' not in world._committed_memory_epoch_ids
        assert manager._v4_store.resolve()['checkpoint_id'] == root['checkpoint_id']
        assert manager._v4_store.restore(0)['environment']['state'] == {'x': 0}
        assert not manager._v4_epoch
        assert not manager._v4_staged_records
        assert not list((tmp_path / 'checkpoints' / 'v4' / 'pending').iterdir())
    finally:
        manager.close()
        world.event_logger.close()


def test_review_shared_blocks_merge_remaps_paths_and_byte_ranges(tmp_path):
    import json
    from society0.checkpoint_records import write_records, merge_records, iter_record_bytes, iter_metadata
    records = [
        {'sequence': 0, 'path': ['a', 'x'], 'operation': 'set', 'value': '甲' * 400000},
        {'sequence': 1, 'path': ['b', 'x'], 'operation': 'set', 'value': {'n': 1}},
        {'sequence': 2, 'path': ['b', 'x'], 'operation': 'set', 'value': '乙' * 400000},
        {'sequence': 3, 'path': ['a', 'x'], 'operation': 'set', 'value': {'n': 2}},
    ]
    first, second, merged = [tmp_path / x for x in ('first.sqlite', 'second.sqlite', 'merged.sqlite')]
    write_records(first, records[:2])
    write_records(second, records[2:])
    merge_records(merged, [first, second])
    assert [m['path'] for m in iter_metadata(merged)] == [r['path'] for r in records]
    for expected in records:
        body = b''.join(iter_record_bytes(merged, expected['sequence']))
        assert json.loads(body) == expected
        # 跨共享压缩块边缘保留逐字节边界。
        start = min(1048500, max(0, len(body) - 100))
        assert b''.join(iter_record_bytes(merged, expected['sequence'], offset=start, max_bytes=200)) == body[start:start+200]
