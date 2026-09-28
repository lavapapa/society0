"""步骤大表与汇总历史的不可变数据集，复用检查点的逐条存储。"""
from collections.abc import Iterator, Mapping
from pathlib import Path
import uuid
import json

from .checkpoint_records import _json_parts, iter_records, iter_record_bytes, read_page, write_records


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
    source = _dataset_path(run_dir, reference)
    if 'record_path' not in reference:
        for record in iter_records(source):
            yield record['value']
        return
    after = -1
    while True:
        page = read_dataset_page(run_dir, reference, after_sequence=after)
        for record in page['records']:
            if 'record_ref' in record:
                record = json.loads(b''.join(iter_record_bytes(source, record['sequence'])))
            yield record['value']
        if page['next_sequence'] is None:
            return
        after = page['next_sequence']


def read_dataset_page(run_dir, reference, *, after_sequence=-1, limit=100, max_bytes=65536):
    return read_page(_dataset_path(run_dir, reference), after_sequence=after_sequence,
                     limit=limit, max_bytes=max_bytes, path_filter=reference.get('record_path'))


def iter_table(run_dir, value):
    if isinstance(value, Mapping) and value.get('dataset') == 'society0_records_v1':
        yield from iter_dataset(run_dir, value)
    elif isinstance(value, list):
        yield from value


def externalize_history(run_dir, value, *, path=()):
    """一次发布所有总结分区，保留每个历史字段的独立读取引用。"""
    partitions = []
    def collect(container, prefix):
        if not isinstance(container, dict):
            return
        for key, item in container.items():
            record_path = (*prefix, key)
            if key in {'by_tick', 'agent_batches', 'by_interaction'} and isinstance(item, dict):
                partitions.append((container, key, item, list(record_path)))
            elif isinstance(item, dict):
                collect(item, record_path)
    collect(value, path)
    if not partitions:
        return
    relative = f'result_datasets/{uuid.uuid4().hex}.sqlite'
    def records():
        for _, _, history, record_path in partitions:
            for name, row in history.items():
                yield {'path': record_path, 'operation': 'row', 'value': {'key': name, 'value': row}}
    write_records(Path(run_dir) / relative, records())
    # 完整容器发布成功后才替换总结字段；失败时保留调用方的原数据。
    for container, key, history, record_path in partitions:
        container[key] = {'dataset': 'society0_records_v1', 'path': relative,
                          'record_path': record_path, 'count': len(history),
                          'step': None, 'publication': 'run_diagnostics'}


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
