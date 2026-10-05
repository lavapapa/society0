from benchmarks.core_next_cold_sqlite_codec import measure


def test_native_sqlite_grouping_preserves_boundaries_and_byte_ranges(tmp_path):
    values=[b'one',b'\0'*150000,'汉🙂'.encode()*13000,b'last']
    for grouped in (False,True):
        result=measure(tmp_path/str(grouped),values,grouped=grouped,codec='zlib')
        assert result['all_values_equal']
        assert result['range_values_equal']
        assert result['max_decoded_block_bytes']<=65536
        assert result['database_bytes']>result['compressed_bytes']
