"""存储作者独立检查总结作者的分区发布和大行恢复合同。"""
import copy

from society0.result_datasets import externalize_history, iter_dataset, load_history, read_dataset_page


def test_summary_partitions_keep_large_rows_and_empty_partition(tmp_path):
    history = {'a': {'by_tick': {'0': {'text': '😀汉' * 70000}, '1': {'n': 2}}},
               'b': {'by_tick': {'0': {'different': True}}},
               'c': {'by_interaction': {}}}
    expected = copy.deepcopy(history)
    externalize_history(tmp_path, history)
    first, second, empty = history['a']['by_tick'], history['b']['by_tick'], history['c']['by_interaction']
    assert first['path'] == second['path'] == empty['path']
    page = read_dataset_page(tmp_path, first, limit=1, max_bytes=512)
    assert page['total'] == 2 and page['records'][0]['record_ref']
    assert page['next_sequence'] is not None
    tail = read_dataset_page(tmp_path, first, after_sequence=page['next_sequence'])
    assert tail['records'][0]['value'] == {'key': '1', 'value': {'n': 2}}
    assert tail['next_sequence'] is None
    assert list(iter_dataset(tmp_path, second)) == [{'key': '0', 'value': {'different': True}}]
    assert list(iter_dataset(tmp_path, empty)) == []
    assert load_history(tmp_path, history) == expected
