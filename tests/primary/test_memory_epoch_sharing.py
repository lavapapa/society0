from types import SimpleNamespace

from society0.agent.memory import Memory
from society0.core_data import World


class NoHistoryIteration(set):
    def __iter__(self):
        raise AssertionError('published history must not be scanned per agent')


def test_world_shares_published_epoch_membership_without_historical_copy():
    world = World.__new__(World)
    memories = [Memory.__new__(Memory) for _ in range(50)]
    world._agent_cache = {str(i): SimpleNamespace(_memory=m) for i, m in enumerate(memories)}
    published = NoHistoryIteration(str(i) for i in range(10000))
    world.set_memory_checkpoint_view(target_step=1, branch_id='main', branch_lineage=[], committed_write_epoch_ids=published)
    shared = world._committed_memory_epoch_ids
    assert all(m._committed_write_epoch_ids is shared for m in memories)
    assert '9999' in shared
    assert not hasattr(shared, 'add')
    published.add('newly-published')
    world.set_memory_checkpoint_view(target_step=2, branch_id='main', branch_lineage=[], committed_write_epoch_ids=published)
    assert all(m._is_epoch_visible({'write_epoch_id': 'newly-published'}) for m in memories)
    assert not any(m._is_epoch_visible({'write_epoch_id': 'failed'}) for m in memories)


def test_branch_membership_is_independent():
    worlds = []
    for epochs in [{'common', 'left'}, {'common', 'right'}]:
        world = World.__new__(World)
        world._agent_cache = {}
        world.set_memory_checkpoint_view(target_step=2, branch_id='branch', branch_lineage=[], committed_write_epoch_ids=epochs)
        worlds.append(world)
    assert 'right' not in worlds[0]._committed_memory_epoch_ids
    assert 'left' not in worlds[1]._committed_memory_epoch_ids


def test_pending_epochs_overlay_does_not_copy_history_and_can_be_revoked():
    from society0.agent.memory_view import PublishedEpochs
    committed = NoHistoryIteration(str(i) for i in range(10000))
    pending = {'new'}
    visible = PublishedEpochs(committed, pending)
    assert '9999' in visible and 'new' in visible
    assert len(visible) == 10001
    pending.clear()
    assert 'new' not in visible
    pending.update({'new', '9999'})
    assert len(visible) == 10001
    ordinary = PublishedEpochs({'a', 'b'}, {'b', 'c'})
    assert set(ordinary) == {'a', 'b', 'c'}
