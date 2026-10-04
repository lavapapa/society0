"""压缩候选须保持原文、队列预算和取消后的临时资源生命周期。"""
import asyncio
import json
import threading
from backports import zstd
import pytest
from benchmarks.core_next_compression_probe import raw_chunks, parallel_chunks, prepare


def test_parallel_chunks_match_full_json_and_bound_pending_input():
    value = {'body': '中文🙂\\\"\n'*50000, 'values': [None, True, 3, 0.125]}
    stats = {}
    result = list(parallel_chunks(raw_chunks(value), workers=4, pending_bytes=262144, stats=stats))
    raw = b''.join(zstd.decompress(payload) for size,payload in result)
    assert json.loads(raw) == value
    assert all(size <= 65536 for size,_ in result)
    assert stats['max_pending_raw_bytes'] <= 262144


@pytest.mark.asyncio
async def test_prepare_freezes_mutable_input_before_first_await_and_cleans_files(tmp_path):
    value = {'body': ['original']*30000}
    task = asyncio.create_task(prepare(value, directory=tmp_path))
    await asyncio.sleep(0)
    value['body'][0] = 'changed after yield'
    prepared = await task
    try:
        original = json.loads(b''.join(zstd.decompress(body) for _,body in prepared.chunks()))
        assert original['body'][0] == 'original'
    finally:
        prepared.close()
    assert not list(tmp_path.iterdir())


@pytest.mark.asyncio
async def test_cancel_waits_for_worker_before_removing_spools(tmp_path, monkeypatch):
    from benchmarks import core_next_compression_probe as probe
    entered = threading.Event()
    release = threading.Event()
    native = probe.zstd.compress
    def block(raw, *, level):
        entered.set()
        release.wait()
        return native(raw, level=level)
    monkeypatch.setattr(probe.zstd, 'compress', block)
    task = asyncio.create_task(prepare({'body':'x'*1000000}, directory=tmp_path))
    while not entered.is_set():
        await asyncio.sleep(0)
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()
    task.cancel()
    for _ in range(10):
        await asyncio.sleep(0)
    still_running = not task.done()
    release.set()
    assert still_running, 'second cancellation released native worker files early'
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not list(tmp_path.iterdir())


@pytest.mark.asyncio
@pytest.mark.parametrize('mode',['sync','threaded','spooled','bytes'])
async def test_probe_runs_actual_thread_writer_and_status_reader(tmp_path,mode):
    from benchmarks.core_next_compression_probe import probe
    result = await probe(tmp_path/mode,mode,131072,iterations=2)
    assert result['status_read_count'] >= 1
    assert result['directory_bytes'] > 0
