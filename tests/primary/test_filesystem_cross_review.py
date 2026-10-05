"""非作者审查：搜索水位必须明确覆盖选中范围。"""
import pytest
from society0.kernel.interaction import Information,InteractionScope,Moment,ResourceStat,Ref

@pytest.mark.asyncio
@pytest.mark.parametrize('revision',[None,'directory-only-v1'])
async def test_search_requires_explicit_scope_revision_instead_of_node_stat(revision):
    class Provider:
        def ref(self,path):return Ref('third','directory',path)
        def stat(self,scope,path):return ResourceStat('directory',None,revision,self.ref(path))
    information=Information(lambda *args:True);information.mount('/third',Provider())
    with pytest.raises(ValueError,match='search_revision'):
        await information.bound(InteractionScope('a',Moment(1,'review'))).search_revision('/third')

@pytest.mark.asyncio
async def test_search_refuses_provider_missing_declared_scope_watermark():
    class Provider:
        def ref(self,path):return Ref('third','directory',path)
        def search_revision(self,scope,path):return None
    information=Information(lambda *args:True);information.mount('/third',Provider())
    with pytest.raises(ValueError,match='search_revision'):
        await information.bound(InteractionScope('a',Moment(1,'review'))).search_revision('/third')

@pytest.mark.asyncio
async def test_schema_file_is_discoverable_readable_and_empty_dataset_safe(tmp_path):
    import json
    from society0.kernel.storage import StageStore
    from society0.kernel.information_sql import SQLInformation,DatasetSpec
    from society0.kernel.information_fs import InformationFiles
    with StageStore.create(tmp_path/'run',('CREATE TABLE docs(id INTEGER PRIMARY KEY,value TEXT)',)) as store:
        info=Information(lambda *args:True);info.mount('/docs',SQLInformation('docs',store,{'rows':DatasetSpec('docs','id',('id','value'))}))
        scope=InteractionScope('a',Moment(1,'review'));files=InformationFiles(info.bound(scope),scope)
        listing=await files.callback('list','/docs/rows')
        assert '@schema.json' in [entry[0] for entry in listing]
        schema=json.loads(await files.callback('read','/docs/rows/@schema.json'))
        assert schema['fields']==['id','value'] and schema['logical_path']=='/world/docs/rows'
        assert schema['field_metadata']['value']=={'type':'TEXT','description':None}
        from society0.kernel.interaction import Query
        for example in schema['query_examples']:
            assert (await info.bound(scope).query('/docs/rows',Query(**example['query']))).total==0
        assert (await files.callback('stat','/docs/rows/@schema.json'))[0]=='file'
        await files.close();await info.close()

@pytest.mark.asyncio
async def test_sql_spool_rechecks_access_and_closes_on_interleaved_identity(tmp_path):
    from society0.kernel.storage import StageStore
    from society0.kernel.information_sql import SQLInformation,DatasetSpec
    from society0.kernel.interaction import Unavailable
    allowed={'a':True,'b':True}
    with StageStore.create(tmp_path/'run',('CREATE TABLE docs(id TEXT PRIMARY KEY NOT NULL,value TEXT)',),initialize=lambda w:w.execute('INSERT INTO docs VALUES(?,?)',('a/b%中文','完整🙂'*30000))) as store:
        source=SQLInformation('docs',store,{'rows':DatasetSpec('docs','id',('id','value'))})
        info=Information(lambda scope,operation,target:allowed[scope.actor]);info.mount('/docs',source)
        scope_a=InteractionScope('a',Moment(1,'review'));scope_b=InteractionScope('b',Moment(1,'review'))
        from urllib.parse import quote
        path='/docs/rows/'+quote('a/b%中文',safe='')
        first=await info.bound(scope_a).read(path,size=16);old=source._record_source[1]
        assert len(first.data)==16
        await info.bound(scope_b).read(path,size=16)
        assert old.closed
        allowed['b']=False
        with pytest.raises(Unavailable):await info.bound(scope_b).read(path,size=16)
        scope_a.close()
        with pytest.raises(Exception):await info.bound(scope_a).read(path,size=16)
        current=source._record_source[1];await info.close();assert current.closed

@pytest.mark.asyncio
async def test_original_special_key_has_same_path_through_projection(tmp_path):
    from society0.kernel.storage import StageStore
    from society0.kernel.information_sql import SQLInformation,DocumentSpec
    from society0.kernel.information_fs import InformationFiles
    from urllib.parse import quote
    body='完整🙂文档'.encode();key='a/b%中文@schema.json'
    with StageStore.create(tmp_path/'run',('CREATE TABLE docs(id TEXT PRIMARY KEY NOT NULL,body BLOB)',),initialize=lambda w:w.execute('INSERT INTO docs VALUES(?,?)',(key,body))) as store:
        info=Information(lambda *args:True);info.mount('/docs',SQLInformation('docs',store,{'text':DocumentSpec('docs','id','body')}))
        scope=InteractionScope('a',Moment(1,'review'));bound=info.bound(scope);files=InformationFiles(bound,scope)
        path='/docs/text/'+quote(key,safe='')
        assert (await bound.list_files('/docs/text')).items[0]['path']==path
        assert path.split('/')[-1] in [entry[0] for entry in await files.callback('list','/docs/text')]
        assert await files.callback('read',path)==(await bound.read(path)).data==body
        await files.close();await info.close()

@pytest.mark.asyncio
async def test_native_sink_cancellation_drains_before_return():
    import asyncio
    from society0.kernel.information_fs import search_reader
    entered=asyncio.Event();finished=asyncio.Event();events=[]
    async def read(offset,size):return b'needle\nneedle\n'[offset:offset+size]
    async def sink(*args):
        entered.set()
        try:await asyncio.Event().wait()
        finally:finished.set();events.append('drained')
    task=asyncio.create_task(search_reader(read,sink,'needle'))
    waiting=asyncio.create_task(entered.wait())
    done,_=await asyncio.wait((task,waiting),timeout=5,return_when=asyncio.FIRST_COMPLETED)
    if task in done:
        waiting.cancel();await asyncio.gather(waiting,return_exceptions=True)
        await task
    assert waiting in done
    task.cancel()
    with pytest.raises(asyncio.CancelledError):await task
    assert finished.is_set() and events==['drained']

@pytest.mark.asyncio
@pytest.mark.parametrize('selected',[True,False])
async def test_grep_freezes_all_selected_mounts_and_ignores_other_mounts(tmp_path,selected):
    from society0.kernel.storage import StageStore
    from society0.kernel.threads import THREAD_SCHEMA,ThreadStore
    from society0.kernel.interaction import Page,DocumentChunk
    from society0.kernel.actor_files import ActorFiles
    from tests.primary.test_kernel_actor_files import session
    versions={'first':1,'second':1};changed=[]
    class Provider:
        def __init__(self,name):self.name=name
        def ref(self,path):return Ref(self.name,'file',path)
        def search_revision(self,scope,path):return versions[self.name]
        def stat(self,scope,path):return ResourceStat('file' if path.endswith('.txt') else 'directory',7 if path.endswith('.txt') else None,versions[self.name],self.ref(path))
        def list_files(self,scope,path,*,limit,cursor):return Page([{'path':'/'+self.name+'/note.txt','kind':'file'}],1,None,versions[self.name])
        def read(self,scope,path,*,offset,size):
            if self.name=='first' and size>1 and not changed:
                versions['second']+=1;changed.append(True)
            data=b'needle\n'[offset:offset+size]
            return DocumentChunk(data,7,offset+len(data) if offset+len(data)<7 else None,versions[self.name],self.ref(path))
    with StageStore.create(tmp_path/'run',THREAD_SCHEMA) as store:
        current=session(store);threads=ThreadStore(store);current.cursors['thread_id']=threads.open('a',0,'decision')
        information=Information(lambda *args:True)
        for name in versions:information.mount('/'+name,Provider(name))
        current.information=information.bound(current.scope);files=ActorFiles(current,threads)
        if selected:
            with pytest.raises(ValueError,match='changed'):await files.grep('needle','/world')
            assert threads.list_artifacts(actor='a').total==0
        else:
            result=await files.grep('needle','/world/first');assert result['status']=='completed' and result['total']==1
        await files.close();await information.close()


@pytest.mark.asyncio
async def test_dataset_registration_exposes_optional_schema_notes_and_query_example(tmp_path):
    from society0.kernel.storage import StageStore
    from society0.kernel.information_sql import SQLInformation,DatasetSpec
    from society0.kernel.interaction import Query
    with StageStore.create(tmp_path/'run',('CREATE TABLE docs(id INTEGER PRIMARY KEY,value TEXT)',)) as store:
        example={'description':'选择指定记录','query':{'filters':[['id','eq',1]],'order':[['id','asc']],'limit':1}}
        source=SQLInformation('docs',store,{'rows':DatasetSpec('docs','id',('id','value'),
            field_descriptions={'value':'原始业务文本'},time_description='此数据集没有时间字段',query_examples=(example,))})
        scope=InteractionScope('a',Moment(1,'review'));schema=await source.metadata(scope,'/docs/rows')
        assert schema['field_metadata']['value']['description']=='原始业务文本'
        assert schema['time_description']=='此数据集没有时间字段'
        assert schema['query_examples']==[example]
        assert (await source.query(scope,'/docs/rows',Query(**example['query']))).total==0
        source.close()

@pytest.mark.asyncio
async def test_native_batch_keeps_unicode_original_offsets_and_final_unterminated_line():
    from society0.kernel.information_fs import search_reader
    lines=[(f'{i}:关键词🙂'+'原文'*((i%7)+1)+'\n').encode() for i in range(6000)]
    lines.append('末行关键词🙂'.encode());body=b''.join(lines);hits=[];offset=0;expected=[]
    for number,line in enumerate(lines,1):
        expected.append((number,offset,line));offset+=len(line)
    async def read(offset,size):return body[offset:offset+min(size,4093)]
    async def sink(line,offset,data):hits.append((line,offset,data))
    result=await search_reader(read,sink,'关键词',literal=True)
    assert result['matches']==len(lines) and hits==expected
    assert result['sink_batches']<len(lines)//10
