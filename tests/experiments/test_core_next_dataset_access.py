from benchmarks.core_next_dataset_access import probe
from society0.kernel.datasets import Datasets,DATASET_SCHEMA
from society0.kernel.storage import StageStore


def test_sequential_pages_advance_and_random_reads_match_bytes(tmp_path):
    with StageStore.create(tmp_path/'run',DATASET_SCHEMA) as store:
        Datasets(store).import_rows('data',[{'n':n,'text':'汉字'*n} for n in range(50)])
        store.complete(1)
    result=probe(tmp_path/'run',pages=20,limit=3,random_reads=20)
    assert result['sequential_records']==50 and result['sequential_pages']==17
    assert result['last_ordinal']==49 and result['random_reads']==20
    assert result['sample_ranges_equal']
