from benchmarks.core_next_cold_runtime_probe import probe
from society0.kernel.storage import StageStore
from society0.kernel.datasets import Datasets,DATASET_SCHEMA


def test_cold_fixed_workload_preserves_body_and_small_complete_changes(tmp_path):
    with StageStore.create(tmp_path/'source',DATASET_SCHEMA) as store:
        Datasets(store).import_rows('facts',[{'body':'汉🙂'*30000}])
        store.complete(1)
    result=probe(tmp_path/'source',tmp_path/'copy',ordinal=0,steps=3)
    assert result['records']==1
    assert all(step['session_bytes']<10000 for step in result['steps'])
    assert result['combined_space']['unique_allocated_bytes']<result['combined_space']['summed_allocated_bytes']
