"""SQL 权威记忆、提取作业与可重建向量投影。"""
import math
import pytest
from society0.kernel.storage import StageStore
from society0.kernel.threads import ThreadStore, THREAD_SCHEMA
from society0.kernel.memory import Memory, MemoryPolicy, MEMORY_SCHEMA


class Collection:
    def __init__(self,metadata):
        self.metadata = metadata
        self.rows = {}
        self.fail = False
    def upsert(self,ids,embeddings,metadatas):
        if self.fail:
            self.fail = False
            raise OSError('index unavailable')
        for key,vector,meta in zip(ids,embeddings,metadatas):self.rows[key]=(vector,meta)
    def delete(self,ids):
        for key in ids:self.rows.pop(key,None)
    def modify(self,metadata):self.metadata=metadata
    def query(self,query_embeddings,n_results,where,include):
        conditions = where.get('$and',[where])
        def matches(meta):
            for condition in conditions:
                field,operation = next(iter(condition.items()))
                operator,value = next(iter(operation.items()))
                if operator=='$eq' and meta[field]!=value:return False
                if operator=='$lte' and meta[field]>value:return False
            return True
        rows = [(sum((a-b)**2 for a,b in zip(query_embeddings[0],vector)),key) for key,(vector,meta) in self.rows.items() if matches(meta)]
        rows.sort()
        return {'ids':[[key for _,key in rows[:n_results]]], 'distances':[[distance for distance,_ in rows[:n_results]]]}


class Client:
    def __init__(self):self.collections={}
    def get_or_create_collection(self,name,metadata=None,embedding_function=None):
        return self.collections.setdefault(name,Collection(metadata))
    def delete_collection(self,name):self.collections.pop(name,None)


class Embed:
    def __init__(self):self.calls=[]
    async def __call__(self,texts,*,metadata):
        self.calls.append(list(texts))
        return [[float(len(text)%7),1.0] for text in texts]


def setup(tmp_path,**options):
    store = StageStore.create(tmp_path/'run',[*THREAD_SCHEMA,*MEMORY_SCHEMA])
    threads = ThreadStore(store)
    thread = threads.open('a',0,'decision')
    embed = Embed()
    client = Client()
    memory = Memory(store,threads,embed=embed,client=client,**options)
    return store,threads,thread,memory,embed,client


@pytest.mark.asyncio
async def test_pending_job_full_checkpoint_resumes_without_extraction(tmp_path):
    store,threads,thread,memory,embed,client = setup(tmp_path)
    with store:
        entries=[{'content':'first fact','importance':4.0,'type':'episodic','metadata':{'nested':[1,True]}}]
        job=memory.prepare_job('a',thread,'job1',timestamp=2,entries=entries)
        assert memory.prepare_job('a',thread,'job1',timestamp=2,entries=entries)==job
        store.complete(1)
        with StageStore.restore(store.path,tmp_path/'resumed') as restored:
            resumed=Memory(restored,ThreadStore(restored),embed=embed,client=Client())
            ids=await resumed.finish_job(job)
            assert len(ids)==1 and embed.calls==[['first fact']]
            assert resumed.get(ids[0],actor='a')['content']=='first fact'
            assert resumed.get(ids[0],actor='a')['metadata']=={'nested':[1,True]}
            assert await resumed.finish_job(job)==ids
            assert len(embed.calls)==1
            restored.complete(2)
            no_embed=Embed()
            with StageStore.restore(restored.path,tmp_path/'again') as again:
                final=Memory(again,ThreadStore(again),embed=no_embed,client=Client())
                await final.sync_index()
                assert no_embed.calls==[]
                assert final.get(ids[0],actor='a')['embedding']==[3.0,1.0]


@pytest.mark.asyncio
async def test_index_failure_retries_saved_vectors_and_receipt(tmp_path):
    store,threads,thread,memory,embed,client=setup(tmp_path)
    with store:
        job=memory.prepare_job('a',thread,'job',timestamp=0,entries=[{'content':'fact','importance':2.0}])
        await memory.sync_index()
        next(iter(client.collections.values())).fail=True
        with pytest.raises(OSError):await memory.finish_job(job)
        assert memory.job(job)['state']=='written'
        ids=await memory.finish_job(job)
        assert len(embed.calls)==1 and memory.job(job)['state']=='complete'
        assert memory.get(ids[0],actor='a')['content']=='fact'
        with pytest.raises(PermissionError):memory.get(ids[0],actor='b')


@pytest.mark.asyncio
async def test_recall_matches_existing_distance_decay_dedup_semantics(tmp_path):
    store,threads,thread,memory,embed,client=setup(tmp_path,decay_rate=0.1)
    with store:
        entries=[{'content':'fact','importance':5.0,'type':'episodic'},
                 {'content':'fact','importance':1.0,'type':'episodic'},
                 {'content':'other','importance':2.0,'type':'semantic'}]
        job=memory.prepare_job('a',thread,'job',timestamp=0,entries=entries)
        await memory.finish_job(job)
        result=await memory.recall('a','fact',top_k=10,current_step=2)
        assert [row['content'] for row in result]==['fact','other']
        assert result[0]['score']==pytest.approx(1+0.5*math.exp(-0.2))
        assert result[1]['score']==pytest.approx(0+0.2*math.exp(-0.2))
        assert await memory.recall('b','fact',top_k=10,current_step=2)==[]


@pytest.mark.asyncio
async def test_embedding_response_must_preserve_every_input(tmp_path):
    store,threads,thread,memory,embed,client=setup(tmp_path)
    with store:
        job=memory.prepare_job('a',thread,'job',timestamp=0,entries=[{'content':'a','importance':1},{'content':'b','importance':2}])
        async def bad(texts,*,metadata):return [[1.0,2.0]]
        memory.embed=bad
        with pytest.raises(ValueError):await memory.finish_job(job)
        assert memory.job(job)['state']=='prepared'
        memory.embed=embed
        assert len(await memory.finish_job(job))==2


@pytest.mark.asyncio
@pytest.mark.parametrize('auto_recall',[False,True])
@pytest.mark.parametrize('auto_write',[False,True])
@pytest.mark.parametrize('active_tools',[False,True])
async def test_three_memory_switches_are_independent(tmp_path,auto_recall,auto_write,active_tools):
    from types import SimpleNamespace
    from society0.kernel.interaction import Moment
    calls=[]
    async def extract(actor,thread_id,messages,*,metadata):
        calls.append(messages)
        return [{'content':'new experience','importance':3}]
    store,threads,thread,memory,embed,client=setup(tmp_path,extract=extract,
        policy=MemoryPolicy(auto_recall,auto_write,active_tools),recall_query=lambda session:'known')
    with store:
        job=memory.prepare_job('a',thread,'initial',timestamp=0,entries=[{'content':'known','importance':3}])
        await memory.finish_job(job)
        embed.calls.clear()
        session=SimpleNamespace(step=1,actor=SimpleNamespace(id='a'),moment=Moment(1,'decision'))
        messages=await memory.before_activation(session,thread)
        assert any('known' in message['content'] for message in messages)==auto_recall
        assert len(embed.calls)==int(auto_recall)
        from society0.kernel.interaction import Actions,InteractionScope,Ref
        actions=Actions(lambda *args:True)
        for action in memory.actions():actions.register(action)
        session.scope=InteractionScope('a',session.moment)
        async with memory.activation(session,thread):
            available=await actions.find(session.scope,Ref('memory','actor','a'))
            assert bool(available.items)==active_tools
        threads.append_message(thread,{'role':'user','content':'new observation'})
        result=SimpleNamespace(status='completed',value={'memory_input_through':threads.describe(thread)['last_seq']})
        await memory.after_activation(session,thread,result)
        await memory.after_activation(session,thread,result)
        assert len(calls)==int(auto_write)
        assert len(embed.calls)==int(auto_recall)+int(auto_write)


@pytest.mark.asyncio
async def test_two_activations_have_distinct_input_jobs_but_retry_reuses_receipt(tmp_path):
    from types import SimpleNamespace
    from society0.kernel.interaction import Moment
    calls=[]
    async def extract(actor,thread_id,messages,*,metadata):
        calls.append((messages,metadata['through']))
        return [{'content':messages[-1]['content'],'importance':2}]
    store,threads,thread,memory,embed,client=setup(tmp_path,extract=extract,policy=MemoryPolicy(False,True,False))
    with store:
        session=SimpleNamespace(step=1,actor=SimpleNamespace(id='a'),moment=Moment(0,'decision'))
        for value in ('first fact','second fact'):
            threads.append_message(thread,{'role':'user','content':value})
            result=SimpleNamespace(status='completed',value={'memory_input_through':threads.describe(thread)['last_seq']})
            await memory.after_activation(session,thread,result)
            await memory.after_activation(session,thread,result)
        assert len(calls)==2 and len(embed.calls)==2
        assert calls[0][0][-1]['content']=='first fact'
        assert calls[1][0][-1]['content']=='second fact'
        incomplete=SimpleNamespace(status='incomplete',value={})
        await memory.after_activation(session,thread,incomplete)
        assert len(calls)==2


@pytest.mark.asyncio
async def test_update_delete_keep_job_receipt_and_restore_old_complete_state(tmp_path):
    store,threads,thread,memory,embed,client=setup(tmp_path)
    with store:
        entry={'content':'old fact'}
        job=memory.prepare_job('a',thread,'original',timestamp=0,entries=[entry])
        identifier=(await memory.finish_job(job))[0]
        assert memory.get(identifier,actor='a')['importance']==3.0
        store.complete(1)
        await memory.update(identifier,actor='a',content='new fact',timestamp=2,importance=4,metadata={'x':1})
        assert memory.get(identifier,actor='a')['content']=='new fact'
        assert memory.prepare_job('a',thread,'original',timestamp=0,entries=[entry])==job
        assert await memory.finish_job(job)==[identifier]
        assert len(embed.calls)==2
        await memory.delete(identifier,actor='a')
        assert await memory.recall('a','fact')==[]
        assert await memory.finish_job(job)==[identifier]
        with pytest.raises(KeyError):memory.get(identifier,actor='a')
        with StageStore.restore(store.path,tmp_path/'previous',step=1) as restored:
            old=Memory(restored,ThreadStore(restored),embed=embed,client=Client())
            assert old.get(identifier,actor='a')['content']=='old fact'
            await old.sync_index()
            assert identifier in old._collection.rows
        store.complete(2)
        with StageStore.restore(store.path,tmp_path/'deleted') as restored:
            final=Memory(restored,ThreadStore(restored),embed=embed,client=Client())
            await final.sync_index()
            assert identifier not in final._collection.rows
            assert await final.finish_job(job)==[identifier]


@pytest.mark.asyncio
async def test_update_embedding_failure_and_concurrent_delete_do_not_overwrite(tmp_path):
    store,threads,thread,memory,embed,client=setup(tmp_path)
    with store:
        job=memory.prepare_job('a',thread,'job',timestamp=0,entries=[{'content':'before'}])
        identifier=(await memory.finish_job(job))[0]
        async def fail(texts,*,metadata):raise OSError('embedding failed')
        memory.embed=fail
        with pytest.raises(OSError):await memory.update(identifier,actor='a',content='after',timestamp=1)
        assert memory.get(identifier,actor='a')['content']=='before'
        async def concurrent(texts,*,metadata):
            await memory.delete(identifier,actor='a')
            return [[1.0,2.0]]
        memory.embed=concurrent
        with pytest.raises(RuntimeError,match='changed'):
            await memory.update(identifier,actor='a',content='after',timestamp=1)
        with pytest.raises(KeyError):memory.get(identifier,actor='a')


@pytest.mark.asyncio
async def test_real_chroma_sql_vectors_rebuild_preserves_candidates_and_context(tmp_path):
    import chromadb
    store,threads,thread,memory,embed,_=setup(tmp_path,decay_rate=0.1)
    memory.client=chromadb.EphemeralClient()
    vectors={'near':[0.123456789012345,0.2],'far':[1.25,0.2],'query':[0.1,0.2]}
    calls=[]
    async def precise(texts,*,metadata):
        calls.append(list(texts))
        return [vectors[text] for text in texts]
    memory.embed=precise
    with store:
        job=memory.prepare_job('a',thread,'real',timestamp=0,entries=[{'content':'near','importance':4},{'content':'far','importance':2}])
        ids=await memory.finish_job(job)
        first=await memory.recall('a','query',top_k=2,current_step=2)
        assert [row['content'] for row in first]==['near','far']
        assert memory.get(ids[0],actor='a')['embedding']==vectors['near']
        projected=memory._collection.get(ids=ids,include=['embeddings'])
        assert projected['embeddings'].dtype.name=='float64'
        # Chroma 以 float32 保存向量，Python 读取数组虽是 float64，精度已经量化。
        import struct
        expected=struct.unpack('<f',struct.pack('<f',vectors['near'][0]))[0]
        assert projected['embeddings'][projected['ids'].index(ids[0])][0]==expected
        store.complete(1)
        with StageStore.restore(store.path,tmp_path/'real-restored') as restored:
            restored_memory=Memory(restored,ThreadStore(restored),embed=precise,client=memory.client,decay_rate=0.1)
            before=len(calls)
            await restored_memory.sync_index()
            assert len(calls)==before
            again=await restored_memory.recall('a','query',top_k=2,current_step=2)
            assert [(r['id'],r['content'],r['score']) for r in again]==[(r['id'],r['content'],r['score']) for r in first]
            restored_memory.client.delete_collection(name=restored_memory._collection.name)
        memory.client.delete_collection(name=memory._collection.name)


@pytest.mark.asyncio
async def test_historical_recall_uses_visible_versions_and_original_vectors(tmp_path):
    store,threads,thread,memory,embed,client=setup(tmp_path)
    with store:
        job=memory.prepare_job('a',thread,'history',timestamp=-10,visible_step=0,entries=[{'content':'old'}])
        identifier=(await memory.finish_job(job))[0]
        await memory.update(identifier,actor='a',content='newer',timestamp=-5,visible_step=2)
        await memory.update(identifier,actor='a',content='final',timestamp=-4,visible_step=2)
        await memory.delete(identifier,actor='a',visible_step=3)
        before=await memory.recall('a','old',current_step=1)
        during=await memory.recall('a','final',current_step=2)
        assert [r['content'] for r in before]==['old']
        assert before[0]['embedding']==[3.0,1.0] and before[0]['timestamp']==-10
        assert [r['content'] for r in during]==['final']
        assert await memory.recall('a','final',current_step=3)==[]
        assert len(client.collections)==1
        store.complete(1)
        with StageStore.restore(store.path,tmp_path/'history-restored') as restored:
            final=Memory(restored,ThreadStore(restored),embed=embed,client=Client())
            assert [r['content'] for r in await final.recall('a','old',current_step=1)]==['old']


@pytest.mark.asyncio
@pytest.mark.parametrize('mode',['success','empty','retry','length'])
async def test_thread_extractor_preserves_history_and_protocol_boundary(tmp_path,mode):
    from society0.kernel.memory import ThreadMemoryExtractor
    store,threads,thread,memory,embed,client=setup(tmp_path)
    with store:
        original=[{'role':'system','content':'persona'},{'role':'user','content':'full experience'}]
        for message in original:threads.append_message(thread,message)
        class Provider:
            def __init__(self):self.calls=[]
            async def request(self,tid,options):
                self.calls.append((threads.read_messages(tid),options))
                if mode=='length':return {'role':'assistant','content':'cut','finish_reason':'length'}
                if mode=='retry' and len(self.calls)==1:return {'role':'assistant','content':'invalid','finish_reason':'stop'}
                import json
                return {'role':'assistant','content':None,'finish_reason':'tool_calls','tool_calls':[{'id':'extract-call','type':'function','function':{'name':'extract_memories','arguments':json.dumps({'memories':[] if mode=='empty' else [{'content':'remember','importance':4}]})}}]}
        provider=Provider()
        extract=ThreadMemoryExtractor(threads,provider)
        if mode=='length':
            with pytest.raises(RuntimeError,match='length'):
                await extract('a',thread,original,metadata={})
        else:
            result=await extract('a',thread,original,metadata={})
            assert result==([] if mode=='empty' else [{'content':'remember','importance':4.0}])
            assert len(provider.calls)==(2 if mode=='retry' else 1)
        assert provider.calls[0][0][:2]==original
        assert provider.calls[0][1]['tool_choice']['function']['name']=='extract_memories'
        assert threads.read_messages(thread)[:2]==original


@pytest.mark.asyncio
async def test_active_memory_actions_include_update_and_delete(tmp_path):
    from society0.kernel.interaction import InteractionScope,Moment,Ref
    store,threads,thread,memory,embed,client=setup(tmp_path,policy=MemoryPolicy(False,False,True))
    with store:
        actions={action.name:action for action in memory.actions()}
        assert set(actions)=={'memory.remember','memory.recall','memory.update','memory.delete'}
        scope=InteractionScope('a',Moment(1,'decision'))
        target=Ref('memory','actor','a')
        job=memory.prepare_job('a',thread,'a',timestamp=0,entries=[{'content':'old'}])
        identifier=(await memory.finish_job(job))[0]
        from types import SimpleNamespace
        current=SimpleNamespace(actor=SimpleNamespace(id='a'),scope=scope,step=1)
        async with memory.activation(current,thread):
            await actions['memory.update'].handler(scope,target,{'memory_id':identifier,'content':'updated'})
            assert memory.get(identifier,actor='a')['content']=='updated'
            await actions['memory.delete'].handler(scope,target,{'memory_id':identifier})
        assert await memory.recall('a','updated')==[]


@pytest.mark.asyncio
async def test_current_recall_sql_work_does_not_grow_with_history(tmp_path):
    import apsw
    import struct
    counts=[]
    for size in (10,10000):
        store,threads,thread,memory,embed,client=setup(tmp_path/str(size))
        with store:
            job=memory.prepare_job('a',thread,'current',timestamp=2,entries=[{'content':'current'}])
            identifier=(await memory.finish_job(job))[0]
            store.transaction(lambda writer:writer.executemany('INSERT INTO memory_history VALUES(?,?,?,?,?,?,?,?,?,?)',
                ((str(index),identifier,'a','episodic',0,3.0,0,1,2,struct.pack('<2d',1.,1.)) for index in range(size))))
            count=[0]
            def progress():count[0]+=1;return False
            def hook(connection):connection.set_progress_handler(progress,1)
            apsw.connection_hooks.append(hook)
            try:
                assert [r['content'] for r in await memory.recall('a','current',current_step=2)]==['current']
            finally:apsw.connection_hooks.remove(hook)
            counts.append(count[0])
    assert counts[1]<=counts[0]*1.15


@pytest.mark.asyncio
async def test_empty_extraction_history_does_not_require_vector_dimension(tmp_path):
    store,threads,thread,memory,embed,client=setup(tmp_path)
    with store:
        job=memory.prepare_job('a',thread,'empty',timestamp=5,entries=[])
        await memory.finish_job(job)
        assert await memory.recall('a','nothing',current_step=1)==[]
        assert embed.calls==[] and client.collections=={}


@pytest.mark.asyncio
async def test_interview_thread_default_policy_does_not_extract_experience(tmp_path):
    from types import SimpleNamespace
    from society0.kernel.interaction import Moment
    async def forbidden(*args,**kwargs):raise AssertionError('interview extraction')
    store,threads,_,memory,embed,client=setup(tmp_path,extract=forbidden)
    with store:
        interview=threads.open('a',0,'interview')
        threads.append_message(interview,{'role':'system','content':'interview'})
        session=SimpleNamespace(step=1,actor=SimpleNamespace(id='a'),moment=Moment(0,'interview'))
        result=SimpleNamespace(status='completed',value={'memory_input_through':threads.describe(interview)['last_seq']})
        await memory.after_activation(session,interview,result)
        assert store.read(lambda view:view.query('SELECT count(*) FROM memory_jobs'))==[(0,)]


@pytest.mark.asyncio
async def test_automatic_recall_top_k_controls_candidates_and_complete_context(tmp_path):
    import json
    from types import SimpleNamespace
    from society0.kernel.interaction import Moment
    store,threads,thread,memory,embed,client=setup(tmp_path,recall_top_k=2,recall_query=lambda session:'x',policy=MemoryPolicy(True,False,False))
    with store:
        job=memory.prepare_job('a',thread,'facts',timestamp=0,entries=[{'content':value} for value in ('x','yy','zzz','wwww')])
        await memory.finish_job(job)
        original=memory._collection.query
        requested=[]
        def query(**kwargs):requested.append(kwargs['n_results']);return original(**kwargs)
        memory._collection.query=query
        messages=await memory.before_activation(SimpleNamespace(step=1,actor=SimpleNamespace(id='a'),moment=Moment(1,'decision')),thread)
        assert requested==[4]
        assert json.loads(messages[0]['content'])=={'recalled_memories':['x','yy']}


@pytest.mark.asyncio
async def test_registered_memory_recall_action_preserves_actor_original_and_thread_scope(tmp_path):
    from types import SimpleNamespace
    from society0.kernel.interaction import Actions,InteractionScope,Moment,Ref
    store,threads,_,memory,embed,client=setup(tmp_path,policy=MemoryPolicy(False,False,True))
    with store:
        moment=Moment(1,'decision');thread=threads.open('a',moment,'decision')
        original='采购三吨原料，每吨2000元。完整原文🙂'*1000
        await memory.finish_job(memory.prepare_job('a',thread,'a',timestamp=0,entries=[{'content':original}]))
        await memory.finish_job(memory.prepare_job('b',threads.open('b',moment,'decision'),'b',timestamp=0,entries=[{'content':'其他主体私有原文'}]))
        recalls=[];native_recall=memory.recall
        async def scoped_recall(*args,**kwargs):recalls.append((args,dict(kwargs)));return await native_recall(*args,**kwargs)
        memory.recall=scoped_recall
        calls=[];native_embed=memory.embed
        async def recorded(texts,*,metadata):calls.append(dict(metadata));return await native_embed(texts,metadata=metadata)
        memory.embed=recorded
        actions=Actions(lambda *args:True)
        for action in memory.actions():actions.register(action)
        scope=InteractionScope('a',moment)
        current=SimpleNamespace(actor=SimpleNamespace(id='a'),scope=scope,step=1)
        async with memory.activation(current,thread):
            result=await actions.bound(scope).invoke('memory.recall',Ref('memory','actor','a'),{'query':'采购原料','top_k':10})
            assert result.status=='completed'
            assert [item['content'] for item in result.value['memories']]==[original]
            assert memory.get(result.value['memories'][0]['id'],actor='a')['content']==original
            denied=await actions.bound(scope).invoke('memory.recall',Ref('memory','actor','b'),{'query':'其他主体'})
            assert denied.status=='rejected' and denied.value=={'reason':'unavailable'}
        assert calls==[{'actor':'a','thread_id':thread,'purpose':'memory_recall'}]
        assert recalls==[(('a','采购原料'),{'top_k':10,'current_step':1,'thread_id':thread})]
        await memory.close()
