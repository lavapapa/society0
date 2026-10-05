"""正式 shell 文件路径的目录请求与未改正文成本；每个规模独立进程。"""
import asyncio
import json
import resource
import subprocess
import sys
import tempfile
import time
from pathlib import Path

async def measure(count):
    from society0.kernel.actors import ActorRecord,actor_plugin
    from society0.kernel.composition import compose
    from society0.kernel.plugins import Plugin
    from society0.kernel.workspace import workspace_plugin
    from society0.kernel.interaction import Actions,Information,InteractionScope,Moment
    from society0.kernel.information_sql import SQLInformation,DocumentSpec
    from society0.kernel.shell import ShellSession
    with tempfile.TemporaryDirectory() as directory:
        root=Path(directory).resolve()
        def initialize(writer):
            for i in range(count):writer.execute('INSERT INTO docs VALUES(?,?)',(i,b'original body'*80))
        plugins=[actor_plugin({'rule':lambda record:None},records=[ActorRecord(str(i),'rule') for i in range(1000)]),workspace_plugin(),
            Plugin('docs',schema=('CREATE TABLE docs(id INTEGER PRIMARY KEY,body BLOB NOT NULL)',),initialize=initialize)]
        async with compose(root/'run',plugins) as host:
            store=host.service('storage','store');workspace=host.service('workspace','workspace');info=Information(lambda *a:True)
            provider=SQLInformation('docs',store,{'texts':DocumentSpec('docs','id','body')});info.mount('/docs',provider)
            read_bytes=[];original_read=provider.read
            async def read(*args,**kwargs):
                result=await original_read(*args,**kwargs);read_bytes.append(len(result.data));return result
            provider.read=read
            def new(step):return ShellSession(InteractionScope('0',Moment(step,'work')),info,Actions(lambda *a:True),result_dir=root/'out',workspace=workspace)
            shell=new(1);start=time.perf_counter();rss=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            result=await shell.execute('ls /world/docs/texts')
            elapsed=time.perf_counter()-start;peak=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            assert result.exit_code==0 and sum(read_bytes)==0
            output=(shell.result_dir/'output/1.stdout').read_text().splitlines();assert len(output)==count
            await shell.aclose()
            lease=workspace.open(InteractionScope('0',Moment(1,'work')))
            lease.save(b'',{'removed':[],'entries':[{'path':'/cold','kind':'file','mode':420,'modified_ns':0,'created_ns':0,'content':b'x'*(8*1024*1024)}]})
            # 首次 shell state 尚未写入：删除空占位，仅保留已经规范登记的文件。
            store.transaction(lambda writer:writer.execute('DELETE FROM workspace_heads WHERE actor=?',('0',)))
            original=store.prepare_artifact;prepared=[];loaded=[]
            original_artifact_read=store.read_artifact
            def artifact_read(*args,**kwargs):
                data,total=original_artifact_read(*args,**kwargs);loaded.append(len(data));return data,total
            store.read_artifact=artifact_read
            def prepare(chunks):
                def tracked():
                    for chunk in chunks:prepared.append(len(chunk));yield chunk
                return original(tracked())
            store.prepare_artifact=prepare
            timings=[]
            for step in range(10):
                shell=new(step+2);started=time.perf_counter();await shell.execute('true');shell.save_workspace();await shell.aclose();timings.append(time.perf_counter()-started)
            assert sum(prepared)==0 and sum(loaded)==0
            return {'directory_entries':count,'directory_seconds':elapsed,'directory_peak_increment_bytes':peak-rss,'process_peak_rss_bytes':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,'directory_body_bytes_read':sum(read_bytes),'directory_output_bytes':result.stdout_total_bytes,'actors':1000,'active_shells':1,'unchanged_file_bytes':8*1024*1024,'unchanged_activations':10,'unchanged_artifact_bytes':sum(prepared),'unchanged_artifact_bytes_read':sum(loaded),'unchanged_seconds':timings}

if __name__=='__main__':
    if len(sys.argv)>1:print(json.dumps(asyncio.run(measure(int(sys.argv[1])))))
    else:
        results=[]
        for count in (100,10000,50000):
            result=subprocess.run([sys.executable,__file__,str(count)],capture_output=True,text=True,check=True)
            results.append(json.loads(result.stdout))
        print(json.dumps({'format':1,'platform':sys.platform,'cases':results},indent=2))
