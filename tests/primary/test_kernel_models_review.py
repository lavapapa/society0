"""真实 SDK 适配层的非作者请求身份验收。"""
import pytest
import httpx
import openai
from openai.types.chat import ChatCompletion
from society0.kernel.models import ModelProvider
from society0.kernel.storage import StageStore
from society0.kernel.threads import THREAD_SCHEMA, ThreadStore


@pytest.mark.asyncio
async def test_review_retry_evidence_preserves_actual_request_when_thread_advances(tmp_path):
    with StageStore.create(tmp_path/'run', THREAD_SCHEMA) as store:
        threads = ThreadStore(store)
        tid = threads.open('a', 0, 'decision')
        original = {'role': 'user', 'content': 'original'}
        threads.append_message(tid, original)
        provider = ModelProvider([{'id': 'test', 'api_key': 'unused', 'base_url': 'http://unused.invalid/v1',
            'model': 'fake', 'concurrency': 1, 'trust_env': False}], threads, retry_delay=0)
        actual = []
        async def create(**kwargs):
            actual.append(kwargs['messages'])
            if len(actual) == 1:
                threads.append_message(tid, {'role': 'user', 'content': 'arrived during request'})
                raise openai.APITimeoutError(request=httpx.Request('POST', 'http://unused.invalid'))
            return ChatCompletion(id='r', created=0, model='fake', object='chat.completion',
                choices=[{'index':0,'finish_reason':'stop','message':{'role':'assistant','content':'done'}}])
        provider.manager.clients['test'].chat.completions.create = create
        try:
            await provider.request(tid, {})
            records = [e for e in threads.tail(tid)['items'] if e['kind']=='request']
            assert len(records) == len(actual) == 2
            for record, sent in zip(records, actual):
                assert threads.read_request(tid, record['seq'])['messages'] == sent
            assert actual == [[original], [original]]
        finally:
            await provider.close()


@pytest.mark.asyncio
async def test_review_provider_diagnostics_do_not_encode_or_copy_full_history(tmp_path, monkeypatch):
    import society0.resource_managers as resource
    with StageStore.create(tmp_path/'run', THREAD_SCHEMA) as store:
        threads=ThreadStore(store)
        tid=threads.open('a',0,'decision')
        messages=[{'role':'user','content':'完整原文🙂'*10000} for _ in range(20)]
        for message in messages: threads.append_message(tid,message)
        provider=ModelProvider([{'id':'test','api_key':'unused','base_url':'http://unused.invalid/v1',
            'model':'fake','concurrency':1,'trust_env':False}],threads)
        encoded=[]
        copied=[]
        original_size=resource._safe_json_size
        original_trace=provider.manager._traceable_provider_request
        def size(value):
            if isinstance(value,dict) and 'messages' in value: encoded.append(len(value['messages']))
            return original_size(value)
        def trace(value):
            if 'messages' in value: copied.append(len(value['messages']))
            return original_trace(value)
        monkeypatch.setattr(resource,'_safe_json_size',size)
        monkeypatch.setattr(provider.manager,'_traceable_provider_request',trace)
        async def create(**kwargs):
            assert kwargs['messages']==messages
            return ChatCompletion(id='r',created=0,model='fake',object='chat.completion',
                choices=[{'index':0,'finish_reason':'stop','message':{'role':'assistant','content':'done'}}])
        provider.manager.clients['test'].chat.completions.create=create
        try:
            assert provider.manager._log_context is None
            await provider.request(tid,{})
            assert (encoded,copied)==([],[]), 'diagnostic-only full-history encoding/copy'
        finally:
            await provider.close()
