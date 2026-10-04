"""SQL 正文引用避免查询物化，并保原始范围、权限与分页。"""
from tests.primary.scripted_provider import TypedScriptProvider
import json
import tracemalloc
from dataclasses import asdict
import apsw
import pytest
from society0.kernel.storage import StageStore,StageReader
from society0.kernel.interaction import InteractionScope,Moment,Query,Unavailable
from society0.kernel.information_sql import SQLInformation,DatasetSpec,DocumentSpec


@pytest.mark.asyncio
@pytest.mark.parametrize('kind',['TEXT','BLOB'])
async def test_large_body_query_keeps_sqlite_memory_bounded_and_unicode_ranges(tmp_path,kind):
    original=('原文🙂'*2000000).encode()
    with StageStore.create(tmp_path/'run',(f'CREATE TABLE docs(id INTEGER PRIMARY KEY,owner TEXT NOT NULL,body {kind} NOT NULL)',),
        initialize=lambda w:w.execute('INSERT INTO docs VALUES(1,?,?)',('alice',original.decode() if kind=='TEXT' else original))) as store:
        def authorize(scope):return 'owner=?',(scope.actor,)
        provider=SQLInformation('world',StageReader(store.path),{
            'rows':DatasetSpec('docs','id',('id','body'),authorize=authorize,documents=(('body','body'),)),
            'body':DocumentSpec('docs','id','body',authorize=authorize)})
        scope=InteractionScope('alice',Moment(1,'read'))
        baseline=apsw.status(apsw.SQLITE_STATUS_MEMORY_USED,True)[0]
        tracemalloc.start()
        page=await provider.query(scope,'/world/rows',Query(limit=1,max_bytes=1024))
        _,python_peak=tracemalloc.get_traced_memory();tracemalloc.stop()
        assert python_peak<2*1024*1024
        _,peak=apsw.status(apsw.SQLITE_STATUS_MEMORY_USED,True)
        print(json.dumps({'body_type':kind,'body_bytes':len(original),'sqlite_extra_peak':peak-baseline,'python_heap_peak':python_peak,'page_bytes':len(json.dumps(asdict(page),ensure_ascii=False,separators=(',',':')).encode())}))
        assert peak-baseline<4*1024*1024
        reference=page.items[0]['body']
        assert reference['total_bytes']==len(original)
        assert len(json.dumps(asdict(page),ensure_ascii=False,separators=(',',':')).encode())<=1024
        raw=bytearray()
        for offset in range(0,105,7):
            part=await provider.read(scope,reference['path'],offset=offset,size=7);raw.extend(part.data)
        assert raw==original[:105]
        offset=0
        while offset<len(original):
            part=await provider.read(scope,reference['path'],offset=offset,size=65537)
            assert part.data==original[offset:offset+65537]
            offset+=len(part.data)
        assert part.next_offset is None
        assert (await provider.stat(scope,reference['path'])).total_bytes==len(original)
        with pytest.raises(Unavailable):await provider.read(InteractionScope('bob',scope.moment),reference['path'])


@pytest.mark.asyncio
async def test_query_byte_budget_keeps_exact_total_and_continuation(tmp_path):
    with StageStore.create(tmp_path/'run',('CREATE TABLE items(id INTEGER PRIMARY KEY,label TEXT NOT NULL)',),
        initialize=lambda w:w.executemany('INSERT INTO items VALUES(?,?)',((i,'轻字段'*30) for i in range(20)))) as store:
        provider=SQLInformation('world',store,{'items':DatasetSpec('items','id',('id','label'))})
        scope=InteractionScope('a',Moment(1,'read'));cursor=None;found=[]
        while True:
            page=await provider.query(scope,'/world/items',Query(limit=20,max_bytes=1024,cursor=cursor))
            assert page.total==20
            assert len(json.dumps(asdict(page),ensure_ascii=False,separators=(',',':')).encode())<=1024
            found.extend(item['id'] for item in page.items)
            cursor=page.next_cursor
            if cursor is None:break
        assert found==list(range(20))


def test_document_mapping_rejects_different_authorization(tmp_path):
    with StageStore.create(tmp_path/'run',('CREATE TABLE docs(id INTEGER PRIMARY KEY,owner TEXT,body TEXT)',)) as store:
        with pytest.raises(ValueError,match='authorization'):
            SQLInformation('world',store,{'rows':DatasetSpec('docs','id',('id','body'),documents=(('body','body'),)),
                'body':DocumentSpec('docs','id','body',authorize=lambda s:('owner=?',(s.actor,)))})


@pytest.mark.asyncio
async def test_sample_budget_is_explicit_and_related_updates_expire_page(tmp_path):
    with StageStore.create(tmp_path/'run',('CREATE TABLE items(id INTEGER PRIMARY KEY,label TEXT NOT NULL)',),
        initialize=lambda w:w.executemany('INSERT INTO items VALUES(?,?)',((i,'light'*40) for i in range(20)))) as store:
        provider=SQLInformation('world',store,{'items':DatasetSpec('items','id',('id','label'))})
        scope=InteractionScope('a',Moment(1,'read'))
        with pytest.raises(ValueError,match='sample exceeds'):
            await provider.query(scope,'/world/items',Query(limit=20,sample_seed=5,max_bytes=512))
        page=await provider.query(scope,'/world/items',Query(limit=1))
        store.transaction(lambda w:w.execute('UPDATE items SET label=? WHERE id=10',('changed',)))
        with pytest.raises(ValueError,match='revision'):
            await provider.query(scope,'/world/items',Query(limit=1,cursor=page.next_cursor))


@pytest.mark.asyncio
@pytest.mark.parametrize('change',['body','permission'])
async def test_document_reference_version_survives_unrelated_write_but_not_domain_change(tmp_path,change):
    from society0.kernel.interaction import Information
    schema=('CREATE TABLE docs(id INTEGER PRIMARY KEY,owner TEXT NOT NULL,body TEXT NOT NULL)',
            'CREATE TABLE diagnostics(id INTEGER PRIMARY KEY,n INTEGER)')
    with StageStore.create(tmp_path/'run',schema,initialize=lambda w:w.execute('INSERT INTO docs VALUES(1,?,?)',('a','完整🙂'*20))) as store:
        auth=lambda s:('owner=?',(s.actor,))
        provider=SQLInformation('world',store,{'rows':DatasetSpec('docs','id',('id','body'),authorize=auth,documents=(('body','body'),)),
            'body':DocumentSpec('docs','id','body',authorize=auth)})
        information=Information(lambda *a:True);information.mount('/world',provider)
        bound=information.bound(InteractionScope('a',Moment(1,'read')))
        ref=(await bound.query('/world/rows',Query())).items[0]['body']
        first=await bound.read(ref['path'],size=7,expected_revision=ref['expected_revision'])
        store.transaction(lambda w:w.execute('INSERT INTO diagnostics VALUES(1,1)'))
        assert (await bound.read(ref['path'],offset=7,size=7,expected_revision=first.revision)).data
        if change=='body':store.transaction(lambda w:w.execute('UPDATE docs SET body=? WHERE id=1',('改动后',)))
        else:store.transaction(lambda w:w.execute('UPDATE docs SET owner=? WHERE id=1',('b',)))
        with pytest.raises((ValueError,Unavailable)):
            await bound.read(ref['path'],offset=14,size=7,expected_revision=first.revision)


@pytest.mark.asyncio
async def test_actual_llm_query_then_utf8_reads_preserves_all_content(tmp_path):
    from types import SimpleNamespace
    from society0.kernel.threads import THREAD_SCHEMA,ThreadStore
    from society0.kernel.llm import LLMDriver
    from society0.kernel.runtime import Actor,Session
    from society0.kernel.interaction import Information,Actions
    original='甲🙂'*3
    with StageStore.create(tmp_path/'run',THREAD_SCHEMA+('CREATE TABLE docs(id INTEGER PRIMARY KEY,body TEXT NOT NULL)',),
        initialize=lambda w:w.execute('INSERT INTO docs VALUES(1,?)',(original,))) as store:
        threads=ThreadStore(store);seen=[];reference={};text=[]
        class Provider:
            async def request(self,tid,options):
                replies=[json.loads(m['content']) for m in threads.read_messages(tid) if m['role']=='tool']
                if not replies:name,args='data_query',{'path':'/world/rows','query':{'max_bytes':1024}}
                else:
                    value=replies[-1];assert 'error' not in value,value
                    if len(replies)==1:
                        reference.update(value['items'][0]['body']);offset=0
                    else:
                        text.append(value['data']);offset=value['next_offset']
                        if offset is None:return {'role':'assistant','content':'done','finish_reason':'stop'}
                    name,args='data_read',{'path':reference['path'],'offset':offset,'size':8,'encoding':'utf-8','expected_revision':reference['expected_revision']}
                seen.append(name)
                return {'role':'assistant','content':'','finish_reason':'tool_calls','tool_calls':[
                    {'id':str(len(seen)),'type':'function','function':{'name':name,'arguments':json.dumps(args)}}]}
        information=Information(lambda *a:True)
        information.mount('/world',SQLInformation('world',store,{'rows':DatasetSpec('docs','id',('id','body'),documents=(('body','body'),)),
            'body':DocumentSpec('docs','id','body')}))
        driver=LLMDriver(TypedScriptProvider(Provider(), threads),threads,input_builder=lambda s:[{'role':'system','content':'读取全部原始材料'}])
        scope=InteractionScope('a',Moment(1,'read'))
        current=Session(Actor('a',driver),scope,information.bound(scope),Actions(lambda *a:True).bound(scope),{},None,(),
                        SimpleNamespace(prepare_artifact=store.prepare_artifact),step=1)
        result=await driver.run(current)
        assert result.status=='completed'
        assert ''.join(text)==original and seen.count('data_query')==1


@pytest.mark.asyncio
async def test_shell_and_observer_forward_document_version(tmp_path):
    import base64
    from society0.kernel.interaction import Information,Actions
    from society0.kernel.shell import ShellSession
    from society0.kernel.observation import Observation
    with StageStore.create(tmp_path/'run',('CREATE TABLE docs(id INTEGER PRIMARY KEY,body TEXT NOT NULL)',),
        initialize=lambda w:w.execute('INSERT INTO docs VALUES(1,?)',('原文🙂'*20,))) as store:
        def factory(reader):
            information=Information(lambda *a:True)
            information.mount('/world',SQLInformation('world',reader,{'body':DocumentSpec('docs','id','body')}))
            return information
        scope=InteractionScope('a',Moment(1,'read'))
        information=factory(store)
        shell=ShellSession(scope,information,Actions(lambda *a:True),result_dir=tmp_path/'shell')
        observer=Observation(store.path,information_factory=factory)
        try:
            first=await observer.read_document(actor='a',moment=asdict(scope.moment),path='/world/body/1',size=7)
            assert base64.b64decode(first['data'])=='原文'.encode()+b'\xf0'
            options=json.dumps({'offset':0,'size':10,'expected_revision':first['revision']})
            answer=await shell.execute("data read /world/body/1 '"+options+"'")
            assert json.loads(answer.stdout)['data']=='原文🙂'
            store.transaction(lambda w:w.execute('UPDATE docs SET body=? WHERE id=1',('changed',)))
            with pytest.raises(ValueError,match='revision'):
                await observer.read_document(actor='a',moment=asdict(scope.moment),path='/world/body/1',size=7,expected_revision=first['revision'])
            answer=await shell.execute("data read /world/body/1 '"+options+"'")
            assert answer.exit_code!=0
        finally:await shell.aclose()


@pytest.mark.asyncio
async def test_document_query_carries_rowid_once_per_page(tmp_path,monkeypatch):
    from society0.kernel.storage import ReadView
    calls=[];original=ReadView.query
    def traced(self,sql,*args,**kwargs):
        if 'FROM "docs" WHERE "id"=?' in sql:calls.append(sql)
        return original(self,sql,*args,**kwargs)
    with StageStore.create(tmp_path/'run',('CREATE TABLE docs(id TEXT PRIMARY KEY NOT NULL,body TEXT NOT NULL,other BLOB NOT NULL)',),
        initialize=lambda w:w.executemany('INSERT INTO docs VALUES(?,?,?)',((str(i),'原文',b'other') for i in range(20)))) as store:
        provider=SQLInformation('world',store,{'rows':DatasetSpec('docs','id',('id','body','other'),documents=(('body','body'),('other','other'))),
            'body':DocumentSpec('docs','id','body'),'other':DocumentSpec('docs','id','other')})
        monkeypatch.setattr(ReadView,'query',traced)
        page=await provider.query(InteractionScope('a',Moment(1,'read')),'/world/rows',Query(limit=20))
        assert page.total==20 and len(page.items)==20
        assert not calls, f'repeated rowid lookup: {len(calls)}'
