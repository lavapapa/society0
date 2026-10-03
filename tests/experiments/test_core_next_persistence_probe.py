"""性能探针计数真实性；不以机器相关耗时设验收阈值。"""
from benchmarks.core_next_persistence_probe import durability_probe,reader_probe


def test_native_sync_probe_counts_real_full_and_normal_commits(tmp_path):
    full=durability_probe(tmp_path/'full','FULL',rounds=3)
    normal=durability_probe(tmp_path/'normal','NORMAL',rounds=3)
    assert full['short_transaction_sync_count']>normal['short_transaction_sync_count']
    assert full['rounds']==normal['rounds']==3
    assert full['messages']==normal['messages']==9
    assert full['complete_step']==normal['complete_step']==1
    assert full['component_fsync_count']>0 and normal['component_fsync_count']>0


def test_reader_probe_reuses_connection_without_changing_query_result(tmp_path):
    result=reader_probe(tmp_path/'reader',tables=10,iterations=5)
    assert result['fresh']['connections']==5
    assert result['reused']['connections']==1
    assert result['fresh']['sum']==result['reused']['sum']==35


def test_reader_reuse_preserves_outer_snapshot_and_nested_fresh_read(tmp_path):
    from benchmarks.core_next_persistence_probe import nested_reader_probe
    result=nested_reader_probe(tmp_path/'nested')
    assert result['outer']==[7,7]
    assert result['nested']==9
    assert result['next_request']==9
    assert result['connections']==2
