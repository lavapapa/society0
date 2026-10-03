import random
import pytest
from society0.kernel.selection import select_ids, sample_ids, result_rows, result_values, result_mean
from society0.kernel.actors import actor_plugin,ActorRecord
from society0.kernel.composition import compose
from society0.kernel.runtime import ActorResult,DriverResult


@pytest.mark.asyncio
async def test_selection_predicate_sample_and_inactive_identity(tmp_path):
    constructed=[]
    plugin=actor_plugin({'rule':lambda record:constructed.append(record.id)},records=[
        ActorRecord(str(i),'rule',state={'n':i},roles=('buyer',) if i%2==0 else ()) for i in range(30)])
    async with compose(tmp_path/'run',[plugin]) as host:
        actors=host.service('actors','actors')
        actors.deactivate('2')
        chosen=list(select_ids(actors,role='buyer',predicate=lambda record:record.state_values(('n',))['n']>10))
        assert chosen==[str(i) for i in range(12,30,2)] and constructed==[]
        first=sample_ids(iter(chosen),4,seed=19)
        assert first==sample_ids(iter(chosen),4,seed=19)
        assert len(set(first))==4 and all(item in chosen for item in first)
        assert [chosen.index(item) for item in first]==sorted(chosen.index(item) for item in first)
        assert sample_ids(iter(chosen),100,seed=19)==chosen
        assert '2' in list(select_ids(actors,active=None)) and actors.get_record('2').state=={'n':2}


def test_reservoir_retains_only_sample_and_consumes_full_source():
    live=0;peak=0;produced=0
    class Item:
        def __init__(self):
            nonlocal live,peak
            live+=1;peak=max(peak,live)
        def __del__(self):
            nonlocal live
            live-=1
    def source():
        nonlocal produced
        for _ in range(10000):
            produced+=1;yield Item()
    values=sample_ids(source(),7,seed=42)
    assert len(values)==7 and produced==10000 and peak<=10
    assert sample_ids(iter(()),3,seed=1)==[]
    with pytest.raises(ValueError):sample_ids(iter(()),-1)


def test_batch_results_keep_round_status_reason_and_full_values():
    body='full'*10000
    records=(ActorResult('a',0,DriverResult('completed',{'n':2,'body':body})),
             ActorResult('a',1,DriverResult('waiting',{'n':4})),
             ActorResult('b',0,DriverResult('incomplete',{'n':'bad'},'budget')))
    rows=list(result_rows(records))
    assert rows[0]['value']['body'] is body
    assert [(r['actor_id'],r['round'],r['status'],r['reason']) for r in rows]==[
        ('a',0,'completed',None),('a',1,'waiting',None),('b',0,'incomplete','budget')]
    assert list(result_values(records,'n'))==[2,4,'bad']
    assert result_mean(records,'n')==3.0 and result_mean(records,'absent') is None


@pytest.mark.asyncio
async def test_real_interview_submit_result_aggregates_explicit_nested_field(tmp_path):
    import json
    from society0.kernel.storage import StageStore
    from society0.kernel.threads import ThreadStore, THREAD_SCHEMA
    from society0.kernel.llm import LLMDriver, LLMPolicy
    from society0.kernel.runtime import Actor, Runtime, Phase
    from society0.kernel.interaction import Information, Actions
    class Provider:
        async def request(self, thread_id, options):
            return {'role':'assistant','content':'完整测量说明','finish_reason':'tool_calls',
                    'tool_calls':[{'id':'score','type':'function','function':{
                        'name':'submit_result','arguments':json.dumps({'score':8})}}]}
    schema={'type':'object','properties':{'score':{'type':'number'}},'required':['score'],'additionalProperties':False}
    with StageStore.create(tmp_path/'run',THREAD_SCHEMA) as store:
        threads=ThreadStore(store)
        driver=LLMDriver(Provider(),threads,input_builder=lambda session:[{'role':'system','content':'完整访谈背景'}],
                         policy=LLMPolicy(mode='interview',result_schema=schema))
        runtime=Runtime([Actor('a',driver)],information=Information(lambda *a:True),actions=Actions(lambda *a:True),store=store)
        collected=[]
        async def measure(context):
            context.activate('a');collected.extend(await context.drain())
        await runtime.run_step(1,1,[Phase('measurement',measure)])
        assert list(result_values(collected,('result','score')))==[8]
        assert result_mean(collected,('result','score'))==8.0
        assert list(result_rows(collected))[0]['value']['result']=={'score':8}
        await runtime.close()
