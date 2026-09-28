"""步骤大表与汇总历史的不可变数据集，复用检查点的逐条存储。"""
from collections.abc import Iterator, Mapping
from pathlib import Path
import uuid

from .checkpoint_records import _json_parts, iter_records, read_page, write_records


def is_large_table(value):
    if isinstance(value, Iterator):
        return True
    if not isinstance(value, (list, tuple)):
        return False
    if len(value) > 1000:
        return True
    size = 0
    try:
        for part in _json_parts(value):
            size += len(part)
            if size > 1024 * 1024:
                return True
    except TypeError:
        return True
    return False


def write_dataset(run_dir, rows, *, step, name, normalize=lambda value: value):
    relative = f'result_datasets/{uuid.uuid4().hex}.sqlite'
    count = write_records(Path(run_dir) / relative, (
        {'path': [str(name)], 'operation': 'row', 'value': normalize(row)}
        for row in rows
    ))
    return {'dataset': 'society0_records_v1', 'path': relative, 'count': count,
            'step': step, 'publication': 'requires_complete_checkpoint'}


def _dataset_path(run_dir, reference):
    root = Path(run_dir).resolve()
    path = (root / reference['path']).resolve()
    if root not in path.parents:
        raise ValueError('dataset path escapes run directory')
    return path


def iter_dataset(run_dir, reference):
    for record in iter_records(_dataset_path(run_dir, reference)):
        yield record['value']


def read_dataset_page(run_dir, reference, *, after_sequence=-1, limit=100, max_bytes=65536):
    return read_page(_dataset_path(run_dir, reference), after_sequence=after_sequence,
                     limit=limit, max_bytes=max_bytes)


def iter_table(run_dir, value):
    if isinstance(value, Mapping) and value.get('dataset') == 'society0_records_v1':
        yield from iter_dataset(run_dir, value)
    elif isinstance(value, list):
        yield from value


def externalize_history(run_dir, value, *, path=()):
    """逐个移出按步骤展开的字段；全局汇总继续保留在 summary 中。"""
    if not isinstance(value, dict):
        return
    for key in list(value):
        item = value[key]
        if key in {'by_tick', 'agent_batches', 'by_interaction'} and isinstance(item, dict):
            value[key] = write_dataset(run_dir, ({'key': name, 'value': row} for name, row in item.items()), step=None, name='/'.join((*path, key)))
            value[key]['publication'] = 'run_diagnostics'
        elif isinstance(item, dict):
            externalize_history(run_dir, item, path=(*path, key))


def dataset_is_committed(reference, manifest):
    """核对登记此数据集的原始 manifest；后续检查点可见性由观察索引查询。

    manifest 应由完整检查点解析器取得。多步骤 epoch 在同一 manifest
    登记其所有步骤的数据集；此函数不扫描后续 manifest 的祖先。
    """
    return manifest.get('annotations', {}).get('dataset:' + reference['path']) == reference


def load_history(run_dir, value):
    """离线报告显式读取全部历史；交互查询应使用 read_dataset_page。"""
    if isinstance(value, dict):
        if value.get('dataset') == 'society0_records_v1' and value.get('publication') == 'run_diagnostics':
            return {row['key']: load_history(run_dir, row['value']) for row in iter_dataset(run_dir, value)}
        return {key: load_history(run_dir, item) for key, item in value.items()}
    if isinstance(value, list):
        return [load_history(run_dir, item) for item in value]
    return value
