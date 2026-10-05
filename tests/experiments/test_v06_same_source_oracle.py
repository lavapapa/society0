"""同源任务 oracle 必须拒绝误算、原文损失与报价缺页。"""
import importlib.util
import json
from pathlib import Path
from copy import deepcopy
import pytest

ROOT=Path(__file__).resolve().parents[2]
EVIDENCE=ROOT/'research/core-next/completion-audit-20261005'
spec=importlib.util.spec_from_file_location('v06_runner',EVIDENCE/'v06_runner.py')
runner=importlib.util.module_from_spec(spec);spec.loader.exec_module(runner)

def test_shell_recomputation_accepts_autonomous_correction():
    from tests.e2e.test_core_next_real import assert_shell_recomputation
    failed={'stdout':'{"count":12,"total":528}','exit_code':5}
    wrong={'stdout':'{"count":12,"total":528}','exit_code':0}
    correct={'stdout':'{"count":12,"total":546}','exit_code':0}
    assert_shell_recomputation([failed,wrong,correct])

@pytest.mark.parametrize('outputs',[
    [{'stdout':'','exit_code':5}],
    [{'stdout':'{"count":12,"total":528}','exit_code':0}],
    [{'stdout':'{"count":12,"total":546}','exit_code':5}],
])
def test_shell_recomputation_rejects_failed_or_incorrect_result(outputs):
    from tests.e2e.test_core_next_real import assert_shell_recomputation
    with pytest.raises(AssertionError):assert_shell_recomputation(outputs)

def test_oracle_rejects_wrong_sum_and_lost_punctuation():
    fixture=json.loads((EVIDENCE/'v06-fixture.json').read_text())
    expected={'count':12,'total':546,'phrase':'原文校验成功。'}
    assert runner.oracle(fixture,[expected])==expected
    for mutation in ({'total':528},{'total':504},{'phrase':'原文校验成功'},{'count':11}):
        with pytest.raises(AssertionError):runner.oracle(fixture,[{**expected,**mutation}])

def test_original_oracle_rejects_missing_page_and_truncated_body():
    fixture=json.loads((EVIDENCE/'v06-fixture.json').read_text())
    messages=json.loads((EVIDENCE/'v06-oracle-fixture.json').read_text())['messages']
    assert runner.structured_originals(fixture,messages)=={'complete_rows':12,'complete_original_bytes':117}
    query_id=next(c['id'] for m in messages for c in m.get('tool_calls',[])
        if c['function']['name']=='data_query' and json.loads(c['function']['arguments'])['path']=='/catalog/prices')
    missing=[m for m in messages if m.get('tool_call_id')!=query_id]
    with pytest.raises(AssertionError):runner.structured_originals(fixture,missing)
    damaged=deepcopy(messages)
    for message in damaged:
        if message['role']=='tool':
            value=json.loads(message['content'])
            if 'data' in value:
                value['data']=value['data'][:-1];message['content']=json.dumps(value);break
    with pytest.raises(AssertionError):runner.structured_originals(fixture,damaged)
