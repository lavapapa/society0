from benchmarks.core_next_artifact_link_probe import probe

def test_immutable_link_survives_source_removal_and_keeps_one_allocation(tmp_path):
    result=probe(tmp_path,blocks=2)
    assert result['same_inode']
    assert result['link_count_before_source_delete']==2
    assert result['target_values_equal_after_source_delete']
    assert result['unique_allocated_bytes']<result['summed_st_blocks_bytes']
