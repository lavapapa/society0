"""独立进程对照同一原文的 range、Reader grep 与 Bashkit head 成本。"""
import asyncio,json,os,resource,subprocess,sys,tempfile,time
from pathlib import Path
from society0.kernel.information_fs import InformationFiles,search_reader
from society0.kernel.interaction import Information,InteractionScope,Moment,DocumentChunk,ResourceStat,Ref

async def run(mode,shape):
    length=8*1024*1024
    line=(b'x'*112+'关键词'.encode()+b'\n')
    body=(line*(length//len(line))+b'x'*(length%len(line))) if shape=='short' else b'x'*(length-10)+'关键词'.encode()+b'\n'
    counters={'read_calls':0,'source_bytes':0,'result_bytes':0}
    class Provider:
        def ref(self,path):return Ref('docs','document','1')
        def stat(self,scope,path):return ResourceStat('file',len(body),1,self.ref(path))
        async def read(self,scope,path,*,offset=0,size=65536):
            data=body[offset:offset+size]
            counters['read_calls']+=1;counters['source_bytes']+=len(data)
            end=offset+len(data)
            return DocumentChunk(data,len(body),end if end<len(body) else None,1,self.ref(path))
    scope=InteractionScope('a',Moment(1,'probe'));info=Information(lambda *a:True);info.mount('/docs',Provider());bound=info.bound(scope)
    started=time.perf_counter();cpu=time.process_time();disk=0
    if mode=='read':
        chunk=await bound.read('/docs/1',size=65536);counters['result_bytes']=len(chunk.data)
    elif mode=='grep':
        with tempfile.TemporaryFile() as output:
            async def read(offset,size):return (await bound.read('/docs/1',offset=offset,size=size,expected_revision=1)).data
            async def sink(number,offset,data):output.write(data);counters['result_bytes']+=len(data)
            result=await search_reader(read,sink,'关键词',literal=True)
            counters['matches']=result['matches'];disk=output.tell()
    else:
        from bashkit import Bash,FileSystem
        from society0_filesystem import callback_filesystem
        fs=InformationFiles(bound,scope)
        bash=Bash();bash.mount('/world',FileSystem.from_capsule(callback_filesystem(fs.callback)),read_only=True)
        result=await bash.execute('head -c 65536 /world/docs/1')
        assert result.exit_code==0,result.stderr
        counters['result_bytes']=len(result.stdout.encode());await fs.close()
    result={'mode':mode,'shape':shape,'input_bytes':len(body),**counters,'wall_seconds':time.perf_counter()-started,
        'cpu_seconds':time.process_time()-cpu,'peak_process_rss_bytes':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        'temporary_result_disk_bytes':disk,'platform':sys.platform}
    print(json.dumps(result))

if __name__=='__main__':
    if len(sys.argv)==3:asyncio.run(run(*sys.argv[1:]))
    else:
        for shape in ('short','long'):
            for mode in ('read','grep','head'):
                subprocess.run([sys.executable,__file__,mode,shape],check=True)
