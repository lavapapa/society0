import pytest
pytest.importorskip('sqlite_vec',reason='isolated sqlite-vec feasibility environment required')
from benchmarks.core_next_vector_backends import probe


@pytest.mark.parametrize('backend',['sqlite-scalar','sqlite-vec0','chroma'])
def test_actual_index_filters_actor_and_returns_original_distances(tmp_path,backend):
    result=probe(tmp_path/backend,backend=backend,count=256,dimensions=16,query_count=3)
    assert result['queries']==3 and result['all_actor_scoped']
    assert result['minimum_top20_overlap']==1
    assert result['max_distance_error']<0.00001
