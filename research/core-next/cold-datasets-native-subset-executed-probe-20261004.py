"""现成转换库的有限子集：不可变批次、完整恢复与只读准备的总成本。"""
import argparse
import json
import os
from pathlib import Path
import platform
import resource
import tempfile
import time
import zlib

import apsw
from society0.kernel.datasets import DATASET_SCHEMA, Datasets
from society0.kernel.storage import StageStore
import society0.kernel.storage as storage
import society0.kernel.datasets as datasets


def space(paths):
    files=[file for path in paths for file in Path(path).rglob('*') if file.is_file()]
    stats=[file.stat() for file in files]
    unique={(s.st_dev,s.st_ino):s for s in stats}
    return {'files':len(files),'logical_bytes':sum(s.st_size for s in stats),
            'summed_allocated_bytes':sum(s.st_blocks*512 for s in stats),
            'unique_allocated_bytes':sum(s.st_blocks*512 for s in unique.values())}


def probe(folder, rows):
    folder=Path(folder);source=folder/'run';target=folder/'restored';view=folder/'view'
    counts={};original=storage._sync
    encoding_seconds=0.0;source_seconds=0.0;sync_seconds=0.0;compression_seconds=0.0
    original_compressor=datasets.zstandard.ZstdCompressor
    def compressor_factory(*args,**kwargs):
        native=original_compressor(*args,**kwargs)
        class Timed:
            def compress(self,raw):
                nonlocal compression_seconds
                start=time.perf_counter()
                result=native.compress(raw)
                compression_seconds+=time.perf_counter()-start
                return result
        return Timed()
    original_encode=datasets.write_json
    def timed_encode(value,emit):
        nonlocal encoding_seconds
        downstream=0.0
        def timed_emit(raw):
            nonlocal downstream
            start=time.perf_counter()
            try:emit(raw)
            finally:downstream+=time.perf_counter()-start
        start=time.perf_counter()
        try:original_encode(value,timed_emit)
        finally:encoding_seconds+=time.perf_counter()-start-downstream
    def timed_rows():
        nonlocal source_seconds
        source=iter(rows())
        while True:
            started=time.perf_counter()
            try:item=next(source)
            except StopIteration:
                source_seconds+=time.perf_counter()-started
                return
            source_seconds+=time.perf_counter()-started
            yield item
    def sync(path):
        nonlocal sync_seconds
        started=time.perf_counter()
        label='directory' if Path(path).is_dir() else 'file'
        counts[label]=counts.get(label,0)+1
        original(path)
        sync_seconds+=time.perf_counter()-started
    storage._sync=sync
    datasets.write_json=timed_encode
    datasets.zstandard.ZstdCompressor=compressor_factory
    started=time.perf_counter();cpu=time.process_time()
    try:
        with StageStore.create(source,DATASET_SCHEMA) as store:
            data=Datasets(store)
            before=time.perf_counter()
            sync_before=sync_seconds
            ref=data.import_rows('source',timed_rows())
            imported=time.perf_counter()-before
            import_sync=sync_seconds-sync_before
            session=store._session.memory_used
            before=time.perf_counter();store.complete(1);complete=time.perf_counter()-before
            count=store.read(lambda r:r.query('SELECT count FROM datasets'))[0][0]
            before=time.perf_counter()
            for _ in range(100):data.page(ref,limit=10,max_bytes=4096)
            reads=time.perf_counter()-before
        source_space=space([source]);source_sync=dict(counts)
        before=time.perf_counter()
        with StageStore.restore(source,target) as restored:
            restore=time.perf_counter()-before
            body_shared=(source/ref['artifact']).stat().st_ino==(target/ref['artifact']).stat().st_ino
            before=time.perf_counter()
            for ordinal,value in enumerate(rows()):assert Datasets(restored).get(ref,ordinal)==value
            verify=time.perf_counter()-before
        before=time.perf_counter()
        with StageStore.prepare_readonly(source,view,run_id='probe:complete:1') as reader:
            readonly=time.perf_counter()-before
            assert reader.read(lambda r:r.complete_step)==1
        combined=space([source,target,view])
        peak=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return {'platform':platform.platform(),'python':platform.python_version(),
                'records':count,'artifact_files':len(list((source/'artifacts').iterdir())),
                'source_space':source_space,'restored_space':space([target]),'readonly_space':space([view]),
                'combined_unique_allocated_bytes':combined['unique_allocated_bytes'],
                'combined_summed_allocated_bytes':combined['summed_allocated_bytes'],
                'body_shared_with_restore':body_shared,'readonly_root_files':len(list(view.glob('root.sqlite'))),
                'import_seconds':imported,'import_encode_seconds':encoding_seconds,'import_compress_seconds':compression_seconds,'import_source_decode_seconds':source_seconds,
                'import_explicit_fsync_seconds':import_sync,'import_sql_and_control_seconds':imported-encoding_seconds-compression_seconds-source_seconds-import_sync,'complete_seconds':complete,'restore_seconds':restore,
                'readonly_prepare_seconds':readonly,'100_pages_seconds':reads,'verify_seconds':verify,
                'session_bytes':session,'python_explicit_fsync_before_restore':source_sync,
                'python_explicit_fsync_all':counts,'all_values_equal':True,
                'wall_seconds':time.perf_counter()-started,'cpu_seconds':time.process_time()-cpu,
                'absolute_peak_rss_bytes':peak if platform.system()=='Darwin' else peak*1024,
                'limits':'fsync counts exclude native SQLite sync syscalls; OS page cache retained; one connection per dataset page; peak includes one decoded source value; timing is single trial'}
    finally:
        storage._sync=original
        datasets.write_json=original_encode
        datasets.zstandard.ZstdCompressor=original_compressor


def source_rows(path,limit):
    connection=apsw.Connection(str(Path(path)/'current.sqlite'),flags=apsw.SQLITE_OPEN_READONLY)
    try:
        for seq, in connection.execute('SELECT seq FROM fixture_entries ORDER BY seq LIMIT ?', (limit,)):
            body=bytearray()
            for compressed, in connection.execute('SELECT payload FROM fixture_chunks WHERE seq=? ORDER BY chunk',(seq,)):
                body.extend(zlib.decompress(compressed))
            yield json.loads(body)
    finally:connection.close()


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--source',type=Path);parser.add_argument('--limit',type=int,default=10000)
    parser.add_argument('--parent',type=Path);args=parser.parse_args()
    rows=(lambda:source_rows(args.source,args.limit)) if args.source else (lambda:({'id':i,'body':'汉🙂'*1000} for i in range(args.limit)))
    with tempfile.TemporaryDirectory(dir=args.parent) as folder:result=probe(folder,rows)
    result.update(source=str(args.source) if args.source else 'synthetic',limit=args.limit)
    print(json.dumps(result,ensure_ascii=False))
