"""V03 比较器用完整运行证据校验，并证明会拒绝正文／顺序损坏。"""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import pytest

ROOT=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location('core_parity',ROOT/'benchmarks/core_next_parity.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)

def outputs():
    root=ROOT/'research/core-next/parity-20261004'
    return {f'{v}-{m}-{r}':json.loads((root/f'{v}-{m}-{r}.json').read_text())
            for v in ('old','new') for m in ('rule','llm') for r in ('continuous','restored')}

def test_actual_old_new_continuous_restored_original_evidence():
    assert len(module.compare(outputs()))==8

@pytest.mark.parametrize('damage',['order','body','provider','memory'])
def test_comparison_rejects_lost_original_material(damage):
    data=outputs();item=data['new-llm-restored']
    if damage=='order':item['snapshots'][-1]['state']['facts'].reverse()
    elif damage=='body':next(iter(item['threads'].values()))[-1]['content']='truncated'
    elif damage=='provider':item['provider_inputs'][0][1]['content']='PARITY:{}'
    else:item['memories'][0]['embedding'][0]=0
    with pytest.raises(AssertionError):module.compare(data)
