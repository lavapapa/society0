"""同一主体文件入口保存原文、权限与跨激活结果。"""
from types import SimpleNamespace
import json
import pytest
from society0.kernel.actor_files import ActorFiles
from society0.kernel.activation import ActivationContext
from society0.kernel.interaction import Information, InteractionScope, Moment, Page, ResourceStat, Ref
from society0.kernel.threads import ThreadStore, THREAD_SCHEMA
from society0.kernel.storage import StageStore


def session(store, actor='a', time=0):
    scope=InteractionScope(actor,Moment(time,'phase'))
    information=Information(lambda *args:True).bound(scope)
    value=SimpleNamespace(actor=SimpleNamespace(id=actor),scope=scope,moment=scope.moment,information=information,cursors={},step=time+1,prepare_artifact=store.prepare_artifact)
    value.cursors['activation']=ActivationContext(value,messages=[{'role':'user','content':'完整中文🙂'}])
    return value


@pytest.mark.asyncio
async def test_root_and_context_original_reads(tmp_path):
    with StageStore.create(tmp_path/'run',THREAD_SCHEMA) as store:
        current=session(store);files=ActorFiles(current,ThreadStore(store))
        page=await files.ls('/')
        assert [item['path'] for item in page.items]==['/world','/context','/workspace','/results']
        chunk=await files.read('/context/messages.json')
        assert json.loads(chunk['data'])==[{'role':'user','content':'完整中文🙂'}]


@pytest.mark.asyncio
async def test_results_cross_activation_actor_owned_and_restored(tmp_path):
    with StageStore.create(tmp_path/'run',THREAD_SCHEMA) as store:
        threads=ThreadStore(store);first=session(store)
        tid=threads.open('a',Moment(0,'phase'),'decision');first.cursors['thread_id']=tid
        artifact=store.prepare_artifact((b'original output',));threads.register_artifact(tid,'shell-old/stdout.txt',artifact,actor='a')
        threads.close(tid,'completed');store.complete(1)
        later=session(store,time=1);later.cursors['thread_id']=threads.open('a',Moment(1,'phase'),'decision')
        files=ActorFiles(later,threads);page=await files.ls('/results')
        assert page.total==1
        assert (await files.read(page.items[0]['path']))['data']=='original output'
        denied=ActorFiles(session(store,actor='b'),threads)
        assert (await denied.ls('/results')).total==0
        with pytest.raises(KeyError):await denied.read(page.items[0]['path'])
    with StageStore.restore(tmp_path/'run',tmp_path/'restored') as store:
        files=ActorFiles(session(store,time=2),ThreadStore(store))
        assert (await files.read((await files.ls('/results')).items[0]['path']))['data']=='original output'


@pytest.mark.asyncio
async def test_context_extension_mount_is_generic(tmp_path):
    from society0.kernel.interaction import DocumentChunk
    class Notes:
        def revision(self):return 'notes-v1'
        def list(self,path,*,limit=100,cursor=None):
            if path!='/':raise NotADirectoryError(path)
            return Page([{'path':'/note.txt','kind':'file'}],1,None,self.revision())
        def read(self,path,*,offset=0,size=65536,expected_revision=None):
            if path!='/note.txt':raise FileNotFoundError(path)
            raw='第三认知插件完整材料🙂'.encode()
            return DocumentChunk(raw[offset:offset+size],len(raw),None,self.revision(),'note')
    with StageStore.create(tmp_path/'run',THREAD_SCHEMA) as store:
        current=session(store);current.cursors['activation'].mounts={'notes':Notes()}
        files=ActorFiles(current,ThreadStore(store))
        assert '/context/notes' in [item['path'] for item in (await files.ls('/context')).items]
        assert (await files.ls('/context/notes')).items[0]['path']=='/context/notes/note.txt'
        assert (await files.read('/context/notes/note.txt'))['data']=='第三认知插件完整材料🙂'


@pytest.mark.asyncio
async def test_find_no_matches_and_very_long_logical_path(tmp_path):
    from society0.kernel.interaction import DocumentChunk
    long='/context/notes/'+'甲'*30000+'.txt'
    class Notes:
        def revision(self):return 'stable'
        def list(self,path,*,limit=100,cursor=None):
            return Page([{'path':'/'+long.rsplit('/',1)[1],'kind':'file'}],1,None,'stable')
        def read(self,path,*,offset=0,size=65536,expected_revision=None):
            if path=='/':raise IsADirectoryError(path)
            return DocumentChunk(b'x'[offset:offset+size],1,None,'stable','note')
    with StageStore.create(tmp_path/'run',THREAD_SCHEMA) as store:
        current=session(store);threads=ThreadStore(store)
        current.cursors['thread_id']=threads.open('a',0,'decision')
        current.cursors['activation'].mounts={'notes':Notes()}
        files=ActorFiles(current,threads)
        empty=await files.find('*.absent','/context/notes')
        assert empty.items==[] and empty.total==0 and empty.next_cursor is None
        found=await files.find('*.txt','/context/notes')
        assert found.items==[{'path':long,'kind':'file'}] and found.total==1


@pytest.mark.asyncio
async def test_grep_original_multiblock_exact_locations_and_persistent_result(tmp_path):
    from society0.kernel.information_sql import SQLInformation,DocumentSpec
    body='首行\n'+'甲'*30000+'连续目标🙂\n末行目标\n'
    with StageStore.create(tmp_path/'run',(*THREAD_SCHEMA,'CREATE TABLE docs(id INTEGER PRIMARY KEY,body TEXT NOT NULL)'),
        initialize=lambda writer:writer.execute('INSERT INTO docs VALUES(1,?)',(body,))) as store:
        current=session(store);threads=ThreadStore(store)
        current.cursors['thread_id']=threads.open('a',0,'decision')
        current.information.information.mount('/docs',SQLInformation('docs',store,{'text':DocumentSpec('docs','id','body')}))
        files=ActorFiles(current,threads)
        found=await files.grep('目标','/world/docs/text')
        assert found['status']=='completed' and found['total']==2
        assert [item['line_number'] for item in found['items']]==[2,3]
        assert found['items'][0]['byte_offset']==len('首行\n'.encode())
        raw=[];offset=0
        while True:
            chunk=await files.read(found['result_path'],offset=offset,size=65536)
            raw.append(chunk['data']);offset=chunk['next_offset']
            if offset is None:break
        rows=[json.loads(row) for row in ''.join(raw).splitlines()]
        assert rows[0]['line']=='甲'*30000+'连续目标🙂\n'
        store.complete(1)
    with StageStore.restore(tmp_path/'run',tmp_path/'restored') as store:
        files=ActorFiles(session(store),ThreadStore(store))
        assert (await files.read(found['result_path'],encoding='base64'))['total_bytes']>65536


@pytest.mark.asyncio
@pytest.mark.parametrize('change',['source','access','binary'])
async def test_grep_failure_keeps_diagnostics_without_success_artifact(tmp_path,change):
    from society0.kernel.information_sql import SQLInformation,DocumentSpec
    from society0.kernel.interaction import Unavailable
    raw=b'needle\n'*12000 if change!='binary' else b'needle\xff\n'
    with StageStore.create(tmp_path/'run',(*THREAD_SCHEMA,
        'CREATE TABLE docs(id INTEGER PRIMARY KEY,body BLOB NOT NULL)',
        'CREATE TABLE access(id INTEGER PRIMARY KEY,n INTEGER NOT NULL)'),
        initialize=lambda w:(w.execute('INSERT INTO docs VALUES(1,?)',(raw,)),w.execute('INSERT INTO access VALUES(1,0)'))) as store:
        current=session(store);threads=ThreadStore(store);current.cursors['thread_id']=threads.open('a',0,'decision')
        info=Information(lambda scope,operation,ref:store.read(lambda view:view.query('SELECT n FROM access')[0][0])==0,access_dependencies=('access',))
        info.mount('/docs',SQLInformation('docs',store,{'text':DocumentSpec('docs','id','body')}));current.information=info.bound(current.scope)
        original=current.information.read;calls=[]
        class Bound:
            def __getattr__(self,name):return getattr(current_info,name)
            async def read(self,*args,**kwargs):
                chunk=await original(*args,**kwargs);calls.append(1)
                if len(calls)==2 and change!='binary':
                    store.transaction(lambda w:w.execute("UPDATE docs SET body=? WHERE id=1",(b'changed',)) if change=='source' else w.execute('UPDATE access SET n=1'))
                return chunk
        current_info=current.information;current.information=Bound();files=ActorFiles(current,threads)
        with pytest.raises((ValueError,Unavailable,RuntimeError)):await files.grep('needle','/world/docs/text')
        assert threads.list_artifacts(actor='a').total==0


@pytest.mark.asyncio
async def test_current_workspace_read_find_and_shell_context_share_files(tmp_path):
    from society0.kernel.composition import compose
    from tests.primary.test_kernel_workspace import plugins,shell
    from society0.kernel.plugins import Plugin
    async with compose(tmp_path/'run',[*plugins(),Plugin('threads',schema=THREAD_SCHEMA)]) as host:
        store=host.service('storage','store');threads=ThreadStore(store)
        one=shell(host,tmp_path);current=session(store,actor='alice');current.scope=one.scope
        current.cursors['thread_id']=threads.open('alice',0,'decision')
        files=ActorFiles(current,threads,shell=one);one.bind_files(files)
        assert (await one.execute('printf original > note')).exit_code==0
        read=await files.read('/workspace/note',offset=0,size=4)
        assert read['data']=='orig' and read['next_offset']==4
        await one.execute('cat /workspace/note')
        assert (await files.read('/workspace/note',expected_revision=read['revision']))['data']=='original'
        with pytest.raises(FileNotFoundError):await files.read('/workspace/missing')
        assert (await files.ls('/workspace')).items[0]['path']=='/workspace/note'
        assert (await files.find('note','/workspace')).total==1
        assert '完整中文' in (await one.execute('cat /context/messages.json')).stdout
        await one.execute('printf changed > note')
        with pytest.raises(ValueError):await files.read('/workspace/note',expected_revision=read['revision'])
        assert (await files.read('/workspace/note'))['data']=='changed'
        one.save_workspace();await one.aclose();await files.close();store.complete(1)


@pytest.mark.asyncio
async def test_find_cursor_ignores_unrelated_thread_and_keeps_all_files(tmp_path):
    from society0.kernel.information_sql import SQLInformation,DocumentSpec
    with StageStore.create(tmp_path/'run',(*THREAD_SCHEMA,'CREATE TABLE docs(id INTEGER PRIMARY KEY,body TEXT NOT NULL)'),
        initialize=lambda w:w.executemany('INSERT INTO docs VALUES(?,?)',[(i,'text') for i in range(4)])) as store:
        current=session(store);threads=ThreadStore(store);current.cursors['thread_id']=threads.open('a',0,'decision')
        current.information.information.mount('/docs',SQLInformation('docs',store,{'text':DocumentSpec('docs','id','body')}))
        files=ActorFiles(current,threads);first=await files.find('*','/world/docs/text',limit=1)
        other=threads.open('b',0,'decision');threads.append_message(other,{'role':'user','content':'unrelated'})
        items=first.items;cursor=first.next_cursor
        while cursor is not None:
            page=await files.find('*','/world/docs/text',limit=1,cursor=cursor)
            assert page.total==4;items+=page.items;cursor=page.next_cursor
        assert [item['path'] for item in items]==['/world/docs/text/'+str(i) for i in range(4)]
        stale=await files.find('*','/world/docs/text',limit=1)
        store.transaction(lambda w:w.execute("UPDATE docs SET body='changed' WHERE id=0"))
        with pytest.raises(ValueError):await files.find('*','/world/docs/text',cursor=stale.next_cursor)


@pytest.mark.asyncio
async def test_grep_cancel_drains_before_artifact_or_scope_close(tmp_path):
    import asyncio
    from society0.kernel.interaction import DocumentChunk
    started=asyncio.Event();released=[]
    class Notes:
        def revision(self):return 1
        def list(self,path,*,limit=100,cursor=None):return Page([{'path':'/note.txt','kind':'file'}],1,None,1)
        async def read(self,path,*,offset=0,size=65536,expected_revision=None):
            if path=='/':raise IsADirectoryError(path)
            if size==1:return DocumentChunk(b'x',2,None,1,'note')
            started.set()
            try:await asyncio.Event().wait()
            finally:released.append(True)
    with StageStore.create(tmp_path/'run',THREAD_SCHEMA) as store:
        current=session(store);threads=ThreadStore(store);current.cursors['thread_id']=threads.open('a',0,'decision')
        current.cursors['activation'].mounts={'notes':Notes()};files=ActorFiles(current,threads)
        task=asyncio.create_task(files.grep('x','/context/notes'))
        await started.wait();task.cancel()
        with pytest.raises(asyncio.CancelledError):await task
        assert released==[True] and threads.list_artifacts(actor='a').total==0
        await files.close()


@pytest.mark.asyncio
async def test_encoded_keys_share_read_ls_find_grep_and_bash_paths(tmp_path):
    from urllib.parse import quote
    from society0.kernel.information_sql import SQLInformation,DocumentSpec
    from society0.kernel.shell import ShellSession
    from society0.kernel.interaction import Actions
    keys=('a/b','有 空格','@at','100%','x%2Fy')
    with StageStore.create(tmp_path/'run',(*THREAD_SCHEMA,'CREATE TABLE docs(id TEXT PRIMARY KEY NOT NULL,body TEXT NOT NULL)'),
        initialize=lambda w:w.executemany('INSERT INTO docs VALUES(?,?)',[(key,'原文-'+key+'\n') for key in keys])) as store:
        current=session(store);threads=ThreadStore(store);current.cursors['thread_id']=threads.open('a',0,'decision')
        info=Information(lambda *a:True);info.mount('/docs',SQLInformation('docs',store,{'text':DocumentSpec('docs','id','body')}))
        current.information=info.bound(current.scope)
        shell=ShellSession(current.scope,info,Actions(lambda *a:True),result_dir=tmp_path/'out')
        files=ActorFiles(current,threads,shell=shell);shell.bind_files(files)
        listed={item['path'] for item in (await files.ls('/world/docs/text')).items}
        assert listed=={'/world/docs/text/'+quote(key,safe='') for key in keys}
        assert set((await shell.execute('ls /world/docs/text')).stdout.splitlines())=={quote(key,safe='') for key in keys}|{'@manifest.json'}
        for key in keys:
            path='/world/docs/text/'+quote(key,safe='')
            assert (await files.read(path))['data']==(await shell.execute('cat '+path)).stdout=='原文-'+key+'\n'
            manifest=json.loads((await files.read(path+'/@manifest.json'))['data'])
            assert manifest==json.loads((await shell.execute('cat '+path+'/@manifest.json')).stdout)
        assert {item['path'] for item in (await files.find('*','/world/docs/text')).items}==listed
        assert {item['path'] for item in (await files.grep('原文','/world/docs/text'))['items']}==listed
        await shell.aclose();await files.close()


@pytest.mark.asyncio
async def test_effective_cognitive_background_in_first_and_repeated_activation_files(tmp_path):
    from tests.primary.test_kernel_llm import setup,reply,call,FakeProvider
    from society0.kernel.cognition import CognitiveInput
    from society0.kernel.llm import LLMDriver
    store,threads,provider,driver,current,calls=setup(tmp_path,[reply(call('read-first','read',{'path':'/context/messages.json'})),reply(text='done')])
    from dataclasses import replace
    current=replace(current,actor=replace(current.actor,config=SimpleNamespace(persona='完整主体背景',state={})))
    driver.input_builder=CognitiveInput(threads,lambda s,cursor:([{'role':'user','content':'本轮完整增量'}],1),
        environment='长期环境背景',precision='明确精度说明')
    with store:
        await driver.run(current)
        tid=current.cursors['thread_id']
        first=json.loads(threads.get_tool_result(tid,'read-first')['content'])['data']
        assert '长期环境背景' in first and '明确精度说明' in first and '本轮完整增量' in first
        next_provider=FakeProvider(threads,[reply(call('read-next','read',{'path':'/context/messages.json'})),reply(text='done')])
        driver.provider=next_provider
        await driver.run(current)
        later=json.loads(threads.get_tool_result(tid,'read-next')['content'])['data']
        assert '长期环境背景' in later and '明确精度说明' in later
