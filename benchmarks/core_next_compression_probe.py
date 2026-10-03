"""独立探针：同步压缩、同步有界线程压缩、事务外冻结/压缩的实际 Thread 写入。"""
from __future__ import annotations
import argparse
import asyncio
import base64
from collections import deque
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import platform
import random
import resource
import statistics
import struct
import tempfile
import time
from unittest.mock import patch
import zlib

from society0.kernel._json_chunks import _json_parts, CHUNK_BYTES
from society0.kernel.storage import StageStore, Writer
from society0.kernel.threads import ThreadStore, THREAD_SCHEMA


def raw_chunks(value):
    buffer = bytearray()
    for part in _json_parts(value):
        for offset in range(0, len(part), CHUNK_BYTES):
            piece = memoryview(part)[offset:offset+CHUNK_BYTES]
            while piece:
                count = min(CHUNK_BYTES-len(buffer),len(piece))
                buffer.extend(piece[:count]);piece = piece[count:]
                if len(buffer)==CHUNK_BYTES:
                    yield bytes(buffer)
                    buffer.clear()
    if buffer:
        yield bytes(buffer)


def parallel_chunks(chunks, *, workers=4, pending_bytes=8*CHUNK_BYTES, stats=None):
    stats = {} if stats is None else stats
    pending = deque()
    retained = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for raw in chunks:
            while pending and retained+len(raw)>pending_bytes:
                size,future = pending.popleft();retained -= size
                yield size,future.result()
            pending.append((len(raw),pool.submit(zlib.compress,raw,3)))
            retained += len(raw)
            stats['max_pending_raw_bytes'] = max(stats.get('max_pending_raw_bytes',0),retained)
        while pending:
            size,future = pending.popleft()
            yield size,future.result()


class Prepared:
    def __init__(self, directory):
        self.directory = tempfile.TemporaryDirectory(prefix='compressed-',dir=directory)
        self.root = Path(self.directory.name)
        self.stats = {}
    def chunks(self):
        with (self.root/'compressed').open('rb') as source:
            while header := source.read(8):
                raw_size,size = struct.unpack('<II',header)
                yield raw_size,source.read(size)
    def close(self):
        self.directory.cleanup()


async def prepare(value, *, directory=None, encoded_bytes=False, workers=4):
    prepared = Prepared(directory)
    start = time.perf_counter()
    try:
        if encoded_bytes:
            if type(value) is not bytes:
                raise TypeError('preencoded path requires immutable bytes')
            raw_size = len(value)
        else:
            with (prepared.root/'raw').open('wb') as stream:
                raw_size = sum(stream.write(chunk) for chunk in raw_chunks(value))
        prepared.stats['freeze_seconds'] = time.perf_counter()-start
        prepared.stats['raw_spool_bytes'] = 0 if encoded_bytes else raw_size
        def compress():
            def read():
                if encoded_bytes:
                    for offset in range(0,len(value),CHUNK_BYTES):
                        yield value[offset:offset+CHUNK_BYTES]
                else:
                    with (prepared.root/'raw').open('rb') as source:
                        while chunk := source.read(CHUNK_BYTES):
                            yield chunk
            with (prepared.root/'compressed').open('wb') as target:
                for size,payload in parallel_chunks(read(),workers=workers,stats=prepared.stats):
                    target.write(struct.pack('<II',size,len(payload)))
                    target.write(payload)
            prepared.stats['compressed_spool_bytes'] = (prepared.root/'compressed').stat().st_size
        task = asyncio.create_task(asyncio.to_thread(compress))
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            # 已启动的 native 工作完成后再释放其文件；取消从未进入 SQL writer。
            while not task.done():
                try:
                    await asyncio.shield(task)
                except asyncio.CancelledError:
                    continue
            task.result()
            raise
        return prepared
    except BaseException:
        prepared.close()
        raise


async def probe(path, mode, size, *, iterations=4, workers=4):
    body = base64.b64encode(random.Random(37).randbytes(size*3//4)).decode()
    value = {'role':'user','content':body}
    # bytes 路径以调用者已拥有完整不可变 JSON bytes 为前提，编码成本另计。
    encoded_start = time.perf_counter()
    encoded = b''.join(raw_chunks(value)) if mode=='bytes' else None
    encoded_seconds = time.perf_counter()-encoded_start
    if mode=='bytes':
        value=None
        del body
    samples=[];reads=[];active=True
    with StageStore.create(path,THREAD_SCHEMA,compression_workers=workers if mode=='product' else 1) as store:
        threads = ThreadStore(store);tid = threads.open('actor',0,'decision')
        async def observer():
            previous = time.perf_counter()
            while active:
                await asyncio.sleep(0.001)
                now=time.perf_counter();samples.append(now-previous);previous=now
                started=time.perf_counter()
                store.read(lambda view:view.query('SELECT last_seq FROM thread_heads WHERE id=?',(tid,)))
                reads.append(time.perf_counter()-started)
        task=asyncio.create_task(observer());await asyncio.sleep(0)
        stats={};freeze=0;temp_bytes=0;writer_seconds=0
        baseline_peak=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        cpu=time.process_time();wall=time.perf_counter()
        for _ in range(iterations):
            if mode in ('spooled','bytes'):
                prepared = await prepare(encoded if mode=='bytes' else value,encoded_bytes=mode=='bytes',workers=workers)
                try:
                    freeze += prepared.stats['freeze_seconds']
                    temp_bytes += prepared.stats['raw_spool_bytes']+prepared.stats['compressed_spool_bytes']
                    stats.update(prepared.stats)
                    started=time.perf_counter()
                    with patch.object(Writer,'encode_chunks',lambda writer,_:prepared.chunks()):
                        threads.append_message(tid,value if value is not None else {})
                    writer_seconds += time.perf_counter()-started
                finally:
                    prepared.close()
            elif mode=='threaded':
                started=time.perf_counter()
                with patch.object(Writer,'encode_chunks',lambda writer,value:parallel_chunks(raw_chunks(value),workers=workers,stats=stats)):
                    threads.append_message(tid,value)
                writer_seconds += time.perf_counter()-started
            else:
                started=time.perf_counter();threads.append_message(tid,value)
                writer_seconds += time.perf_counter()-started
            await asyncio.sleep(0)
        elapsed=time.perf_counter()-wall;cpu_elapsed=time.process_time()-cpu
        write_peak=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        active=False;await task
        # 全值校验在计时之外；所有路线写同一 Thread schema 与规范 writer。
        expected = json.loads(encoded) if mode=='bytes' else value
        assert threads.read_messages(tid)==[expected]*iterations
        store.complete(1)
        disk=sum(file.stat().st_size for file in Path(path).rglob('*') if file.is_file())
        ordered=sorted(samples)
        return {'mode':mode,'workers':workers,'body_bytes':size,'iterations':iterations,
                'wall_seconds':elapsed,'cpu_seconds':cpu_elapsed,'writer_seconds':writer_seconds,
                'freeze_seconds':freeze,'preexisting_bytes_encode_seconds':encoded_seconds,
                'temporary_written_bytes':temp_bytes,'temporary_read_bytes':temp_bytes,
                'max_pending_raw_bytes':stats.get('max_pending_raw_bytes',0),
                'observer_poll_interval_seconds':0.001,'loop_samples':len(samples),'loop_max_seconds':max(samples),
                'loop_p95_seconds':ordered[min(len(ordered)-1,int(.95*len(ordered)))],
                'status_read_count':len(reads),'status_read_max_seconds':max(reads),
                'directory_bytes':disk,'write_peak_rss_native':write_peak,'baseline_peak_rss_native':baseline_peak,
                'peak_rss_native':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
                'rss_native_unit':'bytes' if platform.system()=='Darwin' else 'KiB'}


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--mode',choices=('sync','threaded','spooled','bytes','product'),required=True)
    parser.add_argument('--size',type=int,required=True)
    parser.add_argument('--workers',type=int,default=4)
    parser.add_argument('--output',required=True)
    args=parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='core-next-compression-') as temporary:
        result=asyncio.run(probe(Path(temporary).resolve()/'run',args.mode,args.size,workers=args.workers))
    result.update(platform=platform.platform(),python=platform.python_version(),cpu_count=os.cpu_count())
    Path(args.output).write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')

if __name__=='__main__':
    main()
