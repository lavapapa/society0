"""实际 Chroma 与 sqlite-vec 的同向量、主体过滤及精确候选对照。"""
import argparse
import heapq
import json
import os
import platform
import resource
import sqlite3
import subprocess
import time
from pathlib import Path
import numpy as np


def rss():return int(subprocess.check_output(['ps','-o','rss=','-p',str(os.getpid())],text=True).strip())*1024


def probe(path,*,backend,count,dimensions=1024,query_count=20):
    baseline=rss();started=time.perf_counter();cpu=time.process_time();path=Path(path);path.mkdir()
    # 所有配置逐批使用相同 float32 输入，保留少量查询，不保存全量向量。
    first=np.random.default_rng(31415).random((256,dimensions),dtype=np.float32)
    query_ids=[3+i*10 for i in range(query_count)];queries=first[query_ids].copy();del first
    version=None;client=None
    if backend=='chroma':
        import chromadb
        from chromadb.config import Settings
        version=chromadb.__version__
        client=chromadb.PersistentClient(path=str(path),settings=Settings(anonymized_telemetry=False))
        collection=client.get_or_create_collection('comparison',metadata={'hnsw:space':'l2'},embedding_function=None)
        def add(start,rows):
            collection.add(ids=[str(i) for i in range(start,start+len(rows))],embeddings=rows,
                           metadatas=[{'actor':i%10} for i in range(start,start+len(rows))])
        def search(q):
            result=collection.query(query_embeddings=[q],n_results=20,where={'actor':3},include=['distances'])
            return list(zip(map(int,result['ids'][0]),result['distances'][0]))
    else:
        import sqlite_vec
        version=sqlite_vec.__version__
        connection=sqlite3.connect(path/'vectors.sqlite');connection.enable_load_extension(True);sqlite_vec.load(connection);connection.enable_load_extension(False)
        connection.execute('PRAGMA cache_size=-2048');connection.execute('PRAGMA mmap_size=0')
        if backend=='sqlite-vec0':
            connection.execute(f'CREATE VIRTUAL TABLE vectors USING vec0(id INTEGER PRIMARY KEY,actor INTEGER PARTITION KEY,embedding float[{dimensions}])')
            sql='SELECT id,distance FROM vectors WHERE embedding MATCH ? AND k=20 AND actor=3 ORDER BY distance'
        else:
            connection.execute('CREATE TABLE vectors(id INTEGER PRIMARY KEY,actor INTEGER NOT NULL,embedding BLOB NOT NULL)')
            connection.execute('CREATE INDEX vector_actor ON vectors(actor)')
            sql='SELECT id,vec_distance_l2(embedding,?) AS distance FROM vectors WHERE actor=3 ORDER BY distance,id LIMIT 20'
        def add(start,rows):
            connection.executemany('INSERT INTO vectors VALUES(?,?,?)',((i,i%10,rows[i-start].tobytes()) for i in range(start,start+len(rows))))
        def search(q):
            raw=q.tobytes();return [(identifier,distance*distance) for identifier,distance in connection.execute(sql,(raw,))]
    initialized=rss();before=time.perf_counter();rng=np.random.default_rng(31415)
    for start in range(0,count,256):add(start,rng.random((min(256,count-start),dimensions),dtype=np.float32))
    if client is None:connection.commit()
    built=time.perf_counter()-before;write_rss=rss();before=time.perf_counter()
    answers=[search(query) for query in queries];query_seconds=time.perf_counter()-before
    query_rss=rss();peak=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    measured_wall=time.perf_counter()-started;measured_cpu=time.process_time()-cpu
    # 验证单独计时：流式生成原向量，以 float64 平方 L2 得到精确 top20。
    verified=time.perf_counter();heaps=[[] for _ in queries];rng=np.random.default_rng(31415)
    for start in range(0,count,256):
        batch=rng.random((min(256,count-start),dimensions),dtype=np.float32)
        indices=np.array([i for i in range(start,start+len(batch)) if i%10==3])
        if not len(indices):continue
        scoped=batch[indices-start].astype(np.float64)
        for q,heap in zip(queries,heaps):
            distances=np.sum((scoped-q.astype(np.float64))**2,axis=1)
            for identifier,distance in zip(indices,distances):
                item=(-float(distance),-int(identifier))
                if len(heap)<20:heapq.heappush(heap,item)
                elif item>heap[0]:heapq.heapreplace(heap,item)
    exact=[sorted([(-identifier,-distance) for distance,identifier in heap],key=lambda item:(item[1],item[0])) for heap in heaps]
    overlaps=[];error=0.
    for found,wanted in zip(answers,exact):
        known=dict(wanted);overlaps.append(len(set(dict(found))&set(known))/len(known))
        for identifier,distance in found:
            if identifier in known:error=max(error,abs(distance-known[identifier]))
    result={'platform':platform.platform(),'python':platform.python_version(),'backend':backend,'backend_version':version,
        'sqlite_version':sqlite3.sqlite_version,'count':count,'dimensions':dimensions,'actors':10,'queries':len(queries),
        'rss_before_backend_bytes':baseline,'rss_initialized_bytes':initialized,'rss_after_write_bytes':write_rss,'rss_after_query_bytes':query_rss,
        'peak_before_validation_bytes':peak if platform.system()=='Darwin' else peak*1024,
        'build_seconds':built,'queries_seconds':query_seconds,'wall_before_validation_seconds':measured_wall,'cpu_before_validation_seconds':measured_cpu,
        'disk_bytes':sum(p.stat().st_size for p in path.rglob('*') if p.is_file()),
        'all_actor_scoped':all(i%10==3 for rows in answers for i,_ in rows),'minimum_top20_overlap':min(overlaps),
        'mean_top20_overlap':sum(overlaps)/len(overlaps),'max_distance_error':error,
        'validation_seconds':time.perf_counter()-verified,'query_ids':query_ids,'answers':answers,'exact_answers':exact,
        'limits':'sqlite distance squared to match Chroma squared L2; default vec0 chunk layout; OS cache retained; float32 canonical input'}
    if client is not None:client.close()
    else:connection.close()
    return result


if __name__=='__main__':
    import tempfile
    parser=argparse.ArgumentParser();parser.add_argument('--backend',required=True);parser.add_argument('--count',type=int,required=True);args=parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='society0-vector-comparison-') as directory:
        print(json.dumps(probe(Path(directory)/'index',backend=args.backend,count=args.count)))
