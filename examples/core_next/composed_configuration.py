"""明确配置边界：公开配方、两类 DAG 与安装后的来源消费。"""
import json
import sys
from pathlib import Path
from society0.kernel.actors import ActorRecord, ActorDirectory, actor_data_plugin
from society0.kernel.plugins import Plugin
from society0.kernel.runner import RunPlan, RunContract
from society0.kernel.runtime import DriverResult, Phase
from society0.kernel.schedule import SequenceSchedule, schedule_plugin
from society0.kernel.storage import StageReader
from society0.presets import rule_preset, llm_preset

SCHEMA=('CREATE TABLE configuration_observations(id INTEGER PRIMARY KEY,step INTEGER,kind TEXT NOT NULL,value TEXT NOT NULL)',)
ERRORS={
    'duplicate-source':('ValueError','duplicate cognition source id'),
    'empty-source-id':('ValueError','cognition source id must be a nonempty string'),
    'bad-service':('KeyError',"'missing'"),
    'bad-position':('TypeError','cognition position must be a source-id mapping'),
    'missing-provider':('ValueError','LLM preset requires provider and input_builder references'),
    'missing-builder':('ValueError','LLM preset requires provider and input_builder references'),
    'duplicate-plugin':('ValueError','duplicate plugin: repeated'),
    'service-cycle':('ValueError','plugin requires dependency cycle'),
    'schema-cycle':('ValueError','plugin schema_requires dependency cycle'),
    'records-and-data':('ValueError','records and data are mutually exclusive'),
}
POSITIVE=('legal-two-graphs','external-directory','base-only')


def save(store,kind,value,step=None):
    store.transaction(lambda writer:writer.execute('INSERT INTO configuration_observations(step,kind,value) VALUES(?,?,?)',
        (step,kind,json.dumps(value,ensure_ascii=False))))


def build(config):
    variant=config['variant']
    if config['case']!='configuration' or variant not in (*ERRORS,*POSITIVE):
        raise ValueError('unsupported configuration scenario')
    if not config['release']['commit'] or config.get('provider')!='scripted':
        raise ValueError('configuration scenario requires source identity and scripted provider')
    config={**config,'start':config.get('start',1),'end':config.get('end',2),'models':{}}
    if config['start']!=1 or config['end'] not in (1,2):
        raise ValueError('configuration scenario requires start=1 and end=1 or 2')
    if variant in ('duplicate-source','empty-source-id'):
        from society0.plugins.cognition import SourceSpec,cognition_plugin
        ids=('same','same') if variant=='duplicate-source' else ('',)
        cognition_plugin(sources=tuple(SourceSpec(identifier,('source','read')) for identifier in ids))
    def activate(context): context.activate('a')
    schedule=schedule_plugin(SequenceSchedule(range(1,config['end']+1),[Phase('configuration',activate)]))
    roots=[Plugin('configuration.observations',schema=SCHEMA)]
    state={}
    def driver(ctx):
        store=ctx.require('storage','store')
        async def run(session):
            save(store,'entered',session.actor.id,session.step)
            if variant=='bad-position':
                from society0.plugins.cognition import CompositePerception,SourceBatch
                await CompositePerception((('source',lambda current,previous:SourceBatch([],None)),))(session,['invalid-old-position'])
            forbidden=[name for name in ('pydantic_ai','chromadb','openai') if name in sys.modules]
            save(store,'completed',{'actor':session.actor.id,'persona':ActorDirectory(store).get_record(session.actor.id).persona,'optional':forbidden},session.step)
            return DriverResult('completed',session.actor.id)
        state['run']=run
        ctx.provide('run',run)
    roots.append(Plugin('configuration.driver',('storage',),driver))
    async def run(session): return await state['run'](session)
    # 自有配置消费者在全部服务安装后进入实际规则回合。
    from society0.kernel.drivers import rule_driver_plugin
    configured=rule_preset(records=(ActorRecord('a','rule',persona='configured-persona'),),run=run,
        allows=lambda *args:True,schedule=schedule,plugins=roots)
    configured=tuple(rule_driver_plugin(run,requires=('configuration.driver',)) if plugin.name=='rule_driver' else plugin for plugin in configured)
    if variant in ('missing-provider','missing-builder'):
        configured=llm_preset(records=(ActorRecord('a','llm'),),
            provider=None if variant=='missing-provider' else ('provider','model'),
            input_builder=None if variant=='missing-builder' else ('cognition','input_builder'),
            allows=lambda *args:True,schedule=schedule)
    elif variant=='bad-service':
        from society0.plugins.cognition import SourceSpec,cognition_plugin
        configured=(*configured,Plugin('source',install=lambda ctx:ctx.provide('read',lambda *args:None)),
            Plugin('threads',install=lambda ctx:ctx.provide('threads',object())),
            cognition_plugin(sources=(SourceSpec('source',('source','missing')),)))
    elif variant=='duplicate-plugin': configured=(*configured,Plugin('repeated'),Plugin('repeated'))
    elif variant=='service-cycle': configured=(*configured,Plugin('left',requires=('right',)),Plugin('right',requires=('left',)))
    elif variant=='schema-cycle': configured=(*configured,Plugin('left',schema_requires=('right',)),Plugin('right',schema_requires=('left',)))
    elif variant=='records-and-data':
        rule_preset(records=(),data=('external','directory'),run=run,allows=lambda *args:True,schedule=schedule)
    elif variant=='external-directory':
        data=actor_data_plugin(name='external',records=(ActorRecord('a','rule',persona='external-persona'),))
        configured=tuple(plugin for plugin in configured if plugin.name not in ('actors','actors.data'))
        from society0.kernel.actors import actor_plugin
        configured=(*configured,data,actor_plugin({'rule':('rule_driver','factory')},data=('external','directory')))
    elif variant=='legal-two-graphs':
        def left_initialize(writer): writer.execute("INSERT INTO configuration_observations(kind,value) VALUES('schema-left','true')")
        def right_initialize(writer): writer.execute("INSERT INTO configuration_observations(kind,value) VALUES('schema-right','true')")
        def left(ctx):
            store=ctx.require('storage','store')
            save(store,'service-left',ctx.require('right','value'))
        def right(ctx):
            save(ctx.require('storage','store'),'service-right',True)
            ctx.provide('value','right-ready')
        configured=(*configured,Plugin('left',requires=('storage','right'),install=left,initialize=left_initialize,schema_requires=('configuration.observations',)),
            Plugin('right',requires=('storage',),install=right,initialize=right_initialize,schema_requires=('left',)))
    contract=RunContract(release=config['release'],dependencies={'python':sys.version},configuration=config,
        time={'start':1,'end':config['end']},budgets={})
    return RunPlan(configured,contract)


def check(identifier,expected,actual,location):
    return {'id':identifier,'passed':expected==actual,'expected':expected,'actual':actual,'location':location}


def facts(path):
    manifest=json.loads((Path(path)/'runner.json').read_text(encoding='utf-8'))
    with StageReader(path) as reader:
        rows=reader.read(lambda view:view.query('SELECT step,kind,value FROM configuration_observations ORDER BY id'))
        data={'complete':reader.read(lambda view:view.complete_step),'id':reader.read(lambda view:view.run_id),
              'rows':[(step,kind,json.loads(value)) for step,kind,value in rows]}
    return manifest,data


def validate(path):
    manifest,data=facts(path)
    config=manifest['contract']['configuration']
    variant=config['variant']
    checks=[check('complete',config['end'],data['complete'],'stage_meta/complete_step'),
        check('no-model-configuration',{},manifest['contract']['configuration']['models'],'runner.json/contract/configuration/models')]
    completed=[(step,value) for step,kind,value in data['rows'] if kind=='completed']
    checks.append(check('real-activations',list(range(1,config['end']+1)),[step for step,value in completed],'configuration_observations/completed'))
    persona='external-persona' if variant=='external-directory' else 'configured-persona'
    checks.append(check('actual-directory',[(step,{'actor':'a','persona':persona,'optional':[]}) for step in range(1,config['end']+1)],completed,'configuration_observations/completed/value'))
    if variant=='legal-two-graphs':
        checks.append(check('actual-independent-order',['schema-left','schema-right','service-right','service-left'],
            [kind for step,kind,value in data['rows'] if kind.startswith(('schema-','service-'))][:4],'configuration_observations/id=1..4'))
    if variant=='external-directory':
        checks.append(check('single-explicit-data-install',1,sum(plugin['name']=='external' for plugin in manifest['plugins']),'runner.json/plugins'))
        checks.append(check('no-implicit-data',False,any(plugin['name']=='actors.data' for plugin in manifest['plugins']),'runner.json/plugins'))
    return checks


def compare(continuous,restored):
    left,a=facts(continuous);right,b=facts(restored)
    return [check('business-activations',[row for row in a['rows'] if row[1] in ('entered','completed')],
        [row for row in b['rows'] if row[1] in ('entered','completed')],'configuration_observations/step,kind,value'),
        check('restore-source-step',1,right['source']['step'],'runner.json/source/step'),
        check('restore-source-directory',str(Path(continuous).resolve()),str(Path(right['source']['path']).resolve()),'runner.json/source/path'),
        check('new-run-identity',True,a['id']!=b['id'],'stage_meta/run_id')]


def validate_negative(config_path,log,exit_code,run=None):
    config=json.loads(Path(config_path).read_text(encoding='utf-8'))
    variant=config['variant'];kind,message=ERRORS[variant]
    text=Path(log).read_text(encoding='utf-8')
    checks=[check('exact-failed-process',1,exit_code,str(log)),check('specific-exception',True,kind+': '+message in text,str(log))]
    io_variant=variant in ('bad-service','bad-position')
    checks.append(check('specified-io-boundary',io_variant,run is not None and Path(run).exists(),str(run)))
    run_id=None
    if io_variant and run is not None and Path(run).exists():
        with StageReader(run) as reader:
            run_id=reader.read(lambda view:view.run_id)
            complete=reader.read(lambda view:view.complete_step)
            completed=reader.read(lambda view:view.query("SELECT count(*) FROM configuration_observations WHERE kind='completed'")[0][0])
        checks.extend((check('no-completed-business-step',True,complete<1,'stage_meta/complete_step'),
                       check('no-successful-activation',0,completed,'configuration_observations/completed')))
    return {'format':1,'case':'configuration','variant':variant,'source_commit':config['release']['commit'],
        'run_id':run_id,'checks':checks,'first_difference':next((item for item in checks if not item['passed']),None)}
