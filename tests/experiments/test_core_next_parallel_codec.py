import pytest
from benchmarks.core_next_parallel_codec import probe


@pytest.mark.parametrize('mode',['serial','threads','processes'])
@pytest.mark.parametrize('kernel',['codec','numeric'])
def test_parallel_codec_returns_all_original_values_and_bounded_queue(mode,kernel):
    result=probe(mode=mode,workers=2,jobs=4,source_bytes=16384,kernel=kernel)
    assert result['verified_jobs']==4 and result['max_pending_jobs']<=4
    assert result['output_bytes']>0 and result['group_peak_rss_bytes']>0
