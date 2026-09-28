"""用临时合成数据测量现有 Society0 读取与发布路径；不连接模型服务。"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
import tempfile
from time import perf_counter
import tracemalloc

from society0.agent.thread_store import AgentThreadStore
from society0.core_data import World
from society0.incremental_checkpoint import PersistenceSchema, SealedTickDelta
from society0.persistence import PersistenceManager
from society0.state_persistence import persistent_state_schema, replaceable


def thread_read(root: Path, count: int) -> dict:
    store = AgentThreadStore(root)
    tid = store.open_thread(agent_id='a', checkpoint_step=1, scope={'step': 1})
    for i in range(count):
        store.append_event(tid, 'conversation_message', payload={'message': {'role': 'user', 'content': f'{i}:' + 'x' * 4096}})
    path = next(store.threads_dir.rglob('*.jsonl'))
    store.reset_metrics()
    tracemalloc.start()
    start = perf_counter()
    records = store.read_events(tid, materialize_payloads=False)
    elapsed = perf_counter() - start
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert len(records) == count + 1
    return {'count': count, 'file_bytes': path.stat().st_size, 'read_sec': elapsed,
            'python_peak_bytes': peak, 'metrics': dict(store.metrics)}


def partial_tail(root: Path) -> dict:
    writer = AgentThreadStore(root)
    tid = writer.open_thread(agent_id='a', checkpoint_step=1, scope={'step': 1})
    path = next(writer.threads_dir.rglob('*.jsonl'))
    with path.open('ab') as f:
        f.write(b'{"schema_version":2,')
    before = path.stat().st_size
    reader = AgentThreadStore(root, create=False)
    reader.read_events(tid, materialize_payloads=False)
    return {'before_bytes': before, 'after_bytes': path.stat().st_size,
            'reader_removed_bytes': before - path.stat().st_size}


def blob_metadata_read(root: Path) -> dict:
    writer = AgentThreadStore(root, inline_payload_max_bytes=64)
    tid = writer.open_thread(agent_id='a', checkpoint_step=1, scope={'step': 1})
    writer.append_event(tid, 'tool_execution_completed', payload={'result': 'x' * 65536})
    reader = AgentThreadStore(root, create=False)
    materialized = []
    original = reader._materialize_payload
    def observed(event):
        if event.get('payload_ref'):
            materialized.append(event['payload_ref']['bytes'])
        return original(event)
    reader._materialize_payload = observed
    reader.read_events(tid, materialize_payloads=False)
    return {'blob_read_calls': len(materialized), 'blob_bytes_processed': sum(materialized)}


def journal_coalescing(root: Path) -> dict:
    root.mkdir()
    world = World(event_log_path=str(root/'events.jsonl'))
    world.environment_data['state'] = {'item': {'counter': 0, 'body': 'x'*65536}}
    world.set_state_change_event_recording(False)
    world.configure_persistence(PersistenceSchema.compile(
        persistent_state_schema(item=replaceable()), root_path=('environment','state')))
    world.begin_persistence_tick(1)
    state = world.create_environment_state_proxy()
    for i in range(10000):
        state['item']['counter'] = i
    delta = world.seal_persistence_tick()
    assert len(delta.replacements) == 1
    assert delta.replacements[0]['value']['counter'] == 9999
    world.event_logger.close()
    return {'mutations': 10000, 'sealed_replacements': len(delta.replacements),
            'sealed_json_bytes': len(json.dumps(delta.replacements).encode())}


async def manager_history(root: Path) -> list[dict]:
    root.mkdir()
    world = World(event_log_path=str(root/'events.jsonl'))
    world.environment_data['type'] = 'plain'
    world.environment_data['state'] = {'counter': 0}
    manager = PersistenceManager(str(root))
    manager.configure_v4(world, PersistenceSchema.compile(
        persistent_state_schema(counter=replaceable()), root_path=('environment','state')))
    await manager.publish_root(world, schedule=object())
    store = manager._v4_store
    original = store._manifest_chain
    chain_lengths = []
    def observed(step):
        chain = original(step)
        chain_lengths.append(len(chain))
        return chain
    store._manifest_chain = observed
    results=[]
    for step in range(1, 201):
        start=perf_counter()
        await manager.publish_delta(SealedTickDelta(step=step, replacements=(), appends=(),
                                    write_epoch_ids=(f'e{step}',)), object())
        elapsed=perf_counter()-start
        if step in (10, 100, 200):
            results.append({'step':step, 'publish_sec':elapsed,
                            'last_manifest_chain_length':chain_lengths[-1],
                            'cumulative_manifest_visits':sum(chain_lengths)})
    manager.close()
    world.event_logger.close()
    return results


def main():
    with tempfile.TemporaryDirectory(prefix='society0-observation-probe-') as tmp:
        root=Path(tmp)
        result={'thread_reads':[thread_read(root/f'thread-{n}',n) for n in (200,2000)],
                'partial_tail_read':partial_tail(root/'tail'),
                'metadata_only_blob_read':blob_metadata_read(root/'blob'),
                'journal':journal_coalescing(root/'journal'),
                'manager_history':asyncio.run(manager_history(root/'manager'))}
        print(json.dumps(result,indent=2))

if __name__ == '__main__':
    main()
