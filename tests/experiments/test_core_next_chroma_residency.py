from benchmarks.core_next_chroma_residency import probe


def test_real_chroma_probe_preserves_vectors_and_records_actual_backend(tmp_path):
    result=probe(tmp_path/'index',count=120,dimensions=16,batch_size=40)
    assert result['count']==120 and result['query_first']=='vector-3'
    assert result['vector_values_equal']
    assert result['backend'].endswith('RustBindingsAPI')
    assert result['rss_after_write_bytes']>0 and result['disk_bytes']>0
