import json
from pathlib import Path

import pytest

from society0.agent.thread_store import AgentThreadStore


def opened(tmp_path, **kwargs):
    store = AgentThreadStore(tmp_path, **kwargs)
    tid = store.open_thread(agent_id='a', checkpoint_step=1, scope={'kind': 'test'})
    return store, tid


def test_observer_never_repairs_partial_tail_or_reads_blobs(tmp_path, monkeypatch):
    writer, tid = opened(tmp_path, inline_payload_max_bytes=32)
    writer.append_event(tid, 'large', payload={'text': 'x' * 10000})
    path = writer._resolve_thread_path(tid)
    with path.open('ab') as f:
        f.write(b'{"unfinished":')
    before = path.read_bytes()
    reader = AgentThreadStore(tmp_path, create=False)
    monkeypatch.setattr(reader, '_materialize_payload', lambda *_: pytest.fail('blob read'))
    assert len(reader.read_events(tid, materialize_payloads=False)) == 2
    assert path.read_bytes() == before
    with pytest.raises(PermissionError):
        reader.append_event(tid, 'forbidden')


def test_pages_capture_boundary_and_references_have_complete_content(tmp_path):
    writer, tid = opened(tmp_path)
    writer.append_event(tid, 'large', payload={'text': '汉' * 10000})
    reader = AgentThreadStore(tmp_path, create=False)
    first = reader.read_event_page(tid, max_records=1, max_bytes=1024)
    writer.append_event(tid, 'later', payload=3)
    second = reader.read_event_page(tid, cursor=first['next_cursor'], max_bytes=1024)
    assert second['total'] == 2
    assert second['next_cursor'] is None
    assert second['bytes'] <= 1024
    ref = second['events'][0]['event_ref']
    assert reader.read_event(ref)['payload']['text'] == '汉' * 10000
    chunks = []
    offset = 0
    while True:
        part = reader.read_content(ref, offset=offset, max_bytes=777)
        chunks.append(part['data'])
        if part['next_offset'] is None:
            break
        offset = part['next_offset']
    assert json.loads(b''.join(chunks))['event_type'] == 'large'
    assert reader.read_event_page(tid)['total'] == 3


def test_new_and_cold_threads_do_not_glob_and_caches_are_bounded(tmp_path, monkeypatch):
    store = AgentThreadStore(tmp_path, cache_max_entries=2)
    monkeypatch.setattr(Path, 'glob', lambda *_: pytest.fail('historical glob'))
    ids = [store.open_thread(agent_id='a', checkpoint_step=i, scope={'i': i}) for i in range(8)]
    assert len(store._path_cache) <= 2
    assert len(store._thread_indexes) <= 2
    reader = AgentThreadStore(tmp_path, create=False, cache_max_entries=2)
    for tid in ids:
        assert reader.read_event_page(tid)['total'] == 1
    assert len(reader._path_cache) <= 2


def test_published_boundary_and_model_history(tmp_path):
    writer, tid = opened(tmp_path)
    for i in range(10):
        writer.append_event(tid, 'conversation_message', payload={'role': 'user', 'content': str(i)})
    ref = writer.get_thread_reference(tid)
    writer.append_event(tid, 'later')
    reader = AgentThreadStore(tmp_path, create=False)
    page = reader.read_event_page(tid, boundary=ref, max_records=100)
    assert page['total'] == 11
    assert len(page['events']) == 11
    assert len(reader.read_messages(tid)) == 10


def test_page_reads_only_requested_records_and_never_takes_writer_lock(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    writer, tid = opened(tmp_path)
    for _ in range(200):
        writer.append_event(tid, 'data', payload='x' * 1000)
    reader = AgentThreadStore(tmp_path, create=False)
    with ThreadPoolExecutor() as pool:
        with writer._lock:
            page = pool.submit(reader.read_event_page, tid, max_records=2).result(timeout=2)
    assert len(page['events']) == 2
    assert reader.metrics['jsonl_bytes_read'] < 4000
    assert page['readable_through'] == page['boundary']['end_offset']
    assert page['durable_through'] is None


def test_observer_is_read_only_in_fresh_process_and_writer_recovers(tmp_path):
    import os
    import subprocess
    import sys
    writer, tid = opened(tmp_path)
    path = writer._resolve_thread_path(tid)
    with path.open('ab') as stream:
        stream.write(b'{"incomplete":')
    original = path.read_bytes()
    code = '''import sys
from society0.agent.thread_store import AgentThreadStore
s = AgentThreadStore(sys.argv[1], create=False)
p = s.read_event_page(sys.argv[2])
assert p['total'] == 1
assert len(s.read_events(sys.argv[2])) == 1
'''
    subprocess.run([sys.executable, '-c', code, str(tmp_path), tid], check=True, env=os.environ)
    assert path.read_bytes() == original
    resumed = AgentThreadStore(tmp_path)
    resumed.append_event(tid, 'recovered')
    assert [e['sequence'] for e in resumed.read_event_page(tid)['events']] == [1, 2]


def test_page_cursor_rejects_other_run_and_blob_range_is_complete(tmp_path):
    writer, tid = opened(tmp_path / 'one', inline_payload_max_bytes=100)
    writer.append_event(tid, 'blob', payload={'content': 'z' * 10000})
    page = writer.read_event_page(tid, max_records=1)
    other = AgentThreadStore(tmp_path / 'two')
    other.open_thread(thread_id=tid, agent_id='a', checkpoint_step=1, scope={'kind': 'test'})
    with pytest.raises(ValueError, match='cursor'):
        other.read_event_page(tid, cursor=page['next_cursor'])
    blob = writer.read_event_page(tid, cursor=page['next_cursor'])['events'][0]['payload_ref']
    assert writer.read_payload(blob) == {'content': 'z' * 10000}
    assert len(writer.read_content(blob, max_bytes=100)['data']) == 100


def test_large_opened_payload_is_not_retained_in_cache(tmp_path):
    store = AgentThreadStore(tmp_path)
    tid = store.open_thread(agent_id='a', checkpoint_step=1, scope={'context': 'x' * 70000})
    assert store._thread_indexes[tid].opened_payload is None
    assert store.read_events(tid)[0]['payload']['scope']['context'] == 'x' * 70000


def test_page_size_budget_with_many_records_and_unicode(tmp_path):
    store, tid = opened(tmp_path)
    for _ in range(20):
        store.append_event(tid, 'unicode', payload='中文' * 300)
    records = []
    cursor = None
    while True:
        page = store.read_event_page(tid, cursor=cursor, max_records=3, max_bytes=1024)
        assert len(json.dumps(page['events'], ensure_ascii=False, separators=(',', ':')).encode()) == page['bytes']
        assert page['bytes'] <= 1024
        assert 0 < len(page['events']) <= 3
        records.extend(page['events'])
        cursor = page['next_cursor']
        if cursor is None:
            break
    assert len(records) == 21


def test_oversized_opened_payload_does_not_make_each_append_scan_history(tmp_path):
    store = AgentThreadStore(tmp_path)
    tid = store.open_thread(agent_id='a', checkpoint_step=1, scope={'context': 'x' * 70000})
    store.reset_metrics()
    for i in range(5):
        store.append_event(tid, 'item', payload=i)
    assert store.metrics['jsonl_full_reads'] == 0
    assert store.get_thread_reference(tid)['scope']['context'] == 'x' * 70000


@pytest.mark.parametrize('offset_state', ['behind', 'ahead', 'partial'])
def test_cold_writer_rebuilds_offsets_after_interrupted_publication(tmp_path, offset_state):
    import struct
    store, tid = opened(tmp_path)
    store.append_event(tid, 'one')
    path = store._resolve_thread_path(tid)
    offsets = path.with_suffix('.offsets')
    original = offsets.read_bytes()
    if offset_state == 'behind':
        offsets.write_bytes(original[:8])
    elif offset_state == 'ahead':
        offsets.write_bytes(original + struct.pack('<Q', path.stat().st_size + 30))
    else:
        offsets.write_bytes(original + b'123')
    resumed = AgentThreadStore(tmp_path)
    resumed.append_event(tid, 'after-restart')
    page = AgentThreadStore(tmp_path, create=False).read_event_page(tid)
    assert page['total'] == 3
    assert [event['event_type'] for event in page['events']] == ['thread_opened', 'one', 'after-restart']
    assert len(offsets.read_bytes()) == 24


def test_writer_process_exit_between_jsonl_and_offset_is_recoverable(tmp_path):
    import subprocess
    import sys
    store, tid = opened(tmp_path)
    script = '''
import os,sys
from pathlib import Path
from society0.agent.thread_store import AgentThreadStore
store = AgentThreadStore(sys.argv[1])
original = Path.open
def crash_before_offset(path, mode='r', *args, **kwargs):
    if path.suffix == '.offsets' and mode == 'ab':
        os._exit(23)
    return original(path, mode, *args, **kwargs)
Path.open = crash_before_offset
store.append_event(sys.argv[2], 'written-before-crash')
'''
    crashed = subprocess.run([sys.executable, '-c', script, str(tmp_path), tid])
    assert crashed.returncode == 23
    reader = AgentThreadStore(tmp_path, create=False)
    assert reader.read_event_page(tid)['total'] == 1
    resumed = AgentThreadStore(tmp_path)
    resumed.append_event(tid, 'after-restart')
    assert [event['event_type'] for event in reader.read_event_page(tid)['events']] == ['thread_opened', 'written-before-crash', 'after-restart']


def test_cold_index_rebuild_is_atomic_for_observers(tmp_path, monkeypatch):
    import society0.agent.thread_store as module
    store, tid = opened(tmp_path)
    store.append_event(tid, 'before-restart')
    boundary = store.get_thread_reference(tid)
    reader = AgentThreadStore(tmp_path, create=False)
    original = module.struct.pack
    observations = []
    def observe_during_rebuild(*args):
        if not observations:
            page = reader.read_event_page(tid, boundary=boundary)
            observations.append(page['total'])
            assert len(page['events']) == 2
        return original(*args)
    monkeypatch.setattr(module.struct, 'pack', observe_during_rebuild)
    resumed = AgentThreadStore(tmp_path)
    resumed.append_event(tid, 'after-restart')
    assert observations == [2]
