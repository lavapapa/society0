"""真实 SDK 流式发送边界：实际入参、水位、原文和独立进程恢复。"""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from society0.kernel.storage import StageReader
from society0.kernel.threads import ThreadStore
from .test_core_next_composition_entrypoint import config, verify


def execute(tmp_path, name, configuration, *, source=None):
    root=tmp_path/name
    settings=tmp_path/(name+'.json')
    settings.write_text(json.dumps(configuration,ensure_ascii=False),encoding='utf-8')
    args=[sys.executable,'-m','society0.kernel.runner','--factory','tests.e2e.core_next_composition_sdk:build',
          '--config',str(settings),'--output',str(root)]
    if source is not None: args+=['--source',str(source),'--step','1']
    result=subprocess.run(args,capture_output=True,text=True,env=os.environ)
    (tmp_path/(name+'.log')).write_text(result.stdout+'\n'+result.stderr,encoding='utf-8')
    return root,result


@pytest.mark.parametrize('variant',['default','interview'])
def test_actual_sdk_and_http_payload_preserve_typed_sources_and_schema(tmp_path,variant):
    task='任务原文🙂：依据完整材料执行本轮明确任务。'
    root,result=execute(tmp_path,'run',config(variant=variant,task=task))
    assert result.returncode==0,result.stdout+'\n'+result.stderr
    verify(root)
    with StageReader(root) as reader:
        rows=reader.read(lambda view:view.query('SELECT thread_id,request_seq,physical_id,sdk,wire FROM composition_sdk ORDER BY id'))
        assert len(rows)==4
        for thread,sequence,physical,sdk,wire in rows:
            sdk,wire=json.loads(sdk),json.loads(wire)
            assert sdk['messages']==wire['messages']
            assert sdk['tools']==wire['tools']
            assert sdk['parallel_tool_calls'] is False
            assert sdk['tool_choice']==wire['tool_choice']=='auto'
            assert sdk['stream'] is True and wire['stream'] is True
            assert wire['chat_template_kwargs']=={'enable_thinking':False}
            assert wire['temperature']==0
            assert ThreadStore(reader).read_request(thread,sequence)['physical_request_id']==physical
            names=[tool['function']['name'] for tool in wire['tools']]
            assert names==(['submit_result'] if variant=='interview' else ['action_find','action_describe','action_invoke','ls','read','find','grep'])
            assert all(tool['function']['parameters'] for tool in wire['tools'])
            serialized=json.dumps(wire,ensure_ascii=False)
            assert 'sdk-fixture-key' not in serialized
            assert task in serialized


def test_actual_sdk_requests_match_after_new_process_complete_point_restore(tmp_path):
    continuous,first=execute(tmp_path,'continuous',config())
    assert first.returncode==0,first.stdout+first.stderr
    failed,second=execute(tmp_path,'failed',config(fault='after-callback-partial-write',fault_step=2))
    assert second.returncode==1,second.stdout+second.stderr
    restored,third=execute(tmp_path,'restored',config(),source=failed)
    assert third.returncode==0,third.stdout+third.stderr
    verify(restored,compare=continuous,failed=failed)
    with StageReader(continuous) as one,StageReader(restored) as two:
        first_wire=one.read(lambda view:[json.loads(row[0]) for row in view.query('SELECT wire FROM composition_sdk ORDER BY id')])
        restored_wire=two.read(lambda view:[json.loads(row[0]) for row in view.query('SELECT wire FROM composition_sdk ORDER BY id')])
        assert first_wire==restored_wire
