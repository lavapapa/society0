import pytest
from benchmarks.core_next_step_cost import probe


@pytest.mark.asyncio
@pytest.mark.parametrize('mode',['rule','llm','memory'])
async def test_complete_steps_preserve_identical_account_effects(tmp_path,mode):
    result=await probe(tmp_path/'run',mode=mode,steps=2,actors=2)
    assert result['balances']==[('actor-0',2),('actor-1',2)]
    assert result['complete_step']==2 and len(result['steps'])==2
    assert result['provider_calls']==(8 if mode=='memory' else (4 if mode=='llm' else 0))
    assert result['restored_equal']

    if mode=='memory':assert result['memory_count']==4
