"""同源真实对照研究消费者；凭据仅从 stdin 的一行读取。"""
import argparse
import asyncio
import dataclasses
import importlib.util
import json
import getpass
import ast
import shlex
from pathlib import Path
import sys
import time

MODEL = 'Qwen/Qwen3.8-27B'
OPTIONS = {'max_tokens': 1024, 'temperature': 0, 'parallel_tool_calls': False,
           'extra_body': {'enable_thinking': False}}


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2))


def load_module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def oracle(fixture, submissions):
    body = fixture['report']
    assert body.count('\n核对短语：') == 1
    expected = {'count': len(fixture['prices']),
                'total': sum(row['amount'] for row in fixture['prices']),
                'phrase': body.split('\n核对短语：', 1)[1]}
    assert submissions == [expected], (submissions, expected)
    return expected


def structured_originals(fixture,messages):
    calls={c['id']:c for m in messages for c in m.get('tool_calls',[])}
    pages=[]; chunks=[]
    for message in messages:
        if message['role']!='tool':continue
        call=calls[message['tool_call_id']]; name=call['function']['name']
        args=json.loads(call['function']['arguments']); value=json.loads(message['content'])
        if name=='data_query' and args['path']=='/catalog/prices':pages.append((args['query'],value))
        if name=='data_read':chunks.append((args,value))
    assert len(pages)==4
    previous=None; rows=[]
    for query,page in pages:
        assert query.get('cursor')==previous and query['limit']==3 and page['total']==12
        previous=page['next_cursor']; rows.extend(page['items'])
    assert previous is None and [{k:r[k] for k in ('id','amount')} for r in rows]==fixture['prices']
    offset=0; original=''
    for args,value in chunks:
        assert args['offset']==offset and args['size']==64
        assert args['expected_revision']==value['revision']
        original+=value['data'];offset=value['next_offset']
    assert offset is None and original==fixture['report']
    return {'complete_rows':len(rows),'complete_original_bytes':len(original.encode())}


def export(args):
    sys.path.insert(0,str(Path(__file__).resolve().parents[3]))
    from society0.kernel.storage import StageReader
    from tests.e2e.test_core_next_real import assert_vfs_artifacts
    assert_vfs_artifacts(args.new_run)
    with StageReader(args.new_run) as reader:
        fixture = reader.read(lambda v: {
            'prices': [dict(zip(('id', 'amount'), row)) for row in v.query('SELECT id,amount FROM prices ORDER BY id')],
            'actor': list(v.query('SELECT id,task FROM catalog_actors')),
            'report': v.query('SELECT body FROM reports WHERE id=1')[0][0],
        })
    fixture['new_run'] = str(args.new_run.resolve())
    fixture['new_contract'] = json.loads((args.new_run/'runner.json').read_text())
    oracle(fixture, [{'count': 12, 'total': 546, 'phrase': '原文校验成功。'}])
    save(args.fixture, fixture)


async def structured(args, fixture, key):
    support = load_module(args.source/'tests/e2e/core_next_real_support.py', 'v06_frozen_support')
    from society0.kernel.llm import LLMPolicy
    from society0.kernel.runner import run_plan
    from society0.kernel.storage import StageReader
    from society0.kernel.threads import ThreadStore
    from society0.kernel import usage
    # 冻结版既有领域消费者提供相同 SQLInformation 与 Action 合同。
    case = load_module(args.source/'tests/e2e/test_core_next_real.py', 'v06_frozen_case')
    config = support.RealConfiguration({'release': 'edabcad3e88ba7b7b92c68dcfa5c22bf6f2e304a', 'output': args.output,
        'llm': {'endpoints': [{'id': 'real', 'base_url': args.url, 'model': MODEL, 'api_key': key,
                             'timeout': 60, 'concurrency': 1, 'trust_env': False}],
                'max_attempts': 1, 'request_jitter': 0, 'session_transport': None,
                'request_options': {'max_tokens': 1024, 'temperature': 0, 'parallel_tool_calls': False,
                                    'openai_continuous_usage_stats':True,'extra_body': {'enable_thinking': False}}},
        'embed': {'endpoints': [{'id': 'unused', 'base_url': args.url, 'model': 'Qwen/Qwen3-Embedding-0.6B',
                               'api_key': key, 'concurrency': 1, 'timeout': 60, 'trust_env': False, 'send_dimensions': False}],
                  'dimensions': 1024, 'max_attempts': 1}})
    tree=ast.parse((args.source/'tests/e2e/test_core_next_real.py').read_text())
    test=next(n for n in tree.body if isinstance(n,ast.AsyncFunctionDef) and n.name=='test_real_vfs_discovery_pagination_original_and_action')
    task=ast.literal_eval(next(n.value for n in test.body if isinstance(n,ast.Assign) and n.targets[0].id=='task'))
    async def setup(ctx,phase,held):
        if not args.dry_run:return
        from tests.primary.scripted_provider import scripted_response
        threads=ctx.require('threads','threads'); turn=0; cursor=None; reference=None; offset=0
        async def scripted(tid,options,**kwargs):
            nonlocal turn,cursor,reference,offset
            prior=[m for m in threads.read_messages(tid) if m['role']=='tool']
            if prior:
                last=json.loads(prior[-1]['content'])
                assert not last.get('error'),last
                if 2<=turn<=5:cursor=last['next_cursor']
                if turn==6:reference=last['items'][0]['body']
                if turn in (7,8):offset=last['next_offset']
            target={'namespace':'catalog','kind':'actors','key':'a'}
            if turn==0:name,arguments='data_list',{'path':'/','limit':10,'cursor':None}
            elif 1<=turn<=4:name,arguments='data_query',{'path':'/catalog/prices','query':{'limit':3,'cursor':cursor}}
            elif turn==5:name,arguments='data_query',{'path':'/catalog/reports','query':{}}
            elif turn in (6,7):name,arguments='data_read',{'path':reference['path'],'offset':offset,'size':64,'expected_revision':reference['expected_revision']}
            elif turn==8:name,arguments='data_query',{'path':'/catalog/actors','query':{}}
            elif turn==9:name,arguments='action_find',{'target':target,'query':'submit','limit':10,'cursor':None}
            elif turn==10:name,arguments='action_describe',{'target':target,'name':'catalog.submit'}
            elif turn==11:name,arguments='bash',{'script':'printf %s '+shlex.quote(json.dumps(fixture['prices']))+" | jq '{count:length,total:map(.amount)|add}'"}
            else:name,arguments='action_invoke',{'target':target,'name':'catalog.submit','arguments':{'count':12,'total':546,'phrase':fixture['report'].split('\n核对短语：')[1]}}
            turn+=1
            return scripted_response(threads,tid,{'role':'assistant','content':'','tool_calls':[{'id':str(turn),'type':'function','function':{'name':name,'arguments':json.dumps(arguments)}}],'finish_reason':'tool_calls'})
        ctx.require('models','models')['main'].request_model=scripted
    plan, held = support.plan(config, args.output, goals=task, policy=LLMPolicy(max_turns=20,max_action_calls=2),setup=setup,workspace=True,
                              extra_plugins=(case.discovery_catalog_plugin(),))
    started = time.perf_counter()
    try:
        await run_plan(args.output, plan)
    finally:
        if args.output.exists():
            save(args.output/'v06-cost.json', {'wall_seconds': time.perf_counter()-started, 'sdk_intervals': held['intervals']})
    with StageReader(args.output) as reader:
        actual = reader.read(lambda v: {'prices': [dict(zip(('id','amount'),r)) for r in v.query('SELECT id,amount FROM prices ORDER BY id')],
                                       'actor': [list(r) for r in v.query('SELECT id,task FROM catalog_actors')],
                                       'report': v.query('SELECT body FROM reports WHERE id=1')[0][0]})
        assert all(actual[k] == fixture[k] for k in actual)
        rows = reader.read(lambda v: v.query('SELECT count,total,phrase FROM submissions'))
        result = oracle(fixture, [dict(zip(('count','total','phrase'), r)) for r in rows])
        threads = ThreadStore(reader)
        messages = threads.read_messages(threads.find('a',{'time':1,'phase':'decision'}))
        original_check=structured_originals(fixture,messages)
        counts = reader.read(usage.read)
    save(args.output/'v06-result.json', {'oracle': result, 'original_check':original_check,'messages': messages, 'usage': counts,
                                       'outcomes': [dataclasses.asdict(row.result) for row in held['outcomes']]})
    assert all(row.result.status == 'completed' for row in held['outcomes'])
    case.assert_vfs_artifacts(args.output)


async def world(args, fixture, key):
    from society0.core_data import World
    from society0.agent.agent_loop import ActionSet, execute_action_loop
    from openai import AsyncOpenAI
    args.output.mkdir(parents=True, exist_ok=False)
    from importlib.metadata import version
    save(args.output/'v06-contract.json',{'source':'96b1f3b','source_directory':str(args.source.resolve()),
        'fixture':str(args.fixture.resolve()),'source_new_run':fixture['new_run'],
        'provider':{'model':MODEL,'base_url':args.url,'timeout':60,'max_retries':0,'options':OPTIONS},
        'budget':{'max_turns':20,'max_action_calls':2,'max_request_messages':None},
        'dependencies':{'openai':version('openai')},'input':'完整同源材料直接输入','credential':'hidden TTY input'})
    current = World(step=1, event_log_path=str(args.output/'events.jsonl'))
    current.environment_data['state'] = {'submissions': []}
    actions = ActionSet()
    def submit(count:int,total:int,phrase:str):
        value = {'count':count,'total':total,'phrase':phrase}
        current.environment_data['state']['submissions'].append(value)
        return value
    actions.add_action('catalog_submit', submit, '提交本主体核对的完整报价结果',
        {'type':'object','properties':{'count':{'type':'integer'},'total':{'type':'integer'},'phrase':{'type':'string'}},
         'required':['count','total','phrase'],'additionalProperties':False},tags=['submission'])
    records=[];messages=[]
    async with AsyncOpenAI(api_key=key,base_url=args.url,max_retries=0,timeout=60) as client:
        async def provider(payload):
            started=time.perf_counter()
            record={'messages':payload['messages'],'tools':payload['tools']}
            records.append(record)
            try:
                if args.dry_run:
                    original=json.dumps({k:fixture[k] for k in ('prices','actor','report')},ensure_ascii=False)
                    assert any(original in (m.get('content') or '') for m in payload['messages'])
                    expected={'count':12,'total':546,'phrase':fixture['report'].split('\n核对短语：')[1]}
                    return {'role':'assistant','content':'','tool_calls':[{'id':'dry-submit','type':'function','function':{'name':'catalog_submit','arguments':json.dumps(expected,ensure_ascii=False)}}]}
                response=await client.chat.completions.create(model=MODEL,messages=payload['messages'],
                    tools=payload['tools'],tool_choice='auto',**OPTIONS)
                record['response']=response.model_dump(mode='json')
                return response.choices[0].message.model_dump(mode='json',exclude_none=True)
            finally:
                record['wall_seconds']=time.perf_counter()-started
                save(args.output/'v06-requests.json',records)
        try:
            result=await execute_action_loop(instruction='核对全部原始资料，复算报价记录数与金额合计，完整读取报告的核对短语，'
                '通过 catalog_submit 提交 count、total、phrase。所有数字来自完整资料。\n完整原文资料：'+json.dumps(
                    {k:fixture[k] for k in ('prices','actor','report')},ensure_ascii=False),
                action_set=actions,system_prompt='你是主体 a。认真遵照本轮任务，保留事实原文。',stages=['act'],
                llm_call=provider,max_turns=20,max_action_calls=2,completion_action_tags=['submission'],
                llm_request_options={'provider_request_retry_max':0},max_request_messages=None,
                thread_message_recorder=lambda m:messages.append(m))
        finally:
            save(args.output/'v06-messages.json',messages)
            save(args.output/'v06-state.json',current.environment_data['state'])
    expected=oracle(fixture,current.environment_data['state']['submissions'])
    save(args.output/'v06-result.json',{'oracle':expected,'loop_result':dataclasses.asdict(result)})
    assert result.status=='success'


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('mode',choices=['export','structured','world'])
    parser.add_argument('--source',type=Path)
    parser.add_argument('--fixture',type=Path,required=True)
    parser.add_argument('--output',type=Path)
    parser.add_argument('--new-run',type=Path)
    parser.add_argument('--url',default='https://api.siliconflow.cn/v1')
    parser.add_argument('--dry-run',action='store_true')
    args=parser.parse_args()
    if args.mode=='export':export(args)
    else:
        sys.path.insert(0,str(args.source/'src'))
        sys.path.insert(0,str(args.source))
        fixture=json.loads(args.fixture.read_text())
        key='offline-unused' if args.dry_run else getpass.getpass('Credential: ')
        assert key,'stdin credential required'
        asyncio.run((structured if args.mode=='structured' else world)(args,fixture,key))
