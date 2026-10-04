"""非作者审查：真实提供方适配器的排队、快照失败与共享许可。"""
import asyncio
import pytest
from society0.kernel.models import ModelProvider,ThreadWriteError
from society0.kernel.storage import StageStore
from society0.kernel.threads import THREAD_SCHEMA,ThreadStore
from tests.primary.test_kernel_request_limit import endpoint,response


@pytest.mark.asyncio
async def test_review_cancelled_queued_profile_does_not_cancel_current_owner(tmp_path):
    entered=asyncio.Event();release=asyncio.Event();queued=asyncio.Event();calls=[]
    class Limit(asyncio.Semaphore):
        async def __aenter__(self):
            if self.locked():queued.set()
            return await super().__aenter__()
    limit=Limit(1)
    with StageStore.create(tmp_path/'run',THREAD_SCHEMA) as store:
        threads=ThreadStore(store);one=threads.open('one',1,'decision');two=threads.open('two',1,'decision')
        for tid in (one,two):threads.append_message(tid,{'role':'user','content':'完整请求🙂'})
        before=threads.describe(two)['last_seq']
        a=ModelProvider([endpoint('a')],threads,request_limit=limit)
        b=ModelProvider([endpoint('b')],threads,request_limit=limit)
        async def first(**kw):calls.append('a');entered.set();await release.wait();return response()
        async def second(**kw):calls.append('b');return response()
        a.manager.clients['a'].chat.completions.create=first;b.manager.clients['b'].chat.completions.create=second
        running=asyncio.create_task(a.request(one,{}));await asyncio.wait_for(entered.wait(),2)
        waiting=asyncio.create_task(b.request(two,{}));await asyncio.wait_for(queued.wait(),2)
        try:
            await b.close()
            with pytest.raises(asyncio.CancelledError):await waiting
            assert not running.done() and calls==['a'] and limit._value==0
            assert threads.describe(two)['last_seq']==before
            release.set();await running
            assert limit._value==1
        finally:release.set();await asyncio.gather(a.close(),b.close())


@pytest.mark.asyncio
async def test_review_snapshot_failure_releases_permits_for_next_complete_request(tmp_path):
    limit=asyncio.Semaphore(1);calls=[]
    with StageStore.create(tmp_path/'run',THREAD_SCHEMA) as store:
        threads=ThreadStore(store);tid=threads.open('a',1,'decision')
        original=[{'role':'user','content':'全文🙂'*10000}]
        threads.append_message(tid,original[0])
        before=threads.describe(tid)['last_seq']
        provider=ModelProvider([endpoint('a')],threads,request_limit=limit)
        snapshot=threads.snapshot_messages
        def broken(*args,**kwargs):raise OSError('snapshot unavailable')
        threads.snapshot_messages=broken
        async def create(**kwargs):calls.append(kwargs['messages']);return response()
        provider.manager.clients['a'].chat.completions.create=create
        try:
            with pytest.raises((ThreadWriteError,OSError),match='snapshot unavailable'):
                await provider.request(tid,{})
            assert limit._value==1 and calls==[] and threads.describe(tid)['last_seq']==before
            threads.snapshot_messages=snapshot
            await asyncio.wait_for(provider.request(tid,{}),2)
            assert calls==[original] and limit._value==1
        finally:await provider.close()
