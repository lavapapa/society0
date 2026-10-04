"""共享主体在两个真实轮次机制中运行，恢复只重算所选点之后。"""
import pytest
from examples.core_next.conversation_pilot import build
from society0.kernel.runner import run_plan
from society0.kernel.storage import StageReader
from society0.kernel.results import Results


@pytest.mark.asyncio
async def test_two_mechanism_pilot_and_complete_branch(tmp_path):
    config={'release':{'commit':'explicit-test-release'},'start':1,'end':2}
    result=await run_plan(tmp_path/'run',build(config))
    assert result['complete_step']==2
    with StageReader(tmp_path/'run') as reader:
        for name in ('work','commons'):
            rows=reader.read(lambda r:r.query(f'SELECT sender,receiver,round,body FROM {name}_messages ORDER BY id'))
            assert len(rows)==8
            assert rows[0][3].decode()==f'a 在 {name} 的第 1 轮完整消息'
        data=Results(reader).phase(2,1)
        assert Results(reader).page(data['tables']['messages'])['total']==8
    branch=await run_plan(tmp_path/'branch',build({**config,'start':1,'end':3}),source=tmp_path/'run',step=1)
    assert branch['complete_step']==3 and branch['run_id']!=result['run_id']
    with StageReader(tmp_path/'branch') as reader:
        assert reader.read(lambda r:r.query('SELECT count(*) FROM work_messages')[0][0])==12
