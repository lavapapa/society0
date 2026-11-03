"""真实SDK传输边界：物理重试、退避和终态留证，测试本身不调用外部端点。"""
import asyncio
import httpx2
import pytest
from openai.types import CreateEmbeddingResponse
from openai.types.chat import ChatCompletion
from society0.kernel import models
from society0.kernel.models import ModelProvider, EmbeddingProvider, ProviderFailure, RESOURCE_SCHEMA
from society0.kernel.storage import StageStore
from society0.kernel.threads import THREAD_SCHEMA, ThreadStore
from tests.primary.provider_http import bind_chat, bind_embedding


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['model', 'embedding'])
@pytest.mark.parametrize('mode', ['three_connections_then_success', 'persistent', 'bad_request'])
async def test_profile_retry_defaults_bound_actual_waits_and_preserve_attempts(tmp_path, monkeypatch, kind, mode):
    endpoint = dict(id='retry',api_key='unused',base_url='http://unused.invalid/v1',model='fake',concurrency=1,trust_env=False)
    waits=[];physical=[];original_sleep=asyncio.sleep
    async def sleep(delay):
        if delay>0:waits.append(delay)
        await original_sleep(0)
    monkeypatch.setattr(models.asyncio,'sleep',sleep)
    monkeypatch.setattr(models.random,'uniform',lambda low,high:high)
    options=dict(backoff_max_s=2,jitter=.5) if mode=='persistent' else dict(jitter=0)
    with StageStore.create(tmp_path/'run',THREAD_SCHEMA+RESOURCE_SCHEMA) as store:
        threads=ThreadStore(store);thread=threads.open('a',0,'decision')
        threads.append_message(thread,dict(role='user',content='保留本轮原文'))
        provider=(ModelProvider([endpoint],threads,**options) if kind=='model' else
                  EmbeddingProvider([endpoint],store,threads,batch_wait_ms=0,**options))
        assert provider.max_attempts==6 and provider.backoff_initial_s==1
        async def respond(**wire):
            physical.append(wire)
            if mode=='bad_request':
                return httpx2.Response(400,json={'error':{'message':'bad parameter','type':'invalid_request_error','code':'bad'}})
            if mode=='persistent' or len(physical)<=3:
                raise httpx2.ConnectError('connection temporarily unavailable')
            if kind=='model':
                return ChatCompletion(id='ok',created=0,model='fake',object='chat.completion',
                    choices=[{'index':0,'finish_reason':'stop','message':{'role':'assistant','content':'done'}}])
            return CreateEmbeddingResponse(model='fake',object='list',usage={'prompt_tokens':1,'total_tokens':1},
                data=[{'index':index,'object':'embedding','embedding':[.1,.2]} for index,_ in enumerate(wire['input'])])
        await (bind_chat(provider,respond) if kind=='model' else bind_embedding(provider,respond))
        async def request():
            return await provider.request(thread,{}) if kind=='model' else await provider.embed(['完整记忆'],metadata={'actor':'a','thread_id':thread})
        try:
            if mode=='three_connections_then_success':
                result=await request()
                assert result['content']=='done' if kind=='model' else result==[[.1,.2]]
                assert len(physical)==4 and waits==[1,2,4]
            else:
                with pytest.raises(ProviderFailure):await request()
                assert len(physical)==(6 if mode=='persistent' else 1)
                assert waits==([1.5,2,2,2,2] if mode=='persistent' else [])
            assert all(delay<=provider.backoff_max_s for delay in waits)
            if kind=='model':
                events=threads.tail(thread)['items']
                requests=[threads.read_request(thread,item['seq']) for item in events if item['kind']=='request']
            else:
                ids=store.read(lambda view:view.query("SELECT id FROM resource_calls WHERE kind='embedding' ORDER BY rowid"))
                requests=[provider.calls.read(identifier)[0]['payload'] for identifier, in ids]
            assert len(requests)==len(physical)
            assert sum(item.get('retry_of') is not None for item in requests)==len(physical)-1
        finally:await provider.close()


def test_programming_and_storage_errors_are_not_provider_failures():
    assert not models._service_request_error(TypeError('program defect'),'openai')
    assert not models._service_request_error(models.ThreadWriteError('write failed'),'openai')
    assert not models._service_request_error(asyncio.CancelledError(),'openai')


@pytest.mark.asyncio
@pytest.mark.parametrize('kind,error_type',[(kind,error) for kind in ('model','embedding') for error in (TypeError,ValueError,models.ThreadWriteError,asyncio.CancelledError)]+[('embedding',ProviderFailure)])
async def test_provider_preserves_programming_errors_and_cancellation(tmp_path,monkeypatch,kind,error_type):
    from contextlib import asynccontextmanager
    endpoint=dict(id='direct',api_key='unused',base_url='http://unused.invalid/v1',model='fake',concurrency=1,trust_env=False)
    with StageStore.create(tmp_path/'run',THREAD_SCHEMA+RESOURCE_SCHEMA) as store:
        threads=ThreadStore(store);thread=threads.open('a',0,'decision')
        threads.append_message(thread,dict(role='user',content='原文'))
        provider=ModelProvider([endpoint],threads) if kind=='model' else EmbeddingProvider([endpoint],store,threads,batch_wait_ms=0)
        await provider._start();calls=[]
        failure=ProviderFailure('fixture_reason','fixture failure') if error_type is ProviderFailure else error_type('fixture failure')
        @asynccontextmanager
        async def broken_stream(*args,**kwargs):
            calls.append(1)
            raise failure
            yield  # 保持原生异步上下文管理器协议。
        async def broken_embed(*args,**kwargs):
            calls.append(1)
            raise failure
        monkeypatch.setattr(provider.endpoints[0].model,'request_stream' if kind=='model' else 'embed',broken_stream if kind=='model' else broken_embed)
        try:
            with pytest.raises(error_type) as caught:
                if kind=='model':await provider.request(thread,{})
                else:await provider.embed(['原文'],metadata={'actor':'a','thread_id':thread})
            assert len(calls)==1
            if error_type is ProviderFailure:
                assert caught.value.reason=='fixture_reason' and len(caught.value.embedding_attempts)==1
                assert caught.value.embedding_attempts==failure.embedding_attempts and caught.value.__cause__ is failure
            elif error_type is not asyncio.CancelledError:assert caught.value is failure
        finally:await provider.close()


@pytest.mark.asyncio
async def test_failed_microbatch_preserves_each_item_source_and_other_batch_success(tmp_path,monkeypatch):
    endpoint=dict(id='mixed',api_key='unused',base_url='http://unused.invalid/v1',model='fake',concurrency=2,trust_env=False)
    with StageStore.create(tmp_path/'run',RESOURCE_SCHEMA) as store:
        provider=EmbeddingProvider([endpoint],store,batch_texts=2,batch_wait_ms=0)
        async def respond(**wire):
            return CreateEmbeddingResponse(model='fake',object='list',usage={'prompt_tokens':1,'total_tokens':1},
                data=[{'index':index,'object':'embedding','embedding':[.1,.2]} for index,_ in enumerate(wire['input'])])
        await bind_embedding(provider,respond)
        original=provider.endpoints[0].model.embed
        async def mixed(texts,**kwargs):
            if any(text.startswith('bad-') for text in texts):raise ProviderFailure('fixture_reason','batch unavailable')
            return await original(texts,**kwargs)
        monkeypatch.setattr(provider.endpoints[0].model,'embed',mixed)
        inputs={'a':['bad-a','bad-b'],'b':['bad-b'],'c':['good']}
        try:
            results=await asyncio.gather(*(provider.embed(texts,metadata={'actor':actor}) for actor,texts in inputs.items()),return_exceptions=True)
            assert all(isinstance(result,ProviderFailure) and result.reason=='fixture_reason' for result in results[:2])
            assert results[0].embedding_attempts==results[1].embedding_attempts
            assert len(results[0].embedding_attempts)==1
            assert results[0].embedding_sources[0][1]==0 and results[1].embedding_sources[0][1]==1
            assert results[2]==[[.1,.2]] and provider._pending=={}
            assert (None,'bad-a') not in provider._cache and (None,'bad-b') not in provider._cache
            physical_ids=store.read(lambda view:view.query("SELECT id FROM resource_calls WHERE kind='embedding'"))
            physical={identifier:provider.calls.read(identifier)[0]['payload']['texts'] for identifier, in physical_ids}
            assert sorted(physical.values())==[['bad-a','bad-b'],['good']]
            use_ids=store.read(lambda view:view.query("SELECT id FROM resource_calls WHERE kind='embedding_use'"))
            uses=[provider.calls.read(identifier)[0]['payload'] for identifier, in use_ids]
            assert len(uses)==3
            for use in uses:
                actor=use['metadata']['actor']
                assert use['status']==('completed' if actor=='c' else 'failed')
                assert len(use['sources'])==len(inputs[actor])
                for source in use['sources']:
                    assert physical[source['call_id']][source['item_index']]==inputs[actor][source['input_index']]
            assert await provider.embed(['good'],metadata={'actor':'cached'})==[[.1,.2]]
            assert store.read(lambda view:view.query("SELECT count(*) FROM resource_calls WHERE kind='embedding'"))==[(2,)]
        finally:await provider.close()
