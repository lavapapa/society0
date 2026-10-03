import zlib
from benchmarks.core_next_codec_sample import measure_codec


def test_codec_sample_checks_every_value_and_reports_bounded_group_read():
    values=[b'a'*70,b'b'*70,b'c'*70]
    result=measure_codec(values,lambda x:zlib.compress(x,3),zlib.decompress,group_bytes=100)
    assert result['all_values_equal']
    assert result['raw_bytes']==210
    assert result['groups']==3
    assert result['max_group_raw_bytes']==70
    assert result['random_read_raw_bytes']==210
