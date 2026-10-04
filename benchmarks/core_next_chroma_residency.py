"""实际持久化 Chroma 的单 collection 驻留；每个配置在独立进程执行。"""
import argparse
import gc
import json
import platform
import resource
import time
from pathlib import Path
import chromadb
from chromadb.config import Settings
import numpy as np
import os
import subprocess


def probe(path,*,count,dimensions=1024,batch_size=256,memory_limit=0):
    def rss():
        return int(subprocess.check_output(['ps','-o','rss=','-p',str(os.getpid())],text=True).strip())*1024
    started=time.perf_counter();cpu=time.process_time();baseline=rss()
    settings=Settings(anonymized_telemetry=False)
    if memory_limit:
        settings.chroma_memory_limit_bytes=memory_limit
        settings.chroma_segment_cache_policy='LRU'
    client=chromadb.PersistentClient(path=str(path),settings=settings)
    collection=client.get_or_create_collection('residency-probe',embedding_function=None,metadata={'hnsw:space':'l2'})
    initialized=rss();write_started=time.perf_counter()
    reference=None
    try:
        rng=np.random.default_rng(31415)
        for start in range(0,count,batch_size):
            end=min(count,start+batch_size)
            batch=rng.random((end-start,dimensions),dtype=np.float32)
            if start==0:reference=batch[3].copy()
            collection.add(ids=[f'vector-{i}' for i in range(start,end)],embeddings=batch,
                           metadatas=[{'actor':'actor-'+str(i%10)} for i in range(start,end)])
        del batch
        gc.collect()
        written=rss();write_seconds=time.perf_counter()-write_started
        query_started=time.perf_counter()
        answer=collection.query(query_embeddings=[reference],n_results=10,include=['distances'])
        query_seconds=time.perf_counter()-query_started
        actual=collection.get(ids=['vector-3'],include=['embeddings'])['embeddings'][0]
        equal=bool(np.array_equal(actual,reference))
        peak=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        result={'platform':platform.platform(),'python':platform.python_version(),'chroma':chromadb.__version__,
                'backend':type(client._server).__module__+'.'+type(client._server).__name__,
                'settings_memory_limit_bytes':settings.chroma_memory_limit_bytes,
                'settings_segment_cache_policy':settings.chroma_segment_cache_policy,
                'native_hnsw_cache_entries':getattr(client._server,'hnsw_cache_size',None),
                'collection_configuration':collection.configuration,'count':collection.count(),'dimensions':dimensions,
                'input_batch_bytes':batch_size*dimensions*4,'rss_before_client_bytes':baseline,
                'rss_initialized_bytes':initialized,'rss_after_write_bytes':written,'rss_after_query_bytes':rss(),
                'peak_rss_bytes':peak if platform.system()=='Darwin' else peak*1024,
                'write_seconds':write_seconds,'query_seconds':query_seconds,'query_first':answer['ids'][0][0],
                'vector_values_equal':equal,'disk_bytes':sum(p.stat().st_size for p in Path(path).rglob('*') if p.is_file()),
                'wall_seconds':time.perf_counter()-started,'cpu_seconds':time.process_time()-cpu}
    finally:
        client._system.stop()
    return result


if __name__=='__main__':
    import tempfile
    parser=argparse.ArgumentParser();parser.add_argument('--count',type=int,required=True)
    parser.add_argument('--dimensions',type=int,default=1024);parser.add_argument('--memory-limit',type=int,default=0)
    args=parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='society0-chroma-residency-') as directory:
        print(json.dumps(probe(Path(directory),count=args.count,dimensions=args.dimensions,memory_limit=args.memory_limit)))
