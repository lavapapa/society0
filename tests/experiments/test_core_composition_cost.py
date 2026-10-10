"""分项探针的小规模合同；大历史结果由冻结后的独立命令产生。"""
import pytest
from benchmarks.core_composition_cost import probe, compare


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['rr', 'social', 'social_embedding'])
async def test_small_probe_preserves_restore_and_measures(tmp_path, mode):
    result = await probe(tmp_path / mode, mode=mode, history=4)
    assert result['restored_equal']
    assert result['phases']['hot']['sql_vm_steps'] > 0
    assert result['phases']['cold_small_action']['body_materialized_bytes'] == 0
    assert result['phases']['full_original']['body_materialized_bytes'] >= result['body_bytes']
    assert result['phases']['complete']['disk_logical_delta'] > 0
    assert compare(result, result)['semantic_equal']
    changed = dict(result, semantic={'damaged': True})
    with pytest.raises(AssertionError):
        compare(result, changed)
