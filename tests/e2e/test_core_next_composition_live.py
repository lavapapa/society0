"""获授权的真实组合：完整 typed/SDK 输入、结构化工具和新进程恢复。"""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
from society0.kernel.storage import StageReader
from society0.kernel.threads import ThreadStore
from .core_next_composition_assertions import contains_original, texts
from examples.core_next.composed_cognition import original, SOCIAL_ID, NOTICE_ID
from .core_next_real_support import configuration

pytestmark=[pytest.mark.e2e,pytest.mark.real_e2e,
    pytest.mark.skipif(os.environ.get('SOCIETY0_RUN_CORE_REAL')!='1',reason='需要显式授权真实端点运行')]


def launch(root,name,variant,*,end=1,source=None,fault='none'):
    actual=configuration(embedding=False)
    run=root/name
    frozen=root/(name+'.json')
    frozen.write_text(json.dumps({'release':{'commit':actual['release']},'case':'multi-source','provider':'scripted',
        'variant':variant,'end':end,'fault':fault,'fault_step':2 if fault!='none' else None}),encoding='utf-8')
    args=[sys.executable,'-m','society0.kernel.runner','--factory','tests.e2e.core_next_composition_live:build',
          '--config',str(frozen),'--output',str(run)]
    if source is not None:args+=['--source',str(source),'--step','1']
    result=subprocess.run(args,capture_output=True,text=True,env=os.environ)
    (root/(name+'.log')).write_text(result.stdout+'\n'+result.stderr,encoding='utf-8')
    return run,result


def assert_live(run,end,variant):
    with StageReader(run) as reader:
        requests=reader.read(lambda view:view.query('SELECT thread_id,request_seq,typed FROM composition_requests ORDER BY id'))
        outcomes=reader.read(lambda view:[json.loads(row[0]) for row in view.query('SELECT result FROM composition_outcomes ORDER BY id')])
        sdk=reader.read(lambda view:[json.loads(row[0]) for row in view.query('SELECT sdk FROM composition_sdk ORDER BY id')])
        assert reader.read(lambda view:view.complete_step)==end
        assert len(outcomes)==end*2 and all(row['status']=='completed' for row in outcomes),outcomes
        assert len(requests)==len(sdk) and len(requests)>=end*2
        threads=ThreadStore(reader)
        tid=requests[0][0]
        cursor=threads.input_cursor(tid,'operating_context')['position']
        assert cursor[NOTICE_ID]==end
        seeded=reader.read(lambda view:view.query('SELECT id FROM social_posts WHERE author=? ORDER BY ordinal',('b',)))
        assert cursor[SOCIAL_ID]['seen']==[row[0] for row in seeded]
        last=[part['content'] for message in json.loads(requests[-1][2]) for part in message['parts'] if part['part_kind']=='user-prompt']
        bodies=[original('social',1,1),original('social',1,2),*(original('social',number,2) for number in range(2,end+1)),
                *(original('notice',number) for number in range(1,end+1))]
        assert all(sum(contains_original(body,text) for text in last)==1 for body in bodies)
        manifest=json.loads((run/'runner.json').read_text(encoding='utf-8'))
        provider=manifest['contract']['configuration']['provider_profile']['endpoints'][0]['provider_type']
        schemas=[[tool if provider in ('openai-codex','openai-responses','siwc') else tool['function'] for tool in row['tools']] for row in sdk]
        for row,tools in zip(sdk,schemas):
            assert row['tool_choice']=='auto' and row['parallel_tool_calls'] is False
            assert row['stream'] is True
            assert tools and all(tool['parameters'] for tool in tools)
        if variant=='interview':
            assert all(isinstance(row['value']['result']['measurement'],str) and row['value']['result']['measurement'] for row in outcomes)
        else:
            assert all(row['value']['action_counts'].get('social.publish_post',0)>=1 for row in outcomes)
            assert all([tool['name'] for tool in tools]==['action_find','action_describe','action_invoke','ls','read','find','grep'] for tools in schemas)
        return tid


def test_live_composed_interview_and_new_process_restore():
    actual=configuration(embedding=False)
    root=actual['output']/'composed-interview'
    root.mkdir(parents=True,exist_ok=False)
    failed,result=launch(root,'failed','interview',end=2,fault='after-callback-partial-write')
    assert result.returncode==1,result.stdout+result.stderr
    assert json.loads((failed/'runner-status.json').read_text(encoding='utf-8'))['error']=='CompositionFault'
    with StageReader(failed) as reader:
        assert reader.read(lambda view:view.complete_step)==1
        failure_tid=reader.read(lambda view:view.query('SELECT thread_id FROM composition_requests LIMIT 1'))[0][0]
        assert reader.read(lambda view:view.query('SELECT notice_id FROM "notice.data_receipts" WHERE actor=? ORDER BY notice_id',('a',)))==[(1,),(2,)]
    restored,result=launch(root,'restored','interview',end=2,source=failed)
    assert result.returncode==0,result.stdout+result.stderr
    assert assert_live(restored,2,'interview')==failure_tid


def test_live_composed_autonomous_domain_tools():
    actual=configuration(embedding=False)
    root=actual['output']/'composed-actions'
    root.mkdir(parents=True,exist_ok=False)
    run,result=launch(root,'run','default')
    assert result.returncode==0,result.stdout+result.stderr
    assert_live(run,1,'default')
