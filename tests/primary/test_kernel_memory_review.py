from tests.primary.scripted_provider import TypedScriptProvider
"""记忆版本与异步索引边界的非作者验收。"""
import pytest
from tests.primary.test_kernel_memory import setup


@pytest.mark.asyncio
@pytest.mark.parametrize('mode',['length','invalid_twice'])
async def test_review_failed_extraction_keeps_full_thread_without_success_job(tmp_path,mode):
    from society0.kernel.memory import ThreadMemoryExtractor
    store,threads,tid,memory,embed,client=setup(tmp_path)
    with store:
        original=[{'role':'system','content':'完整主体背景'},
                  {'role':'user','content':'原始经历🙂'*18000}]
        for message in original:threads.append_message(tid,message)
        through=threads.describe(tid)['last_seq']
        calls=[]
        class Provider:
            async def request(self,thread_id,options):
                calls.append(threads.read_messages(thread_id))
                return {'role':'assistant','content':'未完成原文',
                        'finish_reason':'length' if mode=='length' else 'stop'}
        memory.extract=ThreadMemoryExtractor(threads,TypedScriptProvider(Provider(),threads))
        with pytest.raises(RuntimeError):
            await memory.extract_job('a',tid,through=through,timestamp=1)
        assert len(calls)==(1 if mode=='length' else 2)
        assert all(messages[:2]==original for messages in calls)
        assert threads.read_messages(tid)[:2]==original
        assert embed.calls==[]
        assert store.read(lambda view:view.query('SELECT COUNT(*) FROM memory_jobs'))==[(0,)]
        store.complete(1)
        from society0.kernel.storage import StageStore
        from society0.kernel.threads import ThreadStore
        with StageStore.restore(store.path,tmp_path/'restored') as restored:
            recovered=ThreadStore(restored).read_messages(tid)
            assert recovered==threads.read_messages(tid)


@pytest.mark.asyncio
async def test_review_recall_at_fixed_step_keeps_old_version_if_update_occurs_during_embedding(tmp_path):
    store,threads,tid,memory,embed,client=setup(tmp_path)
    with store:
        job=memory.prepare_job('a',tid,'original',timestamp=1,visible_step=1,entries=[{'content':'old original'}])
        identifier=(await memory.finish_job(job))[0]
        changed=[]
        async def interleave(texts,*,metadata):
            if metadata['purpose']=='memory_recall' and not changed:
                changed.append(True)
                await memory.update(identifier,actor='a',content='future replacement',timestamp=2,visible_step=2)
            return await embed(texts,metadata=metadata)
        memory.embed=interleave
        result=await memory.recall('a','query',current_step=1,top_k=10)
        assert [item['content'] for item in result]==['old original']
        assert memory.get(identifier,actor='a')['content']=='future replacement'


@pytest.mark.asyncio
async def test_review_historical_candidate_never_uses_future_body_after_embedding_wait(tmp_path):
    store,threads,tid,memory,embed,client=setup(tmp_path)
    with store:
        old=memory.prepare_job('a',tid,'old',timestamp=0,visible_step=0,entries=[{'content':'old current'}])
        identifier=(await memory.finish_job(old))[0]
        later=memory.prepare_job('a',tid,'later',timestamp=3,visible_step=3,entries=[{'content':'other future'}])
        await memory.finish_job(later)
        changed=[]
        async def interleave(texts,*,metadata):
            if metadata['purpose']=='memory_recall' and not changed:
                changed.append(True)
                await memory.update(identifier,actor='a',content='changed future',timestamp=4,visible_step=4)
            return await embed(texts,metadata=metadata)
        memory.embed=interleave
        result=await memory.recall('a','query',current_step=1,top_k=10)
        assert [item['content'] for item in result]==['old current']
        assert not [name for name in client.collections if name.startswith('society-history-')]


@pytest.mark.asyncio
async def test_review_index_watermark_failure_after_upsert_restores_without_reembedding(tmp_path):
    from society0.kernel.storage import StageStore
    from society0.kernel.threads import ThreadStore
    from society0.kernel.memory import Memory
    from tests.primary.test_kernel_memory import Client, Embed
    store,threads,tid,memory,embed,client=setup(tmp_path)
    with store:
        job=memory.prepare_job('a',tid,'job',timestamp=1,entries=[{'content':'complete original','metadata':{'x':[1,True,None]}}])
        await memory.sync_index()
        collection=memory._collection
        original=collection.modify
        failures=[]
        def fail(metadata):
            if not failures:
                failures.append(True)
                raise OSError('watermark unavailable after upsert')
            return original(metadata)
        collection.modify=fail
        with pytest.raises(OSError): await memory.finish_job(job)
        assert collection.rows and memory.job(job)['state']=='written'
        vectors=memory.get(memory.job(job)['memory_ids'][0],actor='a')['embedding']
        store.complete(1)
        with StageStore.restore(store.path,tmp_path/'restore') as restored:
            future_embed=Embed()
            recovered=Memory(restored,ThreadStore(restored),embed=future_embed,client=Client())
            identifiers=await recovered.finish_job(job)
            assert future_embed.calls==[]
            record=recovered.get(identifiers[0],actor='a')
            assert record['embedding']==vectors and record['content']=='complete original'
            assert record['metadata']=={'x':[1,True,None]}
            assert recovered.job(job)['state']=='complete'


@pytest.mark.asyncio
async def test_review_close_collects_update_wait_before_shared_resources_can_close(tmp_path):
    import asyncio
    store,threads,tid,memory,embed,client=setup(tmp_path)
    with store:
        identifier=(await memory.seed('a','initial',timestamp=0,entries=[{'content':'before close'}]))[0]
        started=asyncio.Event(); release=asyncio.Event(); settled=[]
        async def pending(texts,*,metadata):
            started.set()
            try:await release.wait()
            finally:settled.append(True)
            return await embed(texts,metadata=metadata)
        memory.embed=pending
        update=asyncio.create_task(memory.update(identifier,actor='a',content='after close',timestamp=1))
        await started.wait()
        try:
            await memory.close()
            assert update.done(), 'close returned with an active embedding update'
            assert settled==[True]
            assert memory.get(identifier,actor='a')['content']=='before close'
        finally:
            release.set()
            await asyncio.gather(update,return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize('operation',['update','delete'])
@pytest.mark.parametrize('case',['missing','other_actor'])
async def test_review_memory_action_unavailable_record_is_business_rejection(tmp_path,operation,case):
    from society0.kernel.interaction import Actions, InteractionScope, Moment, Ref
    from society0.kernel.memory import MemoryPolicy
    store,threads,tid,memory,embed,client=setup(tmp_path,policy=MemoryPolicy(False,False,True))
    with store:
        identifier='missing'
        if case=='other_actor':
            identifier=(await memory.seed('b','b-seed',timestamp=0,entries=[{'content':'private b'}]))[0]
        actions=Actions(lambda *args:True)
        for action in memory.actions():actions.register(action)
        arguments={'memory_id':identifier}
        if operation=='update':arguments['content']='changed'
        before=len(embed.calls)
        from tests.primary.test_kernel_memory_activation import session
        current=session('a',actions=actions)
        async with memory.activation(current,tid):
            result=await current.actions.invoke('memory.'+operation,Ref('memory','actor','a'),arguments)
        assert result.status=='rejected'
        assert len(embed.calls)==before
        if case=='other_actor':assert memory.get(identifier,actor='b')['content']=='private b'


@pytest.mark.asyncio
async def test_review_memory_action_provider_failure_still_propagates(tmp_path):
    from society0.kernel.interaction import Actions, InteractionScope, Moment, Ref
    from society0.kernel.memory import MemoryPolicy
    store,threads,tid,memory,embed,client=setup(tmp_path,policy=MemoryPolicy(False,False,True))
    with store:
        identifier=(await memory.seed('a','seed',timestamp=0,entries=[{'content':'before'}]))[0]
        async def failed(*args,**kwargs):raise OSError('embedding transport failure')
        memory.embed=failed
        actions=Actions(lambda *args:True)
        for action in memory.actions():actions.register(action)
        from tests.primary.test_kernel_memory_activation import session
        current=session('a',actions=actions)
        async with memory.activation(current,tid):
            with pytest.raises(OSError,match='embedding transport failure'):
                await current.actions.invoke('memory.update',Ref('memory','actor','a'),
                                             {'memory_id':identifier,'content':'after'})
        assert memory.get(identifier,actor='a')['content']=='before'


@pytest.mark.asyncio
async def test_review_import_generator_failure_preserves_existing_dimension_and_revision(tmp_path):
    store,threads,tid,memory,embed,client=setup(tmp_path)
    with store:
        identifier=(await memory.seed('a','seed',timestamp=0,entries=[{'content':'before'}]))[0]
        before=store.read(lambda r:r.query('SELECT * FROM memory_state'))
        def records():
            yield {'id':'new','content':'valid','timestamp':1,'embedding':[1.,2.]}
            raise RuntimeError('interrupted transfer')
        with pytest.raises(RuntimeError,match='interrupted transfer'):
            memory.import_records('a',records(),visible_step=1)
        assert store.read(lambda r:r.query('SELECT * FROM memory_state'))==before
        assert store.read(lambda r:r.query('SELECT step FROM memory_visibility WHERE actor=?',('a',)))==[(0,)]
        exported=[]
        assert memory.export('a',exported.append)==1
        assert exported[0]['id']==identifier
        assert exported[0]['content']=='before'
