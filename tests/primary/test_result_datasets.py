import json
from pathlib import Path

from society0.schedule import StepResult


def test_iterable_table_is_streamed_and_has_paginated_consumer(tmp_path):
    from society0.result_datasets import read_dataset_page, iter_dataset
    seen = []
    def rows():
        for i in range(1100):
            seen.append(i)
            yield {'id': i, 'text': '汉' * 10}
    result = StepResult(tables={'rows': rows()}).to_dict(result_dir=tmp_path, step=4, step_name='produce')
    ref = result['tables']['rows']
    assert ref['count'] == 1100
    assert ref['step'] == 4
    assert ref['publication'] == 'requires_complete_checkpoint'
    assert seen == list(range(1100))
    assert [row['id'] for row in iter_dataset(tmp_path, ref)] == list(range(1100))
    page = read_dataset_page(tmp_path, ref, limit=2, max_bytes=10000)
    assert page['total'] == 1100
    assert len(page['records']) == 2
    assert len(json.dumps(result)) < 1000


def test_large_single_row_is_external_and_small_table_retains_value(tmp_path):
    small = StepResult(tables={'rows': [{'x': 1}]})
    assert small.to_dict(result_dir=tmp_path, step=1, step_name='small')['tables'] == {'rows': [{'x': 1}]}
    large = StepResult(tables={'rows': [{'x': 'z' * 1100000}]})
    assert large.to_dict(result_dir=tmp_path, step=1, step_name='large')['tables']['rows']['count'] == 1


import pytest


@pytest.mark.asyncio
async def test_dataset_is_bound_to_published_checkpoint_and_failed_data_stays_diagnostic(tmp_path):
    from society0 import Society0
    from society0.incremental_checkpoint import V4CheckpointStore
    from society0.result_datasets import dataset_is_committed
    config = {'agent_types': [{'id': 'r', 'archetype': 'rule'}], 'agents': [],
              'environment': {'type': 'plain', 'state': {}}}
    engine = Society0(save_dir=str(tmp_path), base_config=config)
    @engine.step(name='rows')
    async def rows(ctx):
        return StepResult(tables={'rows': ({'i': i} for i in range(3))})
    await engine.run(steps=1)
    result = json.loads((tmp_path / 'steps.jsonl').read_text())['result']
    reference = result['tables']['rows']
    store = V4CheckpointStore(tmp_path, create=False)
    assert dataset_is_committed(reference, store.resolve(1)['manifest'])
    assert not dataset_is_committed(reference, store.resolve(0)['manifest'])
    orphan = StepResult(tables={'rows': iter([{'i': 4}])}).to_dict(result_dir=tmp_path, step=1, step_name='failed')['tables']['rows']
    assert not dataset_is_committed(orphan, store.resolve(1)['manifest'])


@pytest.mark.asyncio
async def test_summary_reads_resources_once_and_counts_nested_artifacts(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from society0 import Society0
    from society0.result_datasets import iter_dataset
    engine = Society0(save_dir=str(tmp_path), base_config={})
    engine.current_world_state = SimpleNamespace(step=2, get_state_summary=lambda: {})
    monkeypatch.setattr(engine, "_summarize_capabilities", lambda: {})
    (tmp_path / 'chroma_store').mkdir(exist_ok=True)
    (tmp_path / 'chroma_store' / 'data.bin').write_bytes(b'x' * 30)
    nested = tmp_path / 'checkpoints' / 'v4' / 'components'
    nested.mkdir(parents=True, exist_ok=True)
    (nested / 'segment.sqlite').write_bytes(b'x' * 100)
    (tmp_path / 'resource_calls.jsonl').write_text(json.dumps({'resource_type': 'llm', 'status': 'success', 'step': 1, 'duration_sec': 1}) + '\n')
    (tmp_path / 'events.jsonl').write_text(json.dumps({'event': 'tick_completed', 'step': 1, 'duration_sec': 1}) + '\n')
    original = Path.open
    reads = []
    def counted(path, *args, **kwargs):
        mode = args[0] if args else kwargs.get('mode', 'r')
        if path.name == 'resource_calls.jsonl' and 'r' in mode:
            reads.append(path)
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'open', counted)
    await engine._save_summary(steps_requested=2, steps_completed=2, total_time=2)
    summary = json.loads((tmp_path / 'summary.json').read_text())
    assert len(reads) == 1
    assert summary['outputs']['directories']['chroma_store']['total_bytes'] == 30
    assert summary['outputs']['directories']['checkpoints']['total_bytes'] >= 100
    assert summary['outputs']['total_bytes'] == sum(p.stat().st_size for p in tmp_path.rglob('*') if p.is_file() and p.name != 'summary.json')
    assert list(iter_dataset(tmp_path, summary['resources']['llm']['by_tick']))[0]['key'] == '1'


@pytest.mark.asyncio
async def test_multi_step_epoch_datasets_commit_together_and_failed_epoch_stays_hidden(tmp_path, monkeypatch):
    from society0 import Society0
    from society0.core_data import World
    from society0.incremental_checkpoint import V4CheckpointStore
    from society0.observation import ObservationReader
    from society0.result_datasets import dataset_is_committed
    captured = []
    original = World.set_checkpoint_annotation
    def capture(world, name, value):
        if name.startswith('dataset:'):
            captured.append(value)
        return original(world, name, value)
    monkeypatch.setattr(World, 'set_checkpoint_annotation', capture)
    engine = Society0(save_dir=str(tmp_path), checkpoint_every=2, base_config={
        'agent_types': [{'id': 'r', 'archetype': 'rule'}], 'agents': [],
        'environment': {'type': 'plain', 'state': {}}})
    @engine.step(name='rows')
    async def rows(ctx):
        return StepResult(tables={'rows': iter([{'step': ctx.step}])})
    @engine.step(name='fail-fourth')
    async def fail(ctx):
        if ctx.step == 3:
            raise RuntimeError('fourth tick fails')
    with pytest.raises(RuntimeError, match='fourth tick fails'):
        await engine.run(steps=4)
    store = V4CheckpointStore(tmp_path, create=False)
    manifest = store.resolve(2)['manifest']
    assert len(captured) == 4
    assert [dataset_is_committed(ref, manifest) for ref in captured] == [True, True, False, False]
    with ObservationReader(tmp_path) as reader:
        reader.sync()
        assert [reader.dataset_page(ref)['records'][0]['value']['step'] for ref in captured[:2]] == [0, 1]
        for ref in captured[2:]:
            with pytest.raises(ValueError, match='dataset_not_committed'):
                reader.dataset_page(ref)


def test_summary_partitions_publish_once_and_preserve_all_rows(tmp_path, monkeypatch):
    from copy import deepcopy
    from society0 import result_datasets as datasets
    summary = {'failed': True, 'failure': {'type': 'injected'},
               'groups': {str(i): {'by_tick': {str(j): {'value': i * 10 + j} for j in range(i)}} for i in range(8)}}
    expected = deepcopy(summary)
    published = []
    original = datasets.write_records
    def counted(path, records, **kwargs):
        published.append(path)
        return original(path, records, **kwargs)
    monkeypatch.setattr(datasets, 'write_records', counted)
    datasets.externalize_history(tmp_path, summary)
    assert len(published) == 1
    assert len(list((tmp_path / 'result_datasets').glob('*.sqlite'))) == 1
    refs = [group['by_tick'] for group in summary['groups'].values()]
    assert len({ref['path'] for ref in refs}) == 1
    assert len({tuple(ref['record_path']) for ref in refs}) == 8
    assert all(ref['publication'] == 'run_diagnostics' for ref in refs)
    assert summary['failed'] is True
    for index, ref in enumerate(refs):
        rows, after = [], -1
        while True:
            page = datasets.read_dataset_page(tmp_path, ref, after_sequence=after, limit=2)
            assert page['total'] == index
            rows.extend(row['value'] for row in page['records'])
            if page['next_sequence'] is None:
                break
            after = page['next_sequence']
        assert rows == [{'key': str(j), 'value': {'value': index * 10 + j}} for j in range(index)]
        assert list(datasets.iter_dataset(tmp_path, ref)) == rows
        assert not datasets.dataset_is_committed(ref, {'annotations': {}})
    assert datasets.load_history(tmp_path, summary) == expected


def test_summary_partition_failure_does_not_replace_original_values(tmp_path, monkeypatch):
    from society0 import result_datasets as datasets
    value = {'by_tick': {'1': {'count': 2}}}
    def fail(*args, **kwargs):
        raise OSError('injected publish failure')
    monkeypatch.setattr(datasets, 'write_records', fail)
    with pytest.raises(OSError, match='injected'):
        datasets.externalize_history(tmp_path, value)
    assert value == {'by_tick': {'1': {'count': 2}}}


def test_summary_large_partition_row_remains_fully_readable(tmp_path):
    from society0.result_datasets import externalize_history, iter_dataset, read_dataset_page
    text = '长记录' * 50000
    summary = {'a': {'by_tick': {'large': {'text': text}}}, 'b': {'by_tick': {'small': 3}}}
    externalize_history(tmp_path, summary)
    large = summary['a']['by_tick']
    page = read_dataset_page(tmp_path, large, max_bytes=512)
    assert page['total'] == 1 and 'record_ref' in page['records'][0]
    assert list(iter_dataset(tmp_path, large)) == [{'key': 'large', 'value': {'text': text}}]
    assert list(iter_dataset(tmp_path, summary['b']['by_tick'])) == [{'key': 'small', 'value': 3}]


@pytest.mark.asyncio
async def test_failed_run_summary_partitions_stay_diagnostic(tmp_path):
    from society0 import Society0, ObservationReader
    from society0.result_datasets import load_history
    engine = Society0(str(tmp_path), base_config={'agents': [], 'environment': {'type': 'plain', 'state': {}}})
    @engine.step(name='fail')
    async def fail(ctx):
        raise RuntimeError('summary failure fixture')
    with pytest.raises(RuntimeError, match='summary failure fixture'):
        await engine.run(steps=1)
    summary = json.loads((tmp_path / 'summary.json').read_text())
    assert summary['failed'] is True
    restored = load_history(tmp_path, summary)
    refs = []
    def collect(value):
        if isinstance(value, dict):
            if value.get('dataset') == 'society0_records_v1':
                refs.append(value)
            else:
                for item in value.values():
                    collect(item)
    collect(summary)
    assert refs and len({ref['path'] for ref in refs}) == 1
    with ObservationReader(tmp_path, index_dir=tmp_path / 'query') as reader:
        reader.sync()
        assert reader.status()['committed_checkpoint']['step'] == 0
        for ref in refs:
            assert ref['publication'] == 'run_diagnostics'
            assert reader.dataset_page(ref)['total'] == ref['count']
    assert restored['failed'] is True
