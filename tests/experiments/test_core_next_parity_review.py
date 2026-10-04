"""非作者验证持续／恢复对照保留所有原始会话字段。"""
import pytest
from tests.experiments.test_core_next_parity import outputs, module


@pytest.mark.parametrize('version',['old','new'])
@pytest.mark.parametrize('damage',['system','assistant','tool_amount','provider_system'])
def test_review_route_comparison_rejects_non_note_message_corruption(version,damage):
    data=outputs();item=data[f'{version}-llm-restored']
    histories=[row['messages'] for row in item['threads']] if isinstance(item['threads'],list) else list(item['threads'].values())
    history=histories[0]
    if damage=='system':history[0]['content']='changed instructions'
    elif damage=='assistant':next(m for m in history if m['role']=='assistant')['content']='changed reasoning'
    elif damage=='tool_amount':
        tool=next(m for m in history if m['role']=='tool')
        old="'amount': 1" if version=='old' else '"amount": 1'
        assert old in tool['content']
        tool['content']=tool['content'].replace(old,old[:-1]+'999',1)
    else:
        request=item['provider_inputs'][0]
        messages=request['messages'] if isinstance(request,dict) else request
        messages[0]['content']='changed actual provider system'
    with pytest.raises(AssertionError):module.compare(data)
