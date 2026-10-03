"""认知输入持久游标与完整内容消费者。"""
import json
from types import SimpleNamespace
import pytest
from society0.kernel.cognition import CognitiveInput, InputBatch
from society0.kernel.threads import THREAD_SCHEMA, ThreadStore
from society0.kernel.storage import StageStore
from society0.kernel.runtime import Actor, Session
from society0.kernel.interaction import Information, Actions, InteractionScope, Moment
from society0.kernel.llm import LLMDriver


class Provider:
    def __init__(self,threads):self.threads,self.inputs=threads,[]
    async def request(self,tid,options):
        self.inputs.append(self.threads.read_messages(tid))
        return {'content':'done','finish_reason':'stop'}


def session(driver,phase='A'):
    scope=InteractionScope('a',Moment(1,phase))
    record=SimpleNamespace(persona={'type':'类型完整背景','instance':'主体完整背景'},state={'full':'主观状态'},config={})
    return Session(Actor('a',driver,config=record),scope,Information(lambda *a:True).bound(scope),Actions(lambda *a:True).bound(scope),{},None,(),None)


@pytest.mark.asyncio
async def test_cognition_phase_return_and_restore_only_add_new_fov_and_keep_all_history(tmp_path):
    data=['第一条完整信息🙂'*1000]
    positions=[]
    async def perception(current,position):
        positions.append(position)
        position=position or 0
        return ([{'role':'user','content':text} for text in data[position:]],len(data))
    def make(store):
        threads=ThreadStore(store); provider=Provider(threads)
        builder=CognitiveInput(threads,perception,environment='完整环境说明',precision={'detail':'full'},reminders=lambda s:'完整提醒')
        return threads,provider,LLMDriver(provider,threads,input_builder=builder)
    with StageStore.create(tmp_path/'run',THREAD_SCHEMA) as store:
        threads,provider,driver=make(store)
        await driver.run(session(driver,'A'))
        await driver.run(session(driver,'B'))
        data.append('第二条')
        await driver.run(session(driver,'A'))
        tid=threads.find('a',Moment(1,'A'))
        content=[message['content'] for message in threads.read_messages(tid)]
        assert content.count(data[0])==1 and content.count(data[1])==1
        first=provider.inputs[0]
        combined='\n'.join(message['content'] for message in first)
        for text in ('类型完整背景','主体完整背景','主观状态','完整环境说明','完整提醒','full'):assert text in combined
        store.complete(1)
    with StageStore.restore(tmp_path/'run',tmp_path/'restored') as store:
        threads,provider,driver=make(store)
        data.append('第三条')
        await driver.run(session(driver,'A'))
        assert positions==[None,None,1,2]
        received=[message['content'] for message in provider.inputs[0]]
        assert all(received.count(text)==1 for text in data)


@pytest.mark.asyncio
async def test_input_batch_failure_keeps_cursor_and_messages_atomic(tmp_path):
    with StageStore.create(tmp_path/'run',THREAD_SCHEMA) as store:
        threads=ThreadStore(store); provider=Provider(threads)
        driver=LLMDriver(provider,threads,input_builder=lambda s:InputBatch([{'role':'user','content':'valid'},{'role':'user','content':object()}],'fov',1))
        current=session(driver)
        with pytest.raises(TypeError):await driver.run(current)
        tid=current.cursors['thread_id']
        assert threads.input_cursor(tid,'fov') is None
        assert threads.read_messages(tid)==[] and provider.inputs==[]


@pytest.mark.asyncio
async def test_cognition_cursor_survives_fresh_process(tmp_path):
    import os,subprocess,sys
    async def perception(current,position):return ([{'role':'user','content':'original full view'}],7)
    with StageStore.create(tmp_path/'run',THREAD_SCHEMA) as store:
        threads=ThreadStore(store)
        driver=LLMDriver(Provider(threads),threads,input_builder=CognitiveInput(threads,perception))
        await driver.run(session(driver))
        store.complete(1)
    script='''
import asyncio,sys
from pathlib import Path
from tests.primary.test_kernel_cognition import Provider,session
from society0.kernel.cognition import CognitiveInput
from society0.kernel.llm import LLMDriver
from society0.kernel.threads import ThreadStore
from society0.kernel.storage import StageStore
async def main():
    async def perception(current,position):
        assert position==7
        return ([{'role':'user','content':'new after restart'}],8)
    with StageStore.restore(Path(sys.argv[1]),Path(sys.argv[2])) as store:
        threads=ThreadStore(store); provider=Provider(threads)
        driver=LLMDriver(provider,threads,input_builder=CognitiveInput(threads,perception))
        await driver.run(session(driver))
        texts=[m['content'] for m in provider.inputs[0]]
        assert texts.count('original full view')==1 and texts.count('new after restart')==1
        store.complete(2)
asyncio.run(main())
'''
    result=subprocess.run([sys.executable,'-c',script,str(tmp_path/'run'),str(tmp_path/'branch')],capture_output=True,text=True,env={**os.environ,'PYTHONPATH':'src:.'})
    assert result.returncode==0,result.stderr


@pytest.mark.asyncio
@pytest.mark.parametrize('mode',['decision','interview'])
async def test_default_cognition_and_actual_memory_keep_system_first_and_measurement_read_only(tmp_path,mode):
    from society0.kernel.memory import Memory, MEMORY_SCHEMA, ThreadMemoryExtractor
    from society0.kernel.llm import LLMPolicy
    from tests.primary.test_kernel_memory import Embed,Client
    from tests.primary.test_kernel_llm import FakeProvider,reply,call
    with StageStore.create(tmp_path/'run',[*THREAD_SCHEMA,*MEMORY_SCHEMA]) as store:
        threads=ThreadStore(store)
        extraction=call('extract','extract_memories',{'memories':[{'content':'我保留决定经历','importance':3}]})
        provider=FakeProvider(threads,[reply(text='完整的决定或测量原文'),reply(extraction)])
        embed=Embed()
        memory=Memory(store,threads,embed=embed,client=Client(),extract=ThreadMemoryExtractor(threads,provider),recall_query=lambda s:'earlier')
        job=memory.prepare_job('a',None,'seed',timestamp=0,entries=[{'content':'此前完整记忆'}])
        await memory.finish_job(job)
        async def perception(current,position):return ([{'role':'user','content':'完整经营视图'}],1)
        driver=LLMDriver(provider,threads,input_builder=CognitiveInput(threads,perception),memory=memory,policy=LLMPolicy(mode=mode))
        result=await driver.run(session(driver))
        assert result.status=='completed'
        decision_input=provider.requests[0][2]
        assert decision_input[0]['role']=='system'
        contents='\n'.join(m['content'] for m in decision_input)
        assert all(text in contents for text in ('主体完整背景','完整经营视图','此前完整记忆','memory_actions_target'))
        assert contents.index('完整经营视图')<contents.index('此前完整记忆')
        assert len(provider.requests)==(2 if mode=='decision' else 1)
        if mode=='decision':
            extraction_input=provider.requests[1][2]
            assert extraction_input[0]['role']=='system'
            assert any(m['content']=='完整的决定或测量原文' for m in extraction_input)
        assert store.read(lambda r:r.query('SELECT count(*) FROM memory_rows')[0][0])==(2 if mode=='decision' else 1)
