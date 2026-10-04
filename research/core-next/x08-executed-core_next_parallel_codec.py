"""完整 Python 值传入、原生编码压缩、结果传回和逐值验证；含进程 IPC。"""
import argparse
from collections import deque
from concurrent.futures import ThreadPoolExecutor,ProcessPoolExecutor
import json
import multiprocessing
import os
import platform
import random
import resource
import subprocess
import threading
import time
import zlib
from society0.kernel._json_chunks import write_json


def encode(value):
    cpu=time.process_time();chunks=[]
    write_json(value,lambda raw:chunks.append(zlib.compress(raw,3)))
    return chunks,time.process_time()-cpu


def numeric(value):
    cpu=time.process_time();rows=[]
    for column in value['columns']:
        mean=sum(column)/len(column)
        variance=sum((number-mean)**2 for number in column)/len(column)
        rows.append((mean,variance,min(column),max(column)))
    return rows,time.process_time()-cpu


def group_rss():
    process=subprocess.Popen(['ps','-axo','pid=,ppid=,rss='],stdout=subprocess.PIPE,text=True)
    output=process.communicate()[0];rows=[tuple(map(int,line.split())) for line in output.splitlines()]
    members={os.getpid()}
    while True:
        extended=members|{pid for pid,parent,_ in rows if parent in members and pid!=process.pid}
        if extended==members:break
        members=extended
    return sum(rss*1024 for pid,_,rss in rows if pid in members)


def probe(*,mode,workers=4,jobs=32,source_bytes=1048576,kernel='codec'):
    rng=random.Random(31415)
    source=[{'i':i,'amount':i/7,'body':rng.randbytes(max(1,source_bytes//128)).hex()} for i in range(64)]
    if kernel=='numeric':source=[[rng.random() for _ in range(source_bytes//(16*8))] for _ in range(16)]
    task=encode if kernel=='codec' else numeric
    expected=None if kernel=='codec' else numeric({'columns':source})[0]
    baseline=group_rss();peak=baseline;done=threading.Event();samples=0
    def sample():
        nonlocal peak,samples
        while not done.is_set():
            peak=max(peak,group_rss());samples+=1;done.wait(.02)
    sampler=threading.Thread(target=sample);sampler.start()
    child_start=resource.getrusage(resource.RUSAGE_CHILDREN)
    wall=time.perf_counter();cpu=time.process_time();output_bytes=0;verified=0;validation=0.;max_pending=0;worker_cpu=0.
    def consume(value,result):
        nonlocal output_bytes,verified,validation,worker_cpu
        chunks,seconds=result;worker_cpu+=seconds
        start=time.perf_counter()
        if kernel=='codec':
            output_bytes+=sum(map(len,chunks))
            assert json.loads(b''.join(zlib.decompress(chunk) for chunk in chunks))==value
        else:
            output_bytes+=len(json.dumps(chunks).encode())
            assert chunks==expected
        validation+=time.perf_counter()-start;verified+=1
    try:
        if mode=='serial':
            for i in range(jobs):
                value={'job':i,('records' if kernel=='codec' else 'columns'):source};consume(value,task(value))
            max_pending=1
        else:
            executor=ThreadPoolExecutor if mode=='threads' else ProcessPoolExecutor
            options={'max_workers':workers}
            if mode=='processes':options['mp_context']=multiprocessing.get_context('spawn')
            with executor(**options) as pool:
                pending=deque()
                for i in range(jobs):
                    if len(pending)>=workers*2:
                        value,future=pending.popleft();consume(value,future.result())
                    value={'job':i,('records' if kernel=='codec' else 'columns'):source};pending.append((value,pool.submit(task,value)))
                    max_pending=max(max_pending,len(pending))
                while pending:
                    value,future=pending.popleft();consume(value,future.result())
    finally:
        elapsed=time.perf_counter()-wall;parent_cpu=time.process_time()-cpu
        done.set();sampler.join();peak=max(peak,group_rss())
    child_end=resource.getrusage(resource.RUSAGE_CHILDREN)
    child_cpu=child_end.ru_utime+child_end.ru_stime-child_start.ru_utime-child_start.ru_stime
    return {'platform':platform.platform(),'python':platform.python_version(),'mode':mode,'kernel':kernel,'workers':workers,'jobs':jobs,
        'source_text_bytes':sum(len(row['body']) for row in source) if kernel=='codec' else 0,'numeric_values':sum(map(len,source)) if kernel=='numeric' else 0,'verified_jobs':verified,'output_bytes':output_bytes,
        'wall_seconds':elapsed,'parent_cpu_seconds':parent_cpu,
        'worker_cpu_sum_seconds':worker_cpu if mode=='processes' else None,
        'child_cpu_seconds':child_cpu,'total_cpu_seconds':parent_cpu+child_cpu,
        'validation_wall_seconds':validation,'group_initial_rss_bytes':baseline,'group_peak_rss_bytes':peak,
        'rss_samples':samples,'max_pending_jobs':max_pending,
        'limits':'all input pickle/output IPC and spawn startup included; 20ms sampled parent+descendant RSS; subprocess ps overhead included; validation included and overlaps worker computation; no SQL/world writes'}


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--mode',choices=['serial','threads','processes'],required=True)
    parser.add_argument('--kernel',choices=['codec','numeric'],default='codec');parser.add_argument('--workers',type=int,default=4);parser.add_argument('--jobs',type=int,default=32);args=parser.parse_args()
    print(json.dumps(probe(mode=args.mode,workers=args.workers,jobs=args.jobs,kernel=args.kernel)))
