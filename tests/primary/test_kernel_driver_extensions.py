"""驱动插件恢复与共同激活扩展合同。"""
from contextlib import asynccontextmanager
from types import SimpleNamespace
import pytest
from society0.kernel.activation import ActivationContext, activation_scope
from society0.kernel.drivers import RuleDriver, rule_driver_plugin
from society0.kernel.actors import ActorRecord, actor_plugin
from society0.kernel.composition import compose
from society0.kernel.runtime import DriverResult

@pytest.mark.asyncio
async def test_rule_extensions_keep_order_experience_and_cleanup():
    seen=[]
    @asynccontextmanager
    async def extension(context):
        seen.append('enter'); context.messages.append({'role':'user','content':'完整认知'})
        try:
            yield
            seen.append(('finish',context.result.status,context.experience))
        finally: seen.append('exit')
    async def rule(session):
        session.cursors['activation'].experience={'actual':'已经执行的规则经历'}
        return DriverResult('completed',{},'done')
    session=SimpleNamespace(cursors={})
    result=await RuleDriver(rule,extensions=(extension,)).run(session)
    assert result.status=='completed'
    assert seen==['enter',('finish','completed',{'actual':'已经执行的规则经历'}),'exit']
    assert 'activation' not in session.cursors

@pytest.mark.asyncio
async def test_rule_failure_cleans_extension_without_success():
    seen=[]
    @asynccontextmanager
    async def extension(context):
        try: yield
        finally: seen.append(context.result)
    async def fail(session): raise OSError('rule failed')
    with pytest.raises(OSError): await RuleDriver(fail,extensions=(extension,)).run(SimpleNamespace(cursors={}))
    assert seen==[None]

@pytest.mark.asyncio
async def test_driver_factory_service_and_restore_reference_validation(tmp_path):
    async def rule(session): return DriverResult('completed')
    def plugins(driver='rule'):
        return [rule_driver_plugin(rule),actor_plugin({'rule':('rule_driver','factory')},records=[ActorRecord('a',driver,config={'value':7})])]
    async with compose(tmp_path/'run',plugins()) as host:
        assert isinstance(host.service('actors','actors')['a'].driver,RuleDriver)
        assert host.service('actors','actors').get_record('a').config=={'value':7}
        host.service('storage','store').complete(1)
    with pytest.raises(ValueError,match='unresolved actor drivers'):
        async with compose(tmp_path/'restore',[actor_plugin({'other':lambda r:None})],source=tmp_path/'run'): pass

@pytest.mark.asyncio
async def test_rule_memory_without_thread_and_active_records_ranges(tmp_path):
    from society0.kernel.memory import Memory,MemoryPolicy,MemoryExtension,MEMORY_SCHEMA
    from society0.kernel.storage import StageStore
    from society0.kernel.interaction import InteractionScope,Moment,Ref
    from tests.primary.test_kernel_memory import Embed,Client
    observed=[];mounts=[]
    async def extract(actor,tid,experience,*,metadata):
        assert tid is None and experience=={'actual':'本次亲身经历'}
        observed.append(experience)
        return [{'content':'完整记忆🙂'*20000,'importance':4}]
    with StageStore.create(tmp_path/'run',MEMORY_SCHEMA) as store:
        memory=Memory(store,None,embed=Embed(),client=Client(),extract=extract,policy=MemoryPolicy(False,True,True))
        async def rule(session):
            context=session.cursors['activation'];mounts.append(context.mounts['memory'])
            context.experience={'actual':'本次亲身经历'}
            return DriverResult('completed')
        session=SimpleNamespace(cursors={},actor=SimpleNamespace(id='a'),step=1,scope=InteractionScope('a',Moment(1,'rule')))
        await RuleDriver(rule,extensions=(MemoryExtension(memory),)).run(session)
        assert len(observed)==1
        async with memory.activation(session,None):
            page=mounts[0].list('/records',limit=1);assert page.total==1 and page.next_cursor is None
            item=page.items[0];raw=mounts[0].read(item['path'],size=7)
            assert len(raw.data)==7 and raw.next_offset==7 and raw.total_bytes==item['raw_bytes']
            end=mounts[0].read(item['path'],offset=raw.total_bytes-3,size=100,expected_revision=raw.revision)
            assert len(end.data)==3 and end.next_offset is None
            with pytest.raises(ValueError,match='revision'):mounts[0].read(item['path'],expected_revision=-1)
        await memory.close()

@pytest.mark.asyncio
async def test_extension_available_to_input_builder_and_third_driver(tmp_path):
    from tests.primary.test_kernel_llm import setup,reply
    seen=[]
    @asynccontextmanager
    async def extension(context):
        context.mounts['study']='材料作用域';yield
        seen.append(context.result.status)
    store,threads,provider,driver,session,calls=setup(tmp_path,[reply(text='decision')])
    def inputs(current):
        assert current.cursors['activation'].mounts['study']=='材料作用域'
        return [{'role':'system','content':'完整背景'}]
    driver.input_builder=inputs;driver.extensions=(extension,)
    try: await driver.run(session)
    finally:store.close()
    class ThirdDriver:
        async def run(self,session):
            context=ActivationContext(session)
            async with activation_scope(context,(extension,)):
                assert context.mounts['study']=='材料作用域'
                context.result=DriverResult('completed')
            return context.result
    await ThirdDriver().run(SimpleNamespace(cursors={}))
    assert seen==['completed','completed']
