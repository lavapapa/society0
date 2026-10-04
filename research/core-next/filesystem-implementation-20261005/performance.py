"""独立进程资源对照：当前投影、精确总数、活动主体、目录与原文搜索。"""
import asyncio
import base64
from collections import Counter
from dataclasses import asdict
import json
from pathlib import Path
import resource
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace

import apsw
from society0.kernel.actor_files import ActorFiles
from society0.kernel.actors import ACTOR_SCHEMA
from society0.kernel.activation import ActivationContext
from society0.kernel.information_sql import SQLInformation,DatasetSpec,DocumentSpec
from society0.kernel.interaction import Information,Actions,InteractionScope,Moment,Query
from society0.kernel.shell import ShellSession
from society0.kernel.storage import StageStore
from society0.kernel.threads import THREAD_SCHEMA,ThreadStore
from society0.kernel.workspace import WORKSPACE_SCHEMA,WorkspaceStore


async def run(case,factor):
    factor=int(factor);counters=Counter();sql=Counter()
    with tempfile.TemporaryDirectory() as root_name:
        root=Path(root_name);history=10000*factor if case in ('unrelated','count-scan','count-projection') else 10000
        actors=factor if case=='actors' else 1
        schema=(*ACTOR_SCHEMA,*THREAD_SCHEMA,*WORKSPACE_SCHEMA,
            'CREATE TABLE docs(id INTEGER PRIMARY KEY,owner TEXT NOT NULL,body BLOB NOT NULL)',
            'CREATE INDEX docs_owner_id ON docs(owner,id)',
            'CREATE TABLE history(id INTEGER PRIMARY KEY,owner TEXT NOT NULL,value INTEGER NOT NULL)',
            'CREATE INDEX history_owner_id ON history(owner,id)',
            'CREATE TABLE totals(owner TEXT PRIMARY KEY NOT NULL,total INTEGER NOT NULL)')
        body=('完整正文🙂\n'*1500).encode()
        def initialize(w):
            w.executemany('INSERT INTO actors(id,driver,active) VALUES(?,?,1)',[(str(i),'rule') for i in range(actors)])
            w.executemany('INSERT INTO docs VALUES(?,?,?)',[(i*20+j,str(i),body) for i in range(actors) for j in range(20)])
            w.executemany('INSERT INTO history VALUES(?,?,?)',((i,'0',i) for i in range(history)))
            w.execute('INSERT INTO totals VALUES(?,?)',('0',history))
        with StageStore.create(root/'run',schema,initialize=initialize) as store:
            threads=ThreadStore(store);info=Information(lambda *a:True)
            access=lambda scope:('owner=?',(scope.actor,))
            provider=SQLInformation('docs',store,{'rows':DatasetSpec('docs','id',('id','owner'),authorize=access,documents=()),
                'text':DocumentSpec('docs','id','body',authorize=access)})
            info.mount('/docs',provider)
            original=provider.read
            async def measured_read(*args,**kwargs):
                chunk=await original(*args,**kwargs);counters['source_callbacks']+=1;counters['source_bytes']+=len(chunk.data);return chunk
            provider.read=measured_read
            sessions=[];files=[];shell=None
            for i in range(actors):
                scope=InteractionScope(str(i),Moment(1,'probe'))
                current=SimpleNamespace(actor=SimpleNamespace(id=str(i)),scope=scope,moment=scope.moment,step=1,
                    information=info.bound(scope),cursors={},prepare_artifact=store.prepare_artifact)
                current.cursors['activation']=ActivationContext(current)
                current.cursors['thread_id']=threads.open(str(i),scope.moment,'decision');sessions.append(current)
            repeats=10
            if case=='workspace':
                scope=sessions[0].scope;workspace=WorkspaceStore(store);lease=workspace.open(scope)
                count=1000*factor
                from bashkit import Bash
                lease.save(Bash().snapshot(exclude_filesystem=True),{'removed':[],'entries':[{'path':'/'+str(i).zfill(6),'kind':'file','mode':420,'modified_ns':0,'created_ns':0,'content':b'complete original'} for i in range(count)]})
                shell=ShellSession(scope,info,Actions(lambda *a:True),result_dir=root/'shell',workspace=workspace)
                files=[ActorFiles(sessions[0],threads,shell=shell)]
            if case.startswith('grep'):
                length=1024*1024
                if case=='grep-long':body=b'x'*(length-8)+b'needle\n'
                else:body=(b'needle full original line\n' if case=='grep-dense' else b'no matches in original\n')
                if case!='grep-long':body=body*(length//len(body))
                store.transaction(lambda w:w.execute('UPDATE docs SET body=? WHERE id=0',(body,)))
                files=[ActorFiles(sessions[0],threads)];repeats=1
            if case.startswith('count'):
                spec=DatasetSpec('history','id',('id','value'),authorize=access,
                    base_count=(lambda scope:('SELECT total FROM totals WHERE owner=?',(scope.actor,))) if case=='count-projection' else None)
                latest=SQLInformation('history',store,{'prices':spec})
            def profile(event):
                sql['statements']+=1
                for name in ('SQLITE_STMTSTATUS_VM_STEP','SQLITE_STMTSTATUS_FULLSCAN_STEP','SQLITE_STMTSTATUS_SORT'):
                    sql[name]+=event['stmt_status'].get(name,0)
            store.read(lambda view:None)
            store._connection.trace_v2(apsw.SQLITE_TRACE_PROFILE,profile)
            store._reader._connection.trace_v2(apsw.SQLITE_TRACE_PROFILE,profile)
            before_disk=sum(p.stat().st_size for p in root.rglob('*') if p.is_file())
            wall=time.perf_counter();cpu=time.process_time()
            for _ in range(repeats):
                if case in ('unrelated','actors'):
                    for i,current in enumerate(sessions):
                        f=ActorFiles(current,threads);files.append(f)
                        page=await f.ls('/world/docs/rows',limit=5)
                        assert page.total==21 and len(page.items)==5
                        chunk=await f.read('/world/docs/text/'+str(i*20),size=64,encoding='base64')
                        assert base64.b64decode(chunk['data'])==body[:64]
                        counters['result_bytes']+=64
                        query=await provider.query(current.scope,'/docs/rows',Query(fields=('id','owner'),order=(('id','desc'),),limit=5))
                        assert [item['id'] for item in query.items]==list(range(i*20+19,i*20+14,-1))
                elif case.startswith('count'):
                    page=await latest.query(sessions[0].scope,'/history/prices',Query(order=(('id','desc'),),limit=5))
                    assert page.total==history and [item['id'] for item in page.items]==list(range(history-1,history-6,-1))
                    counters['result_bytes']+=len(json.dumps(page.items,default=asdict).encode())
                elif case=='workspace':
                    page=await files[0].ls('/workspace',limit=5)
                    assert page.total==1000*factor and len(page.items)==5
                else:
                    result=await files[0].grep('needle','/world/docs/text/0',literal=True)
                    expected=sum(b'needle' in line for line in body.splitlines())
                    assert result['total']==expected
                    reference=result['result_path'][9:];from urllib.parse import unquote
                    artifact=threads.lookup_actor_artifact(unquote(reference),actor='0')
                    total=(store.path/artifact).stat().st_size
                    counters['sink_callbacks']=expected;counters['sink_transport_batches']=result['sink_batches'];counters['result_bytes']=total
            elapsed=time.perf_counter()-wall;cpu_elapsed=time.process_time()-cpu
            store._connection.trace_v2(0,None)
            store._reader._connection.trace_v2(0,None)
            disk=sum(p.stat().st_size for p in root.rglob('*') if p.is_file())
            if case.startswith('grep'):
                with (store.path/artifact).open('rb') as output_file:
                    originals=(line for line in body.splitlines(keepends=True) if b'needle' in line)
                    for row,original in zip(output_file,originals,strict=True):assert json.loads(row)['line'].encode()==original
            output={'case':case,'factor':factor,'history_rows':history,'active_actors':actors,'repeats':repeats,
                'wall_seconds':elapsed,'cpu_seconds':cpu_elapsed,'peak_process_rss_bytes':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
                'disk_before_bytes':before_disk,'temporary_and_artifact_disk_delta_bytes':disk-before_disk,
                **counters,'sql':dict(sql),'platform':sys.platform}
            for f in files:await f.close()
            if shell is not None:await shell.aclose()
            print(json.dumps(output))


if __name__=='__main__':
    if len(sys.argv)==3:asyncio.run(run(*sys.argv[1:]))
    else:
        for case in ('unrelated','count-scan','count-projection','actors','workspace'):
            for factor in (1,10):subprocess.run([sys.executable,__file__,case,str(factor)],check=True)
        for case in ('grep-empty','grep-dense','grep-long'):subprocess.run([sys.executable,__file__,case,'1'],check=True)
