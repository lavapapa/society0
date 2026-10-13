"""多来源边界：空位置、真实并发与跨运行 Moment 身份轮换。"""
import asyncio
import json
from dataclasses import replace
from pathlib import Path
from society0.kernel.plugins import Plugin
from society0.kernel.runtime import Phase, runtime_plugin
from society0.kernel.schedule import StepPlan
from society0.kernel.interaction import Moment
from society0.kernel.storage import StageReader
from society0.kernel.threads import ThreadStore
from society0.plugins.cognition import SourceBatch,SourceSpec,cognition_plugin
from .composed_cognition import build as ordinary,encoded,original

IDS=('position.a.v1','position.b.v1')
VALUES=((None,0,{},None),(0,{},None,0))
SCHEMA=('CREATE TABLE source_edge_observations(id INTEGER PRIMARY KEY,step INTEGER NOT NULL,activation INTEGER NOT NULL,actor TEXT NOT NULL,source TEXT NOT NULL,kind TEXT NOT NULL,value BLOB NOT NULL)',)


def build(config):
    variant=config['variant']
    if variant not in ('empty-values','concurrent','moment-switch'):
        raise ValueError('unsupported source edge scenario')
    if variant=='moment-switch':
        base=ordinary({**config,'variant':'default'})
        return replace(base,contract=replace(base.contract,configuration={**base.contract.configuration,'variant':variant}))
    if config.get('sources') is not None and tuple(config['sources'])!=IDS:
        raise ValueError('source edge scenario uses its two fixed source IDs')
    if not config.get('capture',True): raise ValueError('source edge behavior validation requires capture=true')
    # 复用基础资源与完整驱动，专门来源随后替换该配方的认知 binding。
    from .composed_cognition import SOCIAL_ID,NOTICE_ID
    base=ordinary({**config,'variant':'default','sources':[SOCIAL_ID,NOTICE_ID]})
    end=base.contract.configuration['end']
    if end not in (1,2): raise ValueError('source edge scenario requires end=1 or 2')
    gates={}
    def install(ctx):
        store=ctx.require('storage','store');threads=ctx.require('threads','threads')
        def save(session,source,kind,value):
            store.transaction(lambda writer:writer.execute('INSERT INTO source_edge_observations(step,activation,actor,source,kind,value) VALUES(?,?,?,?,?,?)',
                (session.step,session.signals[0]['activation_in_step'],session.actor.id,source,kind,encoded(value))))
        for index,identifier in enumerate(IDS):
            async def read(session,previous,index=index,identifier=identifier):
                activation=session.signals[0]['activation_in_step']
                offset=(session.step-1)*2+activation-1
                value=VALUES[index][offset] if variant=='empty-values' else {'actor':session.actor.id,'ordinal':offset+1,'source':identifier}
                before={'present':previous is not None,'position':None if previous is None else previous.position}
                save(session,identifier,'read',before)
                if variant=='concurrent' and index==0:
                    a_started,b_adopted=gates.setdefault((session.step,activation),(asyncio.Event(),asyncio.Event()))
                    if session.actor.id=='a':
                        a_started.set()
                        await b_adopted.wait()
                    else: await a_started.wait()
                body=original(f'{identifier}/{session.actor.id}',session.step,activation)
                def adopted(context):
                    save(session,identifier,'adopted',{'thread':context.thread_id,'before':before,'position':value,
                        'cursor':threads.input_cursor(context.thread_id,'operating_context'),'body':body})
                    if variant=='concurrent' and index==1 and session.actor.id=='b':
                        gates[(session.step,activation)][1].set()
                return SourceBatch([{'role':'user','content':body}],value,effects=(adopted,))
            ctx.provide(identifier,read)
    def schedule_install(ctx):
        class Schedule:
            async def next_step(self,complete):
                number=complete+1
                if number>end:return None
                async def phase(context):
                    for activation in (1,2):
                        for actor in ('a','b') if variant=='concurrent' else ('a',):
                            context.activate(actor,{'activation_in_step':activation})
                        await context.drain()
                        gates.pop((number,activation),None)
                moment=base.contract.configuration['moment']
                return StepPlan(moment['time'],(Phase(moment['phase'],phase,execution='independent' if variant=='concurrent' else 'serial',capacity=2 if variant=='concurrent' else 1),))
        ctx.provide('schedule',Schedule())
    configured=[]
    for plugin in base.plugins:
        if plugin.name=='cognition':
            plugin=cognition_plugin(sources=tuple(SourceSpec(identifier,('source.edges',identifier)) for identifier in IDS),
                environment='共享完整环境；各主体保留自身完整背景、主观状态与全部已呈现原文。')
        elif plugin.name=='schedule':plugin=Plugin('schedule',install=schedule_install)
        elif plugin.name=='runtime' and variant=='concurrent':
            plugin=runtime_plugin(actor_service=('actors','actors'),information=('interaction','information'),
                actions=('interaction','actions'),store=('storage','store'),results=('results','results'),capacity=2)
        configured.append(plugin)
    configured.append(Plugin('source.edges',('storage','threads'),install,schema=SCHEMA))
    return replace(base,plugins=tuple(configured),contract=replace(base.contract,configuration={**base.contract.configuration,
        'variant':variant,'sources':list(IDS)}))


def check(identifier,expected,actual,location):
    return {'id':identifier,'passed':expected==actual,'expected':expected,'actual':actual,'location':location}


def facts(path):
    manifest=json.loads((Path(path)/'runner.json').read_text(encoding='utf-8'))
    with StageReader(path) as reader:
        requests=reader.read(lambda view:[(step,actor,tid,seq,through,json.loads(typed),json.loads(options)) for step,actor,tid,seq,through,typed,options in view.query('SELECT step,actor,thread_id,request_seq,through,typed,options FROM composition_requests ORDER BY id')])
        threads=ThreadStore(reader)
        heads=threads.list_threads(limit=100)['items']
        for head in heads: head['moment']=json.loads(threads.read_payload(head['id'],1)['data'])['moment']
        cursors={head['id']:threads.input_cursor(head['id'],'operating_context') for head in heads}
        result={'complete':reader.read(lambda view:view.complete_step),'id':reader.read(lambda view:view.run_id),
            'requests':requests,'heads':heads,'cursors':cursors,
            'outcomes':reader.read(lambda view:[json.loads(row[0]) for row in view.query('SELECT result FROM composition_outcomes ORDER BY id')])}
        if manifest['contract']['configuration']['variant']!='moment-switch':
            result['observations']=reader.read(lambda view:[(step,activation,actor,source,kind,json.loads(value)) for step,activation,actor,source,kind,value in view.query('SELECT step,activation,actor,source,kind,value FROM source_edge_observations ORDER BY id')])
        result['request_events']=[threads.read_request(row[2],row[3]) for row in requests]
    return manifest,result


def texts(typed):
    return [part['content'] for message in typed for part in message['parts'] if part['part_kind'] in ('system-prompt','user-prompt','text')]


def validate(path):
    manifest,data=facts(path);config=manifest['contract']['configuration'];variant=config['variant'];end=config['end']
    checks=[check('complete',end,data['complete'],'stage_meta/complete_step')]
    count=end*2*(2 if variant=='concurrent' else 1)
    checks.append(check('request-count',count,len(data['requests']),'composition_requests/id'))
    checks.append(check('completed-activations',['completed']*count,[row['status'] for row in data['outcomes']],'composition_outcomes/result/status'))
    for index,(row,event) in enumerate(zip(data['requests'],data['request_events'])):
        step,actor,tid,seq,through,typed,options=row
        checks.append(check(f'request-{index}-watermark',through,event['through'],f'thread_events/{tid}/{seq}/through'))
        checks.append(check(f'request-{index}-original-tools',['action_find','action_describe','action_invoke','ls','read','find','grep'],
            [tool['function']['name'] for tool in options['tools']],f'composition_requests/{index+1}/options/tools'))
        checks.append(check(f'request-{index}-own-persona',True,any('完整主体背景🙂：共享社会中的独立主体 '+actor in text for text in texts(typed)),f'composition_requests/{index+1}/typed/parts/content'))
    if variant=='moment-switch':
        identities={(head['actor'],head['moment']['time'],head['moment']['phase']):head['id'] for head in data['heads']}
        expected={( 'a',1,'read') } if end==1 else {('a',1,'read'),('a',2,'read')}
        checks.append(check('moment-identities',sorted(expected),sorted(identities),'thread_heads/actor,moment,id'))
        for step,actor,tid,seq,through,typed,options in data['requests']:
            expected_tid=identities[('a',2 if step==2 else 1,'read')]
            checks.append(check(f'moment-step-{step}-identity',expected_tid,tid,f'composition_requests/thread_id/step={step}'))
        if end==3:
            first=[row for row in data['requests'] if row[0]==1];last=[row for row in data['requests'] if row[0]==3]
            checks.append(check('moment-return-retains-thread',first[0][2],last[0][2],'composition_requests/thread_id/steps=1,3'))
            # 同身份的旧原文继续出现在实际请求；中间 Moment 的独立历史不混入。
            from .composed_cognition import SOCIAL_ID,NOTICE_ID
            a_tid=identities[('a',1,'read')];b_tid=identities[('a',2,'read')]
            checks.append(check('moment-return-retains-notice-cursor',3,data['cursors'][a_tid]['position'][NOTICE_ID],f'thread_input_cursors/{a_tid}'))
            checks.append(check('intermediate-moment-cursor-unchanged',2,data['cursors'][b_tid]['position'][NOTICE_ID],f'thread_input_cursors/{b_tid}'))
            checks.append(check('moment-return-original-notice-once',1,sum(original('notice',1) in text for text in texts(last[-1][5])),f'composition_requests/{last[-1][3]}/typed'))
    else:
        actors=('a','b') if variant=='concurrent' else ('a',)
        for actor in actors:
            actor_requests=[row for row in data['requests'] if row[1]==actor]
            tid=actor_requests[-1][2]
            checks.append(check(f'{actor}-single-thread',1,len({row[2] for row in actor_requests}),'composition_requests/actor,thread_id'))
            for source_index,identifier in enumerate(IDS):
                reads=[row for row in data['observations'] if row[2:5]==(actor,identifier,'read')]
                adopted=[row for row in data['observations'] if row[2:5]==(actor,identifier,'adopted')]
                values=[VALUES[source_index][ordinal] if variant=='empty-values' else {'actor':actor,'ordinal':ordinal+1,'source':identifier} for ordinal in range(end*2)]
                expected_before=[{'present':i>0,'position':None if i==0 else values[i-1]} for i in range(end*2)]
                checks.append(check(f'{actor}-{identifier}-prior-presence',expected_before,[row[5] for row in reads],'source_edge_observations/read/value'))
                checks.append(check(f'{actor}-{identifier}-positions',values,[row[5]['position'] for row in adopted],'source_edge_observations/adopted/value/position'))
                checks.append(check(f'{actor}-{identifier}-saved-before-effects',values,[row[5]['cursor']['position'][identifier] for row in adopted],'source_edge_observations/adopted/value/cursor'))
                checks.append(check(f'{actor}-{identifier}-final-position',values[-1],data['cursors'][tid]['position'][identifier],f'thread_input_cursors/{tid}'))
                final=texts(actor_requests[-1][5])
                bodies=[original(f'{identifier}/{actor}',step,activation) for step in range(1,end+1) for activation in (1,2)]
                checks.append(check(f'{actor}-{identifier}-full-history',[1]*len(bodies),[sum(body in text for text in final) for body in bodies],'composition_requests/typed/parts/content'))
                if variant=='concurrent':
                    other='b' if actor=='a' else 'a'
                    checks.append(check(f'{actor}-{identifier}-no-foreign-body',False,any(f'原文开始[{identifier}/{other}/' in text for text in final),'composition_requests/typed/parts/content'))
        if variant=='concurrent':
            order=[(row[0],row[1],row[2],row[4]) for row in data['observations'] if row[3]==IDS[0]]
            expected=[]
            for step in range(1,end+1):
                for activation in (1,2):expected.extend(((step,activation,'a','read'),(step,activation,'b','read'),(step,activation,'b','adopted'),(step,activation,'a','adopted')))
            checks.append(check('actual-event-interleaving',expected,order,'source_edge_observations/id,actor,kind'))
    return checks


def compare(continuous,restored):
    from tests.e2e.core_next_composition_assertions import stable_messages
    left,a=facts(continuous);right,b=facts(restored)
    def projection(data):
        # Thread id 用已经另验的一主体一身份关联，不将其他字段宽泛忽略。
        return [(step,actor,seq,through,stable_messages(typed),options) for step,actor,tid,seq,through,typed,options in sorted(data['requests'],key=lambda row:(row[0],row[1],row[3]))]
    return [check('actual-request-continuity',projection(a),projection(b),'composition_requests/step,actor,request_seq,through,typed,options'),
        check('restore-thread-identities',[(head['actor'],head['id'],head['provider_session_id']) for head in a['heads']],
            [(head['actor'],head['id'],head['provider_session_id']) for head in b['heads']],'thread_heads/actor,id,provider_session_id'),
        check('restore-source-step',1,right['source']['step'],'runner.json/source/step'),
        check('new-run',True,a['id']!=b['id'],'stage_meta/run_id')]
