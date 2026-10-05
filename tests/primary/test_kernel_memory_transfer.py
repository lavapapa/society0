"""记忆原文与原向量转移、派生资源所有权。"""
import asyncio
import pytest
from society0.kernel.memory import Memory
from society0.kernel.storage import StageStore
from society0.kernel.threads import ThreadStore
from tests.primary.test_kernel_memory import setup,Client,Embed


@pytest.mark.asyncio
async def test_export_import_and_fork_keep_values_without_reembedding(tmp_path):
    source,threads,thread,memory,embed,client=setup(tmp_path/'source')
    with source:
        ids=await memory.seed('a','seed1',timestamp=0,entries=[{'content':'seed content','metadata':{'nested':[True,1,'中']}}])
        assert await memory.seed('a','seed1',timestamp=0,entries=[{'content':'seed content','metadata':{'nested':[True,1,'中']}}])==ids
        exported=[]
        assert memory.export('a',exported.append)==1
        assert len(embed.calls)==1
        target,target_threads,_,imported,target_embed,_=setup(tmp_path/'target')
        with target:
            assert imported.import_records('a',iter(exported),visible_step=2)==1
            await imported.sync_index()
            assert target_embed.calls==[]
            assert imported.get(ids[0],actor='a')==memory.get(ids[0],actor='a')
            target.complete(1)
            with StageStore.fork(target.path,tmp_path/'fork') as fork:
                fork_memory=Memory(fork,ThreadStore(fork),embed=target_embed,client=Client())
                await fork_memory.update(ids[0],actor='a',content='branch',timestamp=3)
                assert memory.get(ids[0],actor='a')['content']=='seed content'
                assert imported.get(ids[0],actor='a')['content']=='seed content'
                assert fork_memory.get(ids[0],actor='a')['content']=='branch'


@pytest.mark.asyncio
async def test_import_batch_failure_rolls_back_every_record_and_dimension(tmp_path):
    store,_,_,memory,_,_=setup(tmp_path)
    with store:
        def records():
            yield {'id':'valid','content':'one','embedding':[1.,2.],'timestamp':0}
            yield {'id':'invalid','content':'two','embedding':[1.,2.,3.],'timestamp':0}
        with pytest.raises(ValueError,match='dimension'):
            memory.import_records('a',records(),visible_step=1)
        assert store.read(lambda view:view.query('SELECT count(*) FROM memory_rows'))==[(0,)]
        assert store.read(lambda view:view.query('SELECT count(*) FROM memory_state'))==[(0,)]


@pytest.mark.asyncio
async def test_close_cancels_owned_jobs_and_keeps_shared_client_open(tmp_path):
    store,threads,thread,memory,embed,client=setup(tmp_path)
    with store:
        started=asyncio.Event()
        async def pending(texts,*,metadata):
            started.set()
            await asyncio.Event().wait()
        memory.embed=pending
        job=memory.prepare_job('a',thread,'pending',timestamp=0,entries=[{'content':'pending'}])
        task=asyncio.create_task(memory.finish_job(job))
        await started.wait()
        await memory.close()
        with pytest.raises(asyncio.CancelledError):await task
        assert memory.job(job)['state']=='prepared'
        assert client.collections=={}
        with pytest.raises(RuntimeError,match='closed'):await memory.finish_job(job)


@pytest.mark.asyncio
async def test_shared_client_owner_closes_after_memory_tasks_are_collected(tmp_path):
    from society0.kernel.plugins import Plugin,PluginHost
    store,threads,thread,unused,_,client=setup(tmp_path)
    order=[]
    started=asyncio.Event()
    holder={}
    async def embed(texts,*,metadata):
        started.set()
        try:await asyncio.Event().wait()
        finally:order.append('embedding_cancelled')
    def resources(context):
        context.provide('client',client)
        context.on_close(lambda:order.append('client_owner_closed'))
    def install(context):
        memory=Memory(store,threads,embed=embed,client=context.require('resources','client'))
        holder['memory']=memory
        context.on_close(memory.close)
        context.provide('memory',memory)
    with store:
        async with PluginHost([Plugin('resources',install=resources),Plugin('memory',('resources',),install)]):
            memory=holder['memory']
            job=memory.prepare_job('a',thread,'lifecycle',timestamp=0,entries=[{'content':'pending'}])
            task=asyncio.create_task(memory.finish_job(job))
            await started.wait()
        with pytest.raises(asyncio.CancelledError):await task
        assert order==['embedding_cancelled','client_owner_closed']
        assert memory.job(job)['state']=='prepared'
