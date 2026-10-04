"""工作台直接读取完整运行产物，保版本、步骤与主体身份。"""
import json
import subprocess
import sys
from pathlib import Path
import pytest
from society0.kernel.workbench import RunSelection, export_payload
from society0.kernel.runner import run_plan
from examples.core_next.conversation_pilot import build


@pytest.mark.asyncio
async def test_real_pilot_export_and_existing_single_file_renderer(tmp_path):
    await run_plan(tmp_path/'run',build({'release':{'commit':'explicit-test-release'},'start':1,'end':2}))
    payload=export_payload([RunSelection(tmp_path/'run','v1',(1,2),('a','b'))],title='实际对话')
    version=payload['versions'][0];run=version['runs'][0]
    assert version['config']['release']['commit']=='explicit-test-release'
    assert [tick['id'] for tick in run['ticks']]==['1','2']
    snapshots=run['snapshots']
    assert {(s['tickId'],s['entityId']) for s in snapshots}=={(str(t),a) for t in (1,2) for a in ('environment','actor:a','actor:b')}
    original=json.dumps(payload,ensure_ascii=False)
    assert 'sent_to' in original and 'work' in original and 'commons' in original
    data=tmp_path/'data.json';data.write_text(original)
    output=tmp_path/'workbench.html'
    script=Path(__file__).resolve().parents[2]/'skill/scripts/render_workbench.py'
    result=subprocess.run([sys.executable,str(script),'--data',str(data),'--output',str(output)],capture_output=True,text=True)
    assert result.returncode==0,result.stderr
    html=output.read_text();marker='<script id="society0-workbench-data" type="application/json">'
    assert json.loads(html.split(marker,1)[1].split('</script>',1)[0])==payload
    assert '<script src=' not in html


def test_empty_export_has_real_empty_state():
    assert export_payload([],title='空研究')=={'study':{'title':'空研究','question':''},'versions':[]}


@pytest.mark.asyncio
async def test_explicit_versions_do_not_merge_different_configuration(tmp_path):
    for version in ('one','two'):
        await run_plan(tmp_path/version,build({'release':{'commit':version},'start':1,'end':1}))
    selections=[RunSelection(tmp_path/name,name,(1,),('a',)) for name in ('one','two')]
    payload=export_payload(selections)
    assert [v['config']['release']['commit'] for v in payload['versions']]==['one','two']
    with pytest.raises(ValueError,match='configuration'):
        export_payload([RunSelection(item.path,'same',item.steps,item.actors) for item in selections])


@pytest.mark.asyncio
async def test_dataset_and_giant_thread_originals_keep_step_prefix(tmp_path):
    from society0.kernel.plugins import Plugin
    from society0.kernel.services import thread_plugin
    from society0.kernel.datasets import dataset_plugin
    from society0.kernel.results import results_plugin,StepResult,DatasetTable,TableValue
    from society0.kernel.interaction import interaction_plugin
    from society0.kernel.runtime import runtime_plugin,Phase
    from society0.kernel.schedule import CodeSchedule
    from society0.kernel.runner import RunPlan,RunContract
    full='正文🙂</script>\n'*20000
    original={'columns':['body'],'index':[1],'data':[[full]],'index_names':[None],'column_names':['属性']}
    def install(context):
        store=context.require('storage','store');threads=context.require('threads','threads')
        def phase(current):
            number=store.complete_step+1
            tid=threads.find('a',current.moment)
            if tid is None:tid=threads.open('a',current.moment,'decision')
            else:threads.reopen(tid)
            threads.append_message(tid,{'role':'user','content':full if number==1 else '第二步才出现的未来原文'})
            seq=threads.start_action(tid,{'name':'actual','arguments':{}})
            threads.finish_action(tid,seq,{'status':'completed'},status='completed',elapsed_s=.1)
            threads.close(tid,'completed')
            dataset=context.require('datasets','datasets').import_rows('data',[{'body':full,'step':number}])
            return StepResult(tables={'sealed':DatasetTable(dataset),'shape':TableValue(original)})
        context.provide('schedule',CodeSchedule(context.require('runtime','runtime'),[Phase('work',phase)]))
    plugins=[thread_plugin(),dataset_plugin(),interaction_plugin(lambda *a:True),results_plugin(),
        runtime_plugin(information=('interaction','information'),actions=('interaction','actions'),store=('storage','store'),results=('results','results')),
        Plugin('schedule',('storage','threads','runtime','datasets'),install)]
    contract=RunContract({'commit':'explicit-test-release'},{},{'plugins':{},'actors':['a'],'models':{}},{'moments':['same','same']},{})
    await run_plan(tmp_path/'run',RunPlan(plugins,('same','same'),contract))
    payload=export_payload([RunSelection(tmp_path/'run','v1',(1,2),('a',))])
    snapshots=payload['versions'][0]['runs'][0]['snapshots']
    first=next(s for s in snapshots if s['tickId']=='1' and s['entityId']=='actor:a')
    second=next(s for s in snapshots if s['tickId']=='2' and s['entityId']=='actor:a')
    assert any(event['data']=={'role':'user','content':full} for event in first['sessions'][0]['events'])
    assert '第二步才出现的未来原文' not in json.dumps(first,ensure_ascii=False)
    assert '第二步才出现的未来原文' in json.dumps(second,ensure_ascii=False)
    for snapshot in [s for s in snapshots if s['entityId']=='environment']:
        modules=snapshot['tabs'][0]['modules']
        sealed=next(m for m in modules if m['id']=='0:table:sealed')
        assert json.loads(sealed['views'][0]['paragraphs'][0])=={'body':full,'step':int(snapshot['tickId'])}
        shape=next(m for m in modules if m['id']=='0:table:shape')
        assert json.loads(shape['views'][0]['paragraphs'][0])==original
    assert not any(m['id']=='cumulative' for m in first['tabs'][0]['modules'])
    counts=next(m for m in second['tabs'][0]['modules'] if m['id']=='cumulative')
    assert json.loads(counts['views'][0]['paragraphs'][-1])['action_counts']=={'actual':2}


@pytest.mark.asyncio
async def test_cli_exports_selected_actual_run_and_reports_missing_run(tmp_path):
    await run_plan(tmp_path/'run',build({'release':{'commit':'cli-release'},'start':1,'end':1}))
    output=tmp_path/'payload.json'
    base=[sys.executable,'-m','society0.kernel.workbench','--version','cli','--steps','1','--actors','a','--output',str(output)]
    completed=subprocess.run([*base,'--run',str(tmp_path/'run')],capture_output=True,text=True)
    assert completed.returncode==0,completed.stderr
    assert json.loads(output.read_text())==export_payload([RunSelection(tmp_path/'run','cli',(1,),('a',))])
    failed=subprocess.run([*base,'--run',str(tmp_path/'missing')],capture_output=True,text=True)
    assert failed.returncode!=0
    assert json.loads(failed.stderr)['error']['code']=='FileNotFoundError'
    assert 'Traceback' not in failed.stderr


def test_plain_record_ref_shaped_business_value_is_not_interpreted():
    from society0.kernel.workbench import _rows
    value={'kind':'record_ref','business':'原文'}
    class Records:
        def page(self,reference,**kwargs):return {'items':[{'ordinal':0,'raw_bytes':20,'value':value}],'total':1,'next_cursor':None}
        def read_record(self,reference,**kwargs):
            assert reference['id']=='actual-set'
            raw=json.dumps(value).encode()
            return {'data':raw,'total_bytes':len(raw),'next_offset':None}
    assert list(_rows(Records(),{'kind':'result_set','id':'actual-set','origin':'run'}))==[value]


@pytest.mark.asyncio
async def test_entity_identity_separates_environment_and_actor(tmp_path):
    await run_plan(tmp_path/'run',build({'release':{'commit':'test'},'start':1,'end':1}))
    payload=export_payload([RunSelection(tmp_path/'run','v1',(1,),('environment',))])
    entities=payload['versions'][0]['entities']
    assert len({entity['id'] for entity in entities})==2


@pytest.mark.asyncio
async def test_actual_result_rows_and_metrics_have_table_and_chart_views(tmp_path):
    await run_plan(tmp_path/'run',build({'release':{'commit':'test'},'start':1,'end':2}))
    payload=export_payload([RunSelection(tmp_path/'run','v1',(1,2),('a',))])
    snapshots=payload['versions'][0]['runs'][0]['snapshots']
    environment=[s for s in snapshots if s['entityId']=='environment']
    modules=environment[-1]['tabs'][0]['modules']
    table=next(v for m in modules if m['id']=='1:table:messages' for v in m['views'] if v['type']=='table')
    assert len(table['rows'])==8
    assert next(c for c in table['columns'] if c['key']=='actor')['label']=='actor'
    chart=next(v for m in modules for v in m['views'] if v['type']=='timeseries')
    assert chart['rows']==[{'x':'1 · 1','y':4},{'x':'2 · 2','y':4}]
    first=next(v for m in environment[0]['tabs'][0]['modules'] for v in m['views'] if v['type']=='timeseries')
    assert len(first['rows'])==1
