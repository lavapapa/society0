"""隔离临时目录中的原生同步、读连接与事件循环成本探针。"""
from __future__ import annotations
import argparse
import asyncio
import base64
import json
import os
from pathlib import Path
import platform
import random
import resource
import statistics
import tempfile
import time
from unittest.mock import patch

import apsw
from society0.kernel.storage import StageStore,StageReader
from society0.kernel.threads import ThreadStore,THREAD_SCHEMA


class SyncFile(apsw.VFSFile):
    def __init__(self,base,name,flags,stats):
        super().__init__(base,name,flags)
        self.stats=stats
    def xSync(self,flags):
        start=time.perf_counter()
        try:return super().xSync(flags)
        finally:
            self.stats['count']+=1
            self.stats['seconds']+=time.perf_counter()-start


class SyncVFS(apsw.VFS):
    def __init__(self):
        self.base=apsw.vfs_names()[0]
        self.stats={'count':0,'seconds':0.0}
        super().__init__('core-next-sync-probe',self.base,makedefault=True)
    def xOpen(self,name,flags):return SyncFile(self.base,name,flags,self.stats)


def directory_bytes(path):return sum(p.stat().st_size for p in path.rglob('*') if p.is_file())


def durability_probe(path,mode,rounds=30):
    vfs=SyncVFS()
    try:
        with StageStore.create(path,THREAD_SCHEMA) as store:
            store._connection.execute('PRAGMA synchronous='+mode)
            threads=ThreadStore(store)
            thread=threads.open('actor',0,'decision')
            before=dict(vfs.stats)
            sync={'count':0,'seconds':0.0}
            original_fsync=os.fsync
            def fsync(fd):
                start=time.perf_counter()
                try:return original_fsync(fd)
                finally:
                    sync['count']+=1
                    sync['seconds']+=time.perf_counter()-start
            cpu=time.process_time();wall=time.perf_counter()
            with patch('os.fsync',fsync):
                for index in range(rounds):
                    threads.append_message(thread,{'role':'user','content':'input '+str(index)})
                    threads.record_request(thread,provider_options={'model':'probe'},physical_request_id=str(index))
                    threads.event(thread,'provider_response',{'content':'raw response '+str(index)})
                    threads.append_message(thread,{'role':'assistant','content':'answer '+str(index)})
                    threads.save_tool_result(thread,{'id':str(index),'function':{'name':'act','arguments':'{}'}},'full tool result')
                    artifact=store.prepare_artifact([b'full stdout\n',b'full stderr\n'])
                    threads.register_artifact(thread,'result:'+str(index),artifact,actor='actor')
                short_wall=time.perf_counter()-wall;short_cpu=time.process_time()-cpu
                short_sync={'count':vfs.stats['count']-before['count'],'seconds':vfs.stats['seconds']-before['seconds']}
                artifact_sync=dict(sync)
                start=time.perf_counter();store.complete(1);complete_wall=time.perf_counter()-start
            result={'mode':mode,'rounds':rounds,'messages':len(threads.read_messages(thread)),
                    'complete_step':store.complete_step,'short_transactions':rounds*6,
                    'short_wall_seconds':short_wall,'short_cpu_seconds':short_cpu,
                    'short_transaction_sync_count':short_sync['count'],'short_transaction_sync_seconds':short_sync['seconds'],
                    'artifact_fsync_count':artifact_sync['count'],'artifact_fsync_seconds':artifact_sync['seconds'],
                    'component_fsync_count':sync['count']-artifact_sync['count'],
                    'component_fsync_seconds':sync['seconds']-artifact_sync['seconds'],
                    'complete_wall_seconds':complete_wall,'live_directory_bytes':directory_bytes(Path(path))}
        result['closed_directory_bytes']=directory_bytes(Path(path))
        with StageStore.restore(path,Path(path).with_name(Path(path).name+'-restored')) as restored:
            assert len(ThreadStore(restored).read_messages(thread))==rounds*3
        return result
    finally:
        apsw.set_default_vfs(vfs.base)
        vfs.unregister()


def reader_probe(path,tables=20,iterations=100):
    schema=[f'CREATE TABLE table_{index}(id INTEGER PRIMARY KEY,value INTEGER)' for index in range(tables)]
    with StageStore.create(path,schema,initialize=lambda writer:writer.execute('INSERT INTO table_0 VALUES(1,7)')) as store:
        native=apsw.Connection
        output={'tables':tables,'iterations':iterations}
        for reuse in (False,True):
            count=[0]
            def connect(*args,**kwargs):
                count[0]+=1
                return native(*args,**kwargs)
            read=store.read if reuse else StageReader(path).read
            wall=time.perf_counter();cpu=time.process_time()
            with patch('apsw.Connection',connect):
                total=sum(read(lambda view:view.query('SELECT value FROM table_0 WHERE id=1')[0][0]) for _ in range(iterations))
            output['reused' if reuse else 'fresh']={'wall_seconds':time.perf_counter()-wall,'cpu_seconds':time.process_time()-cpu,'connections':count[0],'sum':total}
        return output


async def loop_probe(path,size,iterations=10):
    rng=random.Random(37)
    body=base64.b64encode(rng.randbytes(size*3//4)).decode()
    samples=[]
    running=True
    async def observer():
        previous=time.perf_counter()
        while running:
            await asyncio.sleep(0)
            now=time.perf_counter();samples.append(now-previous);previous=now
    with StageStore.create(path,THREAD_SCHEMA) as store:
        threads=ThreadStore(store);thread=threads.open('a',0,'decision')
        task=asyncio.create_task(observer());await asyncio.sleep(0)
        cpu=time.process_time();wall=time.perf_counter()
        for _ in range(iterations):
            threads.append_message(thread,{'role':'user','content':body})
            await asyncio.sleep(0)
        elapsed=time.perf_counter()-wall;cpu_elapsed=time.process_time()-cpu
        running=False;await task
        ordered=sorted(samples)
        return {'raw_body_bytes':len(body),'iterations':iterations,'wall_seconds':elapsed,'cpu_seconds':cpu_elapsed,
                'loop_max_seconds':max(samples),'loop_median_seconds':statistics.median(samples),
                'loop_p95_seconds':ordered[min(len(ordered)-1,int(len(ordered)*.95))],
                'peak_rss_native':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
                'rss_native_unit':'bytes' if platform.system()=='Darwin' else 'KiB'}


def nested_reader_probe(path):
    """实际产品复用外层空闲连接，嵌套读取独立连接保留不同快照。"""
    with StageStore.create(path,['CREATE TABLE item(id INTEGER PRIMARY KEY,value INTEGER)'],
                           initialize=lambda writer:writer.execute('INSERT INTO item VALUES(1,7)')) as store:
        native=apsw.Connection;connections=0
        def connect(*args,**kwargs):
            nonlocal connections
            connections+=1
            return native(*args,**kwargs)
        result={}
        def outer(view):
            before=view.query('SELECT value FROM item')[0][0]
            store.transaction(lambda writer:writer.execute('UPDATE item SET value=9'))
            result['nested']=store.read(lambda nested:nested.query('SELECT value FROM item')[0][0])
            return [before,view.query('SELECT value FROM item')[0][0]]
        with patch('apsw.Connection',connect):
            result['outer']=store.read(outer)
            result['next_request']=store.read(lambda view:view.query('SELECT value FROM item')[0][0])
        result['connections']=connections
        return result


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output',required=True);args=parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='core-next-persistence-') as temporary:
        root=Path(temporary).resolve()
        result={'platform':platform.platform(),'python':platform.python_version(),'sqlite':apsw.sqlitelibversion(),
                'cpu_count':os.cpu_count(),'filesystem_path':temporary,
                'durability':[durability_probe(root/mode,mode) for mode in ('FULL','NORMAL')],
                'reader':[reader_probe(root/str(size),tables=size) for size in (20,200,1000)],
                'event_loop':[asyncio.run(loop_probe(root/('loop'+str(size)),size)) for size in (131072,1048576,10485760)]}
    Path(args.output).write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')


if __name__=='__main__':main()
