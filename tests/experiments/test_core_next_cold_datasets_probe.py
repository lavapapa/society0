from benchmarks.core_next_cold_datasets_probe import probe


def test_real_consumer_one_batch_shared_body_and_single_readonly_copy(tmp_path):
    result=probe(tmp_path, lambda: iter([{'id':i,'body':'汉🙂'*1000} for i in range(100)]))
    assert result['records']==100 and result['all_values_equal']
    assert result['artifact_files']==1
    assert result['body_shared_with_restore']
    assert result['readonly_root_files']==0
    assert result['session_bytes']<20000
    assert result['combined_unique_allocated_bytes']<result['combined_summed_allocated_bytes']
