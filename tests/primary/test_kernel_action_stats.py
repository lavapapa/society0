"""LLM 实际工具事实的累计诊断与完整恢复。"""
import pytest
from society0.kernel.storage import StageStore
from society0.kernel.threads import THREAD_SCHEMA,ThreadStore
from society0.kernel.observation import Observation


def test_action_outcomes_tags_error_refs_and_closed_activation(tmp_path):
    with StageStore.create(tmp_path/'run',THREAD_SCHEMA) as store:
        threads=ThreadStore(store);tid=threads.open('a',0,'decision')
        first=threads.start_action(tid,{'call_id':'1','name':'send','target':{},'arguments':{'full':'原文'*1000}})
        threads.finish_action(tid,first,{'call_id':'1','name':'send','result':{'status':'completed','value':'whole'}},status='completed',tags=('message',),elapsed_s=.25)
        second=threads.start_action(tid,{'call_id':'2','name':'send','target':{},'arguments':{}})
        failed=threads.finish_action(tid,second,{'call_id':'2','name':'send','error':'full error'},status='error',elapsed_s=.5)
        threads.close(tid,'incomplete',reason='domain_error',elapsed_s=1.)
        summary=Observation(store.path).action_summary(actor='a')
        assert summary['scope']=='llm_tool_actions'
        assert summary['action_counts']=={'send':2}
        assert summary['successful_action_counts']=={'send':1}
        assert summary['failed_action_counts']=={'send':1}
        assert summary['action_tag_counts']=={'message':1}
        assert summary['action_duration_summary']['send']=={'count':2,'total_s':.75,'max_s':.5}
        assert summary['error_samples'][0]['thread_id']==tid
        assert summary['error_samples'][0]['start_seq']==second
        assert summary['error_samples'][0]['finish_seq']==failed
        assert summary['activations']==[{'status':'incomplete','reason':'domain_error','count':1,'total_s':1.,'max_s':1.}]
        store.complete(1)
    with StageStore.restore(tmp_path/'run',tmp_path/'restored') as store:
        current=Observation(store.path).action_summary(actor='a')
        for key in ('action_counts','action_tag_counts','activations','error_samples'):
            assert current[key]==summary[key]


def test_action_outcome_failure_rolls_back_body_and_projection(tmp_path,monkeypatch):
    from society0.kernel import action_stats
    with StageStore.create(tmp_path/'run',THREAD_SCHEMA) as store:
        threads=ThreadStore(store);tid=threads.open('a',0,'decision')
        seq=threads.start_action(tid,{'call_id':'1','name':'tool','arguments':{}})
        before=threads.describe(tid)['last_seq'];original=action_stats.finish
        def fail(*args,**kwargs):
            original(*args,**kwargs)
            raise OSError('failed projection')
        monkeypatch.setattr(action_stats,'finish',fail)
        with pytest.raises(OSError):threads.finish_action(tid,seq,{'result':{'status':'completed'}},status='completed',elapsed_s=1.)
        assert threads.describe(tid)['last_seq']==before
        assert Observation(store.path).action_summary()['successful_action_counts']=={}


@pytest.mark.asyncio
async def test_real_shell_multi_actions_and_replayed_receipt_count_once(tmp_path):
    import json,shlex
    from types import SimpleNamespace
    from society0.kernel.llm import LLMDriver
    from society0.kernel.interaction import Actions,Information,Action,ActionResult,InteractionScope,Moment
    from society0.kernel.runtime import Session,Actor
    from society0.kernel.shell import ShellSession
    actions=Actions(lambda *args:True);information=Information(lambda *args:True);executed=[]
    def work(*args):executed.append(1);return ActionResult('completed',{'whole':'result'})
    actions.register(Action('work',('m','job'),'work',{'type':'object'},work,tags=('tag',)))
    arguments={'name':'work','target':{'namespace':'m','kind':'job','key':'1'},'arguments':{}}
    command='action invoke '+shlex.quote(json.dumps(arguments))
    call={'id':'shell-call','type':'function','function':{'name':'bash','arguments':json.dumps({'script':command+'; '+command})}}
    class Provider:
        def __init__(self):self.replies=iter([{'role':'assistant','content':'','tool_calls':[call],'finish_reason':'tool_calls'},{'role':'assistant','content':'finished','finish_reason':'stop'}])
        async def request(self,*args):return next(self.replies)
    for label,source in (('run',None),('restored',tmp_path/'run')):
        context=StageStore.create(tmp_path/label,THREAD_SCHEMA) if source is None else StageStore.restore(source,tmp_path/label)
        with context as store:
            threads=ThreadStore(store)
            driver=LLMDriver(Provider(),threads,input_builder=lambda s:[],shell_factory=lambda s,ledger:ShellSession(s.scope,information,bound_actions=ledger,result_dir=tmp_path/'shell'))
            scope=InteractionScope('a',Moment(1,'work'))
            session=Session(Actor('a',driver),scope,information.bound(scope),actions.bound(scope),{},None,(),SimpleNamespace(prepare_artifact=store.prepare_artifact))
            assert (await driver.run(session)).status=='completed'
            summary=Observation(store.path).action_summary()
            assert summary['action_counts']=={'work':2}
            assert summary['successful_action_counts']=={'work':2}
            assert summary['action_tag_counts']=={'tag':2}
            assert sum(row['count'] for row in summary['activations'])==(1 if source is None else 2)
            if source is None:store.complete(1)
    assert executed==[1,1]


def test_action_summary_current_counts_and_errors_do_not_scan_history(tmp_path):
    from society0.kernel import action_stats
    work=[]
    with StageStore.create(tmp_path/'run',THREAD_SCHEMA) as store:
        def insert(writer,start,end):
            for index in range(start,end):
                action_stats.start(writer,'thread',index,'a','work')
                action_stats.finish(writer,'thread',index,index+10000,'error',(),.1)
        for start,end in ((0,100),(100,10000)):
            store.transaction(lambda writer:insert(writer,start,end))
            steps=[0]
            def read(view):
                view._connection.set_progress_handler(lambda:steps.__setitem__(0,steps[0]+1) or False,1)
                try:return action_stats.read(view,actor='a',error_limit=5)
                finally:view._connection.set_progress_handler(None)
            summary=store.read(read)
            assert summary['action_counts']=={'work':end}
            assert len(summary['error_samples'])==5 and summary['error_samples'][0]['start_seq']==end-1
            work.append(steps[0])
        print({'history_actions':[100,10000],'query_vm_instructions':work})
        assert work[1]<work[0]*2


@pytest.mark.asyncio
async def test_nested_handler_is_one_explicit_llm_action_and_failure_is_diagnostic(tmp_path):
    import json
    from society0.kernel.llm import LLMDriver
    from society0.kernel.interaction import Actions,Information,Action,ActionResult,InteractionScope,Moment,Ref
    from society0.kernel.runtime import Session,Actor
    actions=Actions(lambda *a:True);ran=[]
    def inner(*args):ran.append('inner');return ActionResult('completed')
    async def outer(scope,target,arguments):
        ran.append('outer');await actions.invoke(scope,'inner',target,{})
        raise RuntimeError('domain fault')
    actions.register(Action('inner',('m','job'),'inner',{},inner))
    actions.register(Action('outer',('m','job'),'outer',{},outer))
    class Provider:
        async def request(self,*args):
            return {'role':'assistant','content':'','finish_reason':'tool_calls','tool_calls':[{'id':'c','type':'function','function':{'name':'action_invoke','arguments':json.dumps({'name':'outer','target':{'namespace':'m','kind':'job','key':'1'},'arguments':'{}'})}}]}
    with StageStore.create(tmp_path/'run',THREAD_SCHEMA) as store:
        store.complete(1);threads=ThreadStore(store);driver=LLMDriver(Provider(),threads,input_builder=lambda s:[])
        scope=InteractionScope('a',Moment(2,'work'));session=Session(Actor('a',driver),scope,Information(lambda *a:True).bound(scope),actions.bound(scope),{},None,(),None)
        with pytest.raises(RuntimeError,match='domain fault'):await driver.run(session)
        store.abort_step()
        summary=Observation(store.path).action_summary()
        assert ran==['outer','inner'] and summary['action_counts']=={'outer':1}
        assert summary['failed_action_counts']=={'outer':1}
        assert summary['activations'][0]['reason']=='RuntimeError' and summary['activations'][0]['status']=='incomplete'
    with StageStore.restore(tmp_path/'run',tmp_path/'restored',step=1) as store:
        assert Observation(store.path).action_summary()['action_counts']=={}
