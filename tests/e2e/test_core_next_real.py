"""新版真实提供方验收；与确定性测试分开，运行失败产物保留。"""
import asyncio
import json
import os
import re
from pathlib import Path
import pytest

from tests.e2e.core_next_real_support import configuration,plan

pytestmark=[pytest.mark.e2e,pytest.mark.real_e2e,
    pytest.mark.skipif(os.environ.get('SOCIETY0_RUN_CORE_REAL')!='1',reason='Explicit new-Core real endpoint run required')]


@pytest.fixture
def config():return configuration()


@pytest.fixture
def destination(config,request):
    path=config['output']/request.node.name
    assert not path.exists(),'A real case must retain its prior attempt; select a new output root.'
    return path


def policy(**options):
    from society0.kernel.llm import LLMPolicy
    return LLMPolicy(max_turns=8,max_action_calls=4,**options)


def successful(held):
    assert held['outcomes']
    assert all(row.result.status=='completed' for row in held['outcomes']),[(row.actor_id,row.result) for row in held['outcomes']]


def decision_answer(reader,actor,time):
    """取得后置记忆提取之前的主体最终正文，排除user召回材料。"""
    from society0.kernel.threads import ThreadStore
    threads=ThreadStore(reader);tid=threads.find(actor,{'time':time,'phase':'decision'})
    assert tid is not None
    through=threads.describe(tid)['last_seq']
    requests=reader.read(lambda view:list(view.iter_query(
        "SELECT seq FROM thread_events WHERE thread_id=? AND kind='request' ORDER BY seq",(tid,))))
    for (seq,) in requests:
        options=threads.read_request(tid,seq)['provider_options']
        if any(tool.get('function',{}).get('name')=='extract_memories' for tool in options.get('tools',[])):
            through=seq-1
            break
    messages=threads.snapshot_messages(tid,through=through)['messages']
    answers=[message['content'] for message in messages
             if message.get('role')=='assistant' and isinstance(message.get('content'),str) and message['content'].strip()]
    assert answers,'Decision must contain an assistant answer before memory extraction'
    return answers[-1]


def assert_order_answer(answer):
    # 自动核对事实字段；保存完整句子以复核否定、猜测等语义。
    assert re.search(r'(?<![A-Za-z0-9])B42(?![A-Za-z0-9])',answer),answer
    assert re.search(r'(?<![0-9])500(?![0-9])|五百',answer),answer


async def execute(config,destination,**options):
    from society0.kernel.runner import run_plan
    current,held=plan(config,destination,**options)
    try:
        await run_plan(destination,current)
    finally:
        if destination.exists():
            (destination/'sdk-intervals.json').write_text(json.dumps({'peak':held['peak'],'intervals':held['intervals']}))
    successful(held)
    from society0.kernel.storage import StageReader
    from society0.kernel.threads import ThreadStore
    from society0.kernel import usage
    with StageReader(destination) as reader:
        counts=reader.read(usage.read)
        assert sum(row['requests'] for row in counts['models'] if row['kind']=='llm')>=len(held['outcomes'])
        assert counts['totals']['errors']==0 and counts['totals']['cancelled']==0
        threads=ThreadStore(reader)
        requests=reader.read(lambda view:list(view.iter_query("SELECT thread_id,seq FROM thread_events WHERE kind='request' ORDER BY thread_id,seq")))
        assert requests
        for tid,seq in requests:
            recorded=threads.read_request(tid,seq)
            assert recorded['messages'] and recorded['provider_options']['model']==config['llm']['endpoints'][0]['model']
    return held


@pytest.mark.asyncio
async def test_real_endpoint_smoke_llm_and_embedding(config,destination):
    async def after(ctx,phase,held):
        vectors=await ctx.require('embeddings','embeddings')['main'].embed(['主体甲独立原文','主体乙独立原文'],metadata={'purpose':'real_smoke'})
        assert len(vectors)==2 and all(len(v)==config['embed']['dimensions'] for v in vectors)
        assert vectors[0]!=vectors[1]
    await execute(config,destination,goals='请用一句话回答测试已就绪。',policy=policy(),after=after)


@pytest.mark.asyncio
async def test_real_endpoint_saturation_llm_and_embedding_managers(config,destination):
    async def after(ctx,phase,held):
        provider=ctx.require('embeddings','embeddings')['main']
        texts=['主体 '+actor+' 独立测试原文' for actor in 'abc']
        vectors=await asyncio.gather(*(provider.embed([text],metadata={'actor':actor,'purpose':'capacity'}) for actor,text in zip('abc',texts)))
        assert len(vectors)==3 and all(len(item[0])==config['embed']['dimensions'] for item in vectors)
    held=await execute(config,destination,goals='请回答你的主体编号。',policy=policy(),actors=tuple('abc'),capacity=3,after=after)
    assert len(held['outcomes'])==3
    assert held['peak']['activation']==3
    assert 1<held['peak']['llm_sdk']<=config['llm']['endpoints'][0]['concurrency']


@pytest.mark.asyncio
async def test_real_interview_writes_artifacts(config,destination):
    schema={'type':'object','properties':{'score':{'type':'integer'},'reason':{'type':'string'}},'required':['score','reason'],'additionalProperties':False}
    held=await execute(config,destination,goals='访谈测量：请给出 score 8，并说明“按明确测量任务回答”。使用 submit_result 返回完整结构。',policy=policy(mode='interview',result_schema=schema))
    assert held['outcomes'][0].result.value['result']['score']==8


@pytest.mark.asyncio
async def test_real_saturation_memory_and_logs(config,destination):
    async def after(ctx,phase,held):
        hits=await asyncio.gather(*(held['memory'].recall(actor,'重要采购决定',top_k=10,current_step=held['store'].complete_step+1) for actor in 'ab'))
        assert all(items for items in hits)
    await execute(config,destination,goals='请记住并确认：今天我以每吨2000元采购3吨原料，这是以后决策必须记住的重要经历。',
        policy=policy(),actors=('a','b'),capacity=2,memory=True,after=after)


@pytest.mark.asyncio
async def test_real_phase_capacity_overrides_runtime(config,destination):
    config=type(config)(config,llm=dict(config['llm'],endpoints=[dict(config['llm']['endpoints'][0],concurrency=1)]))
    held=await execute(config,destination,goals='请回答你的主体编号。',policy=policy(),actors=tuple('abc'),capacity=1,phase_capacity=3)
    from society0.kernel.storage import StageReader
    from society0.kernel.results import Results
    with StageReader(destination) as reader:
        header=Results(reader).phase(1,1)
        assert header['capacity']==3 and header['concurrency_source']=='phase'
    assert len(held['outcomes'])==3
    assert held['peak']['activation']==3 and held['peak']['llm_sdk']==1


@pytest.mark.asyncio
async def test_real_memory_roundtrip(config,destination):
    from society0.kernel.runner import run_plan
    async def after(ctx,phase,held):
        hits=await held['memory'].recall('a','三吨原料每吨2000元',top_k=10,current_step=held['store'].complete_step+1)
        assert hits
        return [item['content'] for item in hits]
    held=await execute(config,destination,goals='重要经历：我今天采购三吨原料，每吨2000元。请确认并记住这条经历。',policy=policy(),memory=True,after=after)
    branch=destination.with_name(destination.name+'-restored')
    second,restored=plan(config,branch,goals='请根据你的记忆说明先前采购的数量与单价。',policy=policy(),memory=True,moments=(2,),after=after)
    await run_plan(branch,second,source=destination)
    successful(restored)
    assert set(held['after'][0]).issubset(set(restored['after'][0]))
    from society0.kernel.storage import StageReader
    with StageReader(branch) as reader:answer=decision_answer(reader,'a',2)
    (branch/'decision-answer.json').write_text(json.dumps({'answer':answer},ensure_ascii=False))
    assert re.search(r'(?:3|三)\s*吨',answer),answer
    assert re.search(r'(?<![0-9])(?:2000|2,000)(?![0-9])|两千|二千',answer),answer


@pytest.mark.asyncio
async def test_real_complete_boundary_memory_restore(config,destination):
    from society0.kernel.runner import run_plan
    from society0.kernel.storage import StageStore
    from society0.kernel.threads import ThreadStore
    from society0.kernel.storage import StageReader
    from society0.kernel.memory import _payload
    def memory_snapshot(reader):
        return reader.read(lambda v:[(row,_payload(v,row[0]),v.query('SELECT dimension,vector FROM memory_vectors WHERE id=?',(row[0],)))
            for row in v.iter_query('SELECT id,actor,type,timestamp,importance,state,visible_step FROM memory_rows ORDER BY id')])
    await execute(config,destination,goals='重要经历：一号合同已支付500元。请确认并记住。',policy=policy(),memory=True)
    with StageReader(destination) as original:
        original_memories=memory_snapshot(original)
        assert original_memories and all(row[2] for row in original_memories)
    branch=destination.with_name(destination.name+'-failed')
    candidate,_=plan(config,branch,goals='本次未完成步骤的新观察：尚未发布的报价600元。请确认。',policy=policy(),memory=True,moments=(2,),fail_after=True)
    with pytest.raises(RuntimeError,match='deliberate incomplete'):
        await run_plan(branch,candidate,source=destination)
    with StageStore.restore(branch,destination.with_name(destination.name+'-recovered')) as recovered:
        assert recovered.complete_step==1
        assert ThreadStore(recovered).find('a',{'time':2,'phase':'decision'}) is None
        assert memory_snapshot(recovered)==original_memories
        assert not any('600' in json.dumps(row[1],ensure_ascii=False) for row in memory_snapshot(recovered))


@pytest.mark.asyncio
async def test_real_round_robin_action_loop(config,destination):
    goal=lambda s:'你本轮的行动目标是 '+json.dumps({'namespace':'chat','kind':'participants','key':s.actor.id})+'。发现并描述可用行动，向已配对伙伴发送一次且仅一次原文“共同讨论产业预期”，然后结束。'
    await execute(config,destination,goals=goal,policy=policy(completion_names=('chat.send_message_to_partner',)),mechanism='round',actors=('a','b'))
    from society0.kernel.storage import StageReader
    with StageReader(destination) as reader:
        rows=reader.read(lambda v:v.query('SELECT count(*) FROM chat_messages'))
        assert rows[0][0]==2
        bodies=reader.read(lambda v:v.query('SELECT body FROM chat_messages ORDER BY id'))
        assert [row[0].decode() for row in bodies]==['共同讨论产业预期']*2


@pytest.mark.asyncio
async def test_real_social_publish_with_memory(config,destination):
    goal='在 social 社交机制中，你的目标为 {"namespace":"social","kind":"participants","key":"a"}。发现并描述发布行动，发布一次且仅一次原文“原料价格保持稳定”，tags 为 ["market"]。'
    await execute(config,destination,goals=goal,policy=policy(completion_names=('social.publish_post',)),mechanism='social',memory=True)
    from society0.kernel.storage import StageReader
    with StageReader(destination) as reader:
        assert reader.read(lambda v:v.query('SELECT body FROM social_bodies'))==[('原料价格保持稳定'.encode(),)]


@pytest.mark.asyncio
async def test_real_environment_action_tag_completion(config,destination):
    from society0.kernel.interaction import Action,ActionResult
    async def setup(ctx,phase,held):
        registry=ctx.require('interaction','actions')
        registry.register(Action('record_observation',('survey','participants'),'提交本轮观测',
            {'type':'object','properties':{'value':{'type':'integer'}},'required':['value'],'additionalProperties':False},
            lambda scope,target,args:ActionResult('completed',args),tags=('measurement',)))
    held=await execute(config,destination,goals='你的行动目标为 {"namespace":"survey","kind":"participants","key":"a"}。发现并描述测量行动，提交 value 7。',
        policy=policy(required_tags=('measurement',),completion_tags=('measurement',)),setup=setup)
    assert held['outcomes'][0].result.reason=='terminal_action'


@pytest.mark.asyncio
async def test_real_terminal_rejection_then_success(config,destination):
    from society0.kernel.interaction import Action,ActionResult
    calls=[]
    async def setup(ctx,phase,held):
        def handler(scope,target,args):
            calls.append(args)
            return ActionResult('rejected',{'reason':'本次校验未通过；请根据返回消息重新提交同一 value。'}) if len(calls)==1 else ActionResult('completed',args)
        ctx.require('interaction','actions').register(Action('submit_measurement',('survey','participants'),'提交本轮测量，依据返回值判定成功',
            {'type':'object','properties':{'value':{'type':'integer'}},'required':['value'],'additionalProperties':False},handler,terminal=True))
    await execute(config,destination,goals='目标 {"namespace":"survey","kind":"participants","key":"a"}：发现并描述提交测量行动，value 为 9，若被拒绝按返回说明再次提交，成功后结束。',policy=policy(),setup=setup)
    assert calls==[{'value':9},{'value':9}]


@pytest.mark.asyncio
async def test_real_social_browse_completion_and_memory(config,destination):
    from society0.kernel.interaction import InteractionScope,Moment,Ref
    async def setup(ctx,phase,held):
        scope=InteractionScope('b',Moment(phase.moment.time,'seed'))
        try:
            result=await ctx.require('interaction','actions').invoke(scope,'social.publish_post',Ref('social','participants','b'),{'content':'重要市场信息：明日物流费用增加10%。','tags':['market']})
            assert result.status=='completed'
        finally:scope.close()
    goal='你的目标为 {"namespace":"social","kind":"participants","key":"a"}。查找并描述 get_trending_posts 行动，执行一次读取市场信息。'
    await execute(config,destination,goals=lambda s:goal if s.actor.id=='a' else '请回答已发布市场信息。',
        policy=policy(completion_names=('social.get_trending_posts',)),mechanism='social',memory=True,actors=('a','b'),setup=setup)
    from society0.kernel.storage import StageReader
    from society0.kernel.threads import ThreadStore
    with StageReader(destination) as reader:
        threads=ThreadStore(reader);tid=threads.find('a',{'time':1,'phase':'decision'})
        messages=threads.read_messages(tid)
        calls={call['id']:call for message in messages for call in message.get('tool_calls',[])}
        feedback=[]
        for message in messages:
            if message['role']!='tool':continue
            call=calls[message['tool_call_id']]
            if call['function']['name']!='action_invoke':continue
            arguments=json.loads(call['function']['arguments'])
            if arguments['name']=='social.get_trending_posts':feedback.append(json.loads(message['content']))
        assert any(item.get('result',{}).get('status')=='completed' and
                   '重要市场信息：明日物流费用增加10%。' in json.dumps(item['result'].get('value'),ensure_ascii=False)
                   for item in feedback),feedback


@pytest.mark.asyncio
async def test_real_multi_tick_social_workflow(config,destination):
    def goal(session):
        return '目标 {"namespace":"social","kind":"participants","key":"a"}：发现并描述发布行动，发布一次原文“这是第'+str(session.step)+'步的完整更新”，tags 为 []。'
    await execute(config,destination,goals=goal,policy=policy(completion_names=('social.publish_post',)),mechanism='social',memory=True,moments=(1,2))
    from society0.kernel.storage import StageReader
    with StageReader(destination) as reader:
        assert reader.read(lambda v:v.query(
            'SELECT p.author,p.created_tick,b.body FROM social_posts p JOIN social_bodies b ON b.id=p.id ORDER BY p.ordinal'))==[
                ('a',1,'这是第1步的完整更新'.encode()),('a',2,'这是第2步的完整更新'.encode())]


def test_real_exit_restore_memory_and_observation(config,destination):
    import subprocess,sys
    from society0.kernel.storage import StageStore,StageReader
    from society0.kernel.threads import ThreadStore
    destination.mkdir(parents=True)
    def child(mode):
        result=subprocess.run([sys.executable,'-m','tests.e2e.core_next_real_process',str(destination),mode],
            capture_output=True,text=True,timeout=600)
        body=result.stdout+'\n'+result.stderr
        for kind in ('llm','embed'):
            for endpoint in config[kind]['endpoints']:body=body.replace(endpoint['api_key'],'[credential]')
        (destination/(mode+'-process.log')).write_text(body)
        return result.returncode
    assert child('source')==91
    with StageReader(destination/'source') as reader:
        threads=ThreadStore(reader)
        dirty=threads.find('a',{'time':2,'phase':'decision'})
        assert dirty is not None
    from society0.kernel.observation import Observation
    with Observation(destination/'source') as observer:
        assert observer.status()['complete']['step']==1
        assert observer.thread_tail(dirty)['complete_through']==0
    with StageStore.prepare_readonly(destination/'source',destination/'before',step=1) as reader:
        threads=ThreadStore(reader);tid=threads.find('a',{'time':1,'phase':'decision'})
        original=threads.read_messages(tid)
        assert threads.find('a',{'time':2,'phase':'decision'}) is None
    assert child('restore')==0
    with StageReader(destination/'restored') as reader:
        threads=ThreadStore(reader)
        assert threads.read_messages(tid)==original
        second=threads.find('a',{'time':2,'phase':'decision'})
        answer=decision_answer(reader,'a',2)
        (destination/'restored-answer.json').write_text(json.dumps({'thread_id':second,'answer':answer},ensure_ascii=False))
        assert_order_answer(answer)
    assert json.loads((destination/'restored-result.json').read_text())['complete_step']==2


def discovery_catalog_plugin():
    from society0.kernel.plugins import Plugin
    from society0.kernel.information_sql import SQLInformation,DatasetSpec,DocumentSpec
    from society0.kernel.interaction import Action,ActionResult
    schema=('CREATE TABLE prices(id INTEGER PRIMARY KEY,amount INTEGER NOT NULL)',
        'CREATE TABLE catalog_actors(id TEXT PRIMARY KEY NOT NULL,task TEXT NOT NULL)',
        'CREATE TABLE reports(id INTEGER PRIMARY KEY,body TEXT NOT NULL)',
        'CREATE TABLE submissions(id INTEGER PRIMARY KEY,actor TEXT NOT NULL,count INTEGER NOT NULL,total INTEGER NOT NULL,phrase TEXT NOT NULL)')
    def initialize(writer):
        writer.executemany('INSERT INTO prices VALUES(?,?)',[(i,i*7) for i in range(1,13)])
        writer.execute('INSERT INTO catalog_actors VALUES(?,?)',('a','核对所有报价后提交总数、合计与报告核对短语。'))
        writer.execute('INSERT INTO reports VALUES(1,?)',('完整信息：报价均为同一计量单位，金额按各行 amount 合计。\n核对短语：原文校验成功。',))
    def install(ctx):
        store=ctx.require('storage','store')
        ctx.require('interaction','information').mount('/catalog',SQLInformation('catalog',store,{
            'prices':DatasetSpec('prices','id',('id','amount')),
            'actors':DatasetSpec('catalog_actors','id',('id','task'),authorize=lambda scope:('id=?',(scope.actor,))),
            'reports':DatasetSpec('reports','id',('id','body'),documents=(('body','body'),)),
            'body':DocumentSpec('reports','id','body')}))
        def submit(scope,target,args):
            store.transaction(lambda w:w.execute('INSERT INTO submissions(actor,count,total,phrase) VALUES(?,?,?,?)',
                (scope.actor,args['count'],args['total'],args['phrase'])))
            return ActionResult('completed',args)
        ctx.require('interaction','actions').register(Action('catalog.submit',('catalog','actors'),'提交本主体核对的完整报价结果',
            {'type':'object','properties':{'count':{'type':'integer'},'total':{'type':'integer'},'phrase':{'type':'string'}},
             'required':['count','total','phrase'],'additionalProperties':False},submit,terminal=True,
            available=lambda scope,target:target.key==scope.actor))
    return Plugin('catalog',('storage','interaction'),install,schema=schema,initialize=initialize)


@pytest.mark.asyncio
async def test_real_vfs_discovery_pagination_original_and_action(config,destination):
    from society0.kernel.llm import LLMPolicy
    task=('从共享根目录开始查找 catalog 信息。分页读取全部 prices，每页 limit=3，依据 total 和 next_cursor 读到结束，'
          '然后用 bash 中的 jq 对这些完整报价复算，输出 JSON 对象含 count 记录数和 total 金额合计。找到 reports 正文引用，'
          '每次 data_read 的 size=64，透传 expected_revision 并按 next_offset 续读直到结束，保留核对短语。'
          '从 actors 找到属于你的 Ref，使用 action_find 和 action_describe 发现提交方法，'
          '提交 count、total、phrase 三个字段，成功后结束。所有数字必须来自实际完整资料。')
    held=await execute(config,destination,goals=task,policy=LLMPolicy(max_turns=20,max_action_calls=2),
        workspace=True,extra_plugins=(discovery_catalog_plugin(),))
    feedback=assert_vfs_artifacts(destination)
    (destination/'tool-feedback.json').write_text(json.dumps(feedback,ensure_ascii=False))


def assert_vfs_artifacts(destination):
    """只读同一套真实工件断言，可复核已完成运行。"""
    from society0.kernel.storage import StageReader
    from society0.kernel.threads import ThreadStore
    with StageReader(destination) as reader:
        body=reader.read(lambda v:v.query('SELECT body FROM reports WHERE id=1'))[0][0]
        marker='\n核对短语：'
        assert body.count(marker)==1
        expected_phrase=body.split(marker,1)[1]
        assert reader.read(lambda v:v.query('SELECT actor,count,total,phrase FROM submissions'))==[('a',12,546,expected_phrase)]
        threads=ThreadStore(reader);tid=threads.find('a',{'time':1,'phase':'decision'})
        messages=threads.read_messages(tid)
        names=[call['function']['name'] for message in messages for call in message.get('tool_calls',[])]
        assert all(name in names for name in ('data_list','data_query','data_read','bash','action_find','action_describe','action_invoke'))
        assert names.count('data_read')>=2
        calls={call['id']:call for message in messages for call in message.get('tool_calls',[])}
        pages=[]
        for message in messages:
            if message['role']!='tool':continue
            call=calls[message['tool_call_id']]
            args=json.loads(call['function']['arguments']);response=json.loads(message['content'])
            if call['function']['name']=='data_query' and args.get('path')=='/catalog/prices' and not response.get('error'):
                pages.append((args['query'],response))
        assert len(pages)==4
        previous=None
        for query,page in pages:
            assert query.get('cursor')==previous and query['limit']==3 and page['total']==12
            previous=page['next_cursor']
        assert previous is None
        assert [row['id'] for _,page in pages for row in page['items']]==list(range(1,13))
        assert [row['amount'] for _,page in pages for row in page['items']]==[i*7 for i in range(1,13)]
        outputs=[json.loads(message['content']) for message in messages if message['role']=='tool']
        shell=next(value for value in outputs if 'stdout' in value)
        assert shell['exit_code']==0 and json.loads(shell['stdout'])=={'count':12,'total':546}
        return {'errors':[value['error'] for value in outputs if value.get('error')]}
