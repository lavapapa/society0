"""唯一Runtime入口的步骤hooks、规则激活和流式结果消费者。"""
import pytest
from society0.kernel.runtime import Runtime,Phase,Actor,DriverResult
from society0.kernel.interaction import Information,Actions
from society0.kernel.results import Results,RESULTS_SCHEMA,StepResult
from society0.kernel.schedule import CodeSchedule,activate,Progress
from society0.kernel.storage import StageStore


@pytest.mark.asyncio
async def test_schedule_hooks_serial_visibility_results_and_single_completion(tmp_path):
    order=[]
    with StageStore.create(tmp_path/'run',[*RESULTS_SCHEMA,'CREATE TABLE business(id INTEGER PRIMARY KEY,n INTEGER NOT NULL)'],
        initialize=lambda w:w.execute('INSERT INTO business VALUES(1,0)')) as store:
        class Rule:
            async def run(self,session):
                value=store.read(lambda r:r.query('SELECT n FROM business')[0][0])
                order.append(('actor',session.actor.id,value))
                return DriverResult('completed',{'seen':value})
        actors={name:Actor(name,Rule()) for name in ('a','b')}
        results=Results(store);progress=Progress(tmp_path/'progress.json',store.read(lambda r:r.run_id))
        runtime=Runtime(actors,information=Information(lambda *a:True),actions=Actions(lambda *a:True),store=store,
                        results=results,progress=progress)
        def before():
            order.append('before');store.transaction(lambda w:w.execute('UPDATE business SET n=1'))
        async def phase(ctx):
            activated=await activate(ctx,iter(('a','b')))
            assert [item.actor_id for item in activated]==['a','b']
            return StepResult(metrics={'actors':len(activated)},tables={'rows':({'actor':item.actor_id,'seen':item.result.value['seen']} for item in activated)})
        async def after():
            order.append('after');store.transaction(lambda w:w.execute('UPDATE business SET n=n+1'))
        runtime._before=(('mechanism',before),)
        runtime._after=(('mechanism',after),)
        schedule=CodeSchedule(runtime,[Phase('decide',phase)])
        receipt=await schedule.run_step(1,42)
        assert receipt['step']==store.complete_step==1
        assert order==['before',('actor','a',1),('actor','b',1),'after']
        assert results.phase(1,1)['tables']['rows']
        assert progress.read()['state']=='ready'
        await runtime.close()


@pytest.mark.asyncio
async def test_progress_io_failure_isolated_but_result_generator_failure_aborts(tmp_path,monkeypatch):
    with StageStore.create(tmp_path/'run',RESULTS_SCHEMA) as store:
        progress=Progress(tmp_path/'progress.json',store.read(lambda r:r.run_id))
        def fail(*a,**k):raise OSError('diagnostic disk unavailable')
        monkeypatch.setattr(progress,'_replace',fail)
        runtime=Runtime([],information=Information(lambda *a:True),actions=Actions(lambda *a:True),store=store,
                        results=Results(store),progress=progress)
        await CodeSchedule(runtime,[Phase('ok',lambda ctx:StepResult(notes='ok'))]).run_step(1,0)
        def broken():
            yield {'original':1}
            raise ValueError('broken table')
        schedule=CodeSchedule(runtime,[Phase('fail',lambda ctx:StepResult(tables={'rows':broken()}))])
        with pytest.raises(ValueError,match='broken table'):await schedule.run_step(2,1)
        assert store.complete_step==1
        await runtime.close()


@pytest.mark.asyncio
async def test_sync_rule_and_collect_results_include_duration_and_order(tmp_path):
    from society0.kernel.schedule import RuleDriver
    with StageStore.create(tmp_path/'run',RESULTS_SCHEMA) as store:
        results=Results(store)
        def rule(session):return DriverResult('incomplete',{'original':'kept'},'budget')
        runtime=Runtime([Actor('a',RuleDriver(rule))],information=Information(lambda *a:True),actions=Actions(lambda *a:True),store=store,results=results)
        async def run(ctx):await activate(ctx,['a'])
        await CodeSchedule(runtime,[Phase('rule',run,incomplete='collect')]).run_step(1,7)
        row=results.page(results.phase(1,0)['activations'])['items'][0]['value']
        assert row['actor_id']=='a' and row['round']==1 and row['elapsed_s']>=0
        assert row['status']=='incomplete' and row['reason']=='budget' and row['value']=={'original':'kept'}
        assert results.summary()['incomplete_count']==1
        await runtime.close()


@pytest.mark.asyncio
async def test_runtime_host_hooks_include_late_plugins_and_quiesce_first(tmp_path):
    import asyncio
    from society0.kernel.plugins import Plugin
    from society0.kernel.composition import compose
    from society0.kernel.interaction import interaction_plugin
    from society0.kernel.runtime import runtime_plugin
    from society0.kernel.results import results_plugin
    order=[];started=asyncio.Event();alive=[True]
    class Driver:
        async def run(self,session):
            started.set()
            try:await asyncio.Event().wait()
            finally:
                assert alive[0]
                order.append('driver-stopped')
    def mechanism(name):
        def install(ctx):
            ctx.on_step(before=lambda:order.append('before-'+name),after=lambda:order.append('after-'+name))
            def close():
                order.append('close-'+name);alive[0]=False
            ctx.on_close(close)
        return Plugin(name,('runtime',),install)
    plugins=[interaction_plugin(lambda *a:True),results_plugin(),
        runtime_plugin([Actor('a',Driver())],information=('interaction','information'),actions=('interaction','actions'),
            store=('storage','store'),results=('results','results')),
        mechanism('first'),mechanism('second')]
    async with compose(tmp_path/'run',plugins) as host:
        rt=host.service('runtime','runtime')
        await rt.run_step(1,1,[Phase('body',lambda ctx:order.append('body'))])
        assert order==['before-first','before-second','body','after-first','after-second']
        async def active(ctx):ctx.activate('a')
        task=asyncio.create_task(rt.run_step(2,2,[Phase('active',active)]))
        await started.wait()
    assert task.cancelled()
    assert order.index('driver-stopped')<order.index('close-second')<order.index('close-first')


@pytest.mark.asyncio
async def test_after_hook_failure_prevents_complete(tmp_path):
    def fail():raise RuntimeError('after failed')
    with StageStore.create(tmp_path/'run',RESULTS_SCHEMA) as store:
        rt=Runtime([],information=Information(lambda *a:True),actions=Actions(lambda *a:True),store=store,
            after=(('business',fail),))
        with pytest.raises(RuntimeError,match='after failed'):await rt.run_step(1,0,[])
        assert store.complete_step==0
        await rt.close()


@pytest.mark.asyncio
async def test_composed_social_instances_automatically_flush_before_complete(tmp_path):
    from types import SimpleNamespace
    from society0.kernel.plugins import Plugin
    from society0.kernel.actors import ActorRecord,actor_plugin
    from society0.kernel.composition import compose
    from society0.kernel.interaction import interaction_plugin
    from society0.kernel.runtime import runtime_plugin
    from society0.kernel.results import results_plugin
    from society0.kernel.schedule import schedule_plugin
    from society0.plugins.social import social_plugin
    from society0.plugins.social_models import SocialNetworkConfig
    from tests.primary.test_kernel_memory import Embed,Client
    embed=Embed();client=Client();ids={}
    def resources(ctx):
        ctx.provide('embeddings',{'default':SimpleNamespace(embed=embed)})
        ctx.provide('client',client)
    config=SocialNetworkConfig(social_media={'recommendation':{'use_embedding_similarity':True}})
    async def body(ctx):
        for name in ('left','right'):
            social=host.service(name,'mechanism')
            ids[name]=social.execute('publish_post','a','a',{'content':name},0).value['post_id']
            social.trending(0,record_impressions=True)
    plugins=[actor_plugin({'rule':lambda r:None},records=[ActorRecord('a','rule')]),
        interaction_plugin(lambda *a:True),Plugin('vectors',install=resources),results_plugin(),
        runtime_plugin(information=('interaction','information'),actions=('interaction','actions'),store=('storage','store'),results=('results','results')),
        *(social_plugin('a',name=name,config=config,embedding=('vectors','default'),vector_client=('vectors','client')) for name in ('left','right')),
        schedule_plugin([Phase('publish',body)])]
    async with compose(tmp_path/'run',plugins) as host:
        await host.service('schedule','schedule').run_step(1,0)
        assert embed.calls==[['left'],['right']]
        for name in ('left','right'):assert host.service(name,'mechanism').post_details(ids[name])['view_count']==1
    async with compose(tmp_path/'restored',plugins,source=tmp_path/'run') as host:
        for name in ('left','right'):assert host.service(name,'mechanism').post_details(ids[name])['view_count']==1
        assert embed.calls==[['left'],['right']]


@pytest.mark.asyncio
async def test_schedule_lazy_selector_sync_rule_and_real_interview_driver(tmp_path):
    from society0.kernel.actors import ActorRecord,actor_plugin
    from society0.kernel.composition import compose
    from society0.kernel.plugins import Plugin
    from society0.kernel.interaction import interaction_plugin
    from society0.kernel.runtime import runtime_plugin
    from society0.kernel.results import results_plugin
    from society0.kernel.schedule import RuleDriver,schedule_plugin
    from society0.kernel.threads import THREAD_SCHEMA,ThreadStore
    from society0.kernel.llm import LLMDriver,LLMPolicy
    built=[];holder={}
    class Provider:
        async def request(self,thread_id,options):
            return {'role':'assistant','content':'完整测量回答🙂','finish_reason':'stop'}
    def rule(record):
        built.append(record.id)
        return RuleDriver(lambda session:DriverResult('completed',{'original':record.state}))
    def interview(record):
        built.append(record.id)
        return LLMDriver(Provider(),holder['threads'],input_builder=lambda session:[{'role':'system','content':'完整访谈背景'}],policy=LLMPolicy(mode='interview'))
    def thread_install(ctx):holder['threads']=ThreadStore(ctx.require('storage','store'))
    def select(role):
        cursor=None
        while True:
            page=host.service('actors','actors').select(role=role,limit=1,cursor=cursor)
            yield from page.items
            cursor=page.next_cursor
            if cursor is None:break
    async def rules(ctx):await activate(ctx,select('rule'))
    async def questions(ctx):await activate(ctx,select('interview'))
    plugins=[actor_plugin({'rule':rule,'interview':interview},records=[ActorRecord('r','rule',roles=('rule',)),ActorRecord('q','interview',roles=('interview',)),ActorRecord('idle','rule',active=False)]),
        Plugin('threads',('storage',),thread_install,schema=THREAD_SCHEMA),interaction_plugin(lambda *a:True),results_plugin(),
        runtime_plugin(actor_service=('actors','actors'),information=('interaction','information'),actions=('interaction','actions'),store=('storage','store'),results=('results','results')),
        schedule_plugin([Phase('rules',rules),Phase('interview',questions)])]
    async with compose(tmp_path/'run',plugins) as host:
        await host.service('schedule','schedule').run_step(1,7)
        assert built==['r','q']
        result=host.service('results','results')
        row=result.page(result.phase(1,1)['activations'])['items'][0]['value']
        assert row['status']=='completed'
        tid=holder['threads'].find('q',{'time':7,'phase':'interview'},kind='interview')
        assert holder['threads'].read_messages(tid)[-1]['content']=='完整测量回答🙂'


@pytest.mark.asyncio
async def test_step_receipt_metrics_restore_and_hook_cancel_never_completes(tmp_path):
    import asyncio
    event=asyncio.Event()
    with StageStore.create(tmp_path/'run',RESULTS_SCHEMA) as store:
        result=Results(store)
        rt=Runtime([],information=Information(lambda *a:True),actions=Actions(lambda *a:True),store=store,results=result)
        await rt.run_step(1,{'date':'today'},[Phase('empty',lambda ctx:None)])
        info=result.step(1)
        assert info['time']=={'date':'today'} and info['phase_count']==1 and info['activation_count']==0
        assert info['elapsed_s']>=0 and info['capacity']==1
        async def stop():
            event.set()
            await asyncio.Event().wait()
        rt._after=(('stop',stop),)
        task=asyncio.create_task(rt.run_step(2,2,[]))
        await event.wait();task.cancel()
        with pytest.raises(asyncio.CancelledError):await task
        assert store.complete_step==1
        await rt.close()
    with StageStore.restore(tmp_path/'run',tmp_path/'restored') as store:
        assert Results(store).step(1)==info
        with pytest.raises(KeyError):Results(store).step(2)
