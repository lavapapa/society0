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
        memory.extract=ThreadMemoryExtractor(threads,Provider())
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
