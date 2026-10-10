"""真实range、稳定原文身份及注册元数据消费者。"""
import pytest
from society0.kernel.interaction import Information,InteractionScope,Moment
from society0.kernel.information_sql import SQLInformation,DatasetSpec,DocumentSpec
from society0.kernel.information_fs import InformationFiles
from society0.kernel.storage import StageStore

@pytest.mark.asyncio
async def test_original_is_file_bashkit_whole_read_and_document_range(tmp_path):
    body=b'x'*65533+'关键词'.encode()+b'\n'
    with StageStore.create(tmp_path/'run',('CREATE TABLE docs(id INTEGER PRIMARY KEY,body BLOB)',),initialize=lambda w:w.execute('INSERT INTO docs VALUES(1,?)',(body,))) as store:
        source=SQLInformation('docs',store,{'text':DocumentSpec('docs','id','body')})
        info=Information(lambda *a:True);info.mount('/docs',source)
        scope=InteractionScope('a',Moment(1,'read'));bound=info.bound(scope);fs=InformationFiles(bound,scope,max_file_bytes=1024)
        assert (await fs.callback('stat','/docs/text/1'))[:2]==('file',len(body))
        assert ('1','file') in [(row[0],row[1]) for row in await fs.callback('list','/docs/text')]
        assert await fs.callback('read','/docs/text/1')==body
        chunk=await bound.read('/docs/text/1',offset=65533,size=9)
        assert chunk.data=='关键词'.encode() and chunk.total_bytes==len(body)
        await fs.close()

@pytest.mark.asyncio
async def test_dataset_original_spool_once_and_registration_metadata(tmp_path):
    with StageStore.create(tmp_path/'run',('CREATE TABLE docs(id INTEGER PRIMARY KEY,body TEXT,day INTEGER NOT NULL)',),initialize=lambda w:w.execute('INSERT INTO docs VALUES(1,?,3)',('甲'*30000,))) as store:
        source=SQLInformation('docs',store,{'rows':DatasetSpec('docs','id',('id','body','day'),order_fields=('day',))})
        info=Information(lambda *a:True);info.mount('/docs',source,owned=True);scope=InteractionScope('a',Moment(1,'read'));bound=info.bound(scope)
        metadata=await bound.metadata('/docs/rows')
        assert metadata['fields']==['id','body','day'] and metadata['order_fields']==['day','id']
        first=await bound.read('/docs/rows/1',size=1024);stream=source._record_source[1]
        second=await bound.read('/docs/rows/1',offset=1024,size=1024,expected_revision=first.revision)
        assert source._record_source[1] is stream and len(second.data)==1024
        before=await bound.search_revision('/docs/rows')
        store.transaction(lambda w:w.execute('UPDATE docs SET day=4 WHERE id=1'))
        assert before!=await bound.search_revision('/docs/rows')
        with pytest.raises(ValueError):await bound.read('/docs/rows/1',offset=1024,expected_revision=first.revision)
        await info.close();assert source._record_source is None and stream.closed

@pytest.mark.asyncio
async def test_workspace_range_reads_only_requested_original(tmp_path):
    from society0.kernel.actors import ActorRecord,actor_plugin
    from society0.kernel.composition import compose
    from society0.kernel.workspace import workspace_plugin
    plugins=[actor_plugin({'rule':lambda r:None},records=[ActorRecord('a','rule')]),workspace_plugin()]
    async with compose(tmp_path/'run',plugins) as host:
        lease=host.service('workspace','workspace').open(InteractionScope('a',Moment(1,'read')))
        lease.save(b'',{'removed':[],'entries':[{'path':'/note','kind':'file','mode':420,'modified_ns':0,'created_ns':0,'content':b'x'*100000}]})
        store=host.service('storage','store');original=store.read_artifact;calls=[]
        def read(ref,**args):calls.append(args);return original(ref,**args)
        store.read_artifact=read
        chunk=await lease.read_range('/note',offset=50,size=64)
        assert chunk.data==b'x'*64 and chunk.total_bytes==100000 and calls==[{'offset':50,'size':64}]
        page=await lease.list_files('/',limit=1)
        assert page.total==1 and page.items[0]['path']=='/note'
        await lease.close()

@pytest.mark.asyncio
async def test_workspace_directory_counts_follow_normative_save(tmp_path):
    from society0.kernel.actors import ActorRecord,actor_plugin
    from society0.kernel.composition import compose
    from society0.kernel.workspace import workspace_plugin
    plugins=[actor_plugin({'rule':lambda r:None},records=[ActorRecord('a','rule')]) ,workspace_plugin()]
    async with compose(tmp_path/'run',plugins) as host:
        lease=host.service('workspace','workspace').open(InteractionScope('a',Moment(1,'read')))
        def entry(path):return {'path':path,'kind':'file','mode':420,'modified_ns':0,'created_ns':0,'content':b'x'}
        lease.save(b'',{'removed':[],'entries':[entry('/d/a'),entry('/d/b'),entry('/other')]})
        store=host.service('storage','store')
        assert store.read(lambda v:v.query('SELECT parent,total FROM workspace_counts WHERE actor=? ORDER BY parent',('a',)))==[('/',1),('/d',2)]
        lease.save(b'',{'removed':['/d/a'],'entries':[entry('/d/b'),entry('/d/c')]})
        assert (await lease.list_files('/d',limit=1)).total==2
        lease.save(b'',{'removed':['/d'],'entries':[]})
        assert (await lease.list_files('/d')).total==0
        assert (await lease.list_files('/')).total==1
        await lease.close()
