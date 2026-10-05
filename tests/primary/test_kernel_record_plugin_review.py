"""独立消费者：追加键身份与非法值写入不污染已完成事实。"""
import math
import pytest
from examples.core_next.typed_records import record_plugin
from society0.kernel.composition import compose
from society0.kernel.interaction import interaction_plugin


@pytest.mark.asyncio
async def test_review_duplicate_fact_keeps_original_and_failed_value_keeps_revision(tmp_path):
    plugins=[interaction_plugin(lambda *a:True),record_plugin(owner='a')]
    async with compose(tmp_path/'run',plugins) as host:
        records=host.service('records','records');store=host.service('storage','store')
        values=[(-(2**90),{'order':[None,False,0,-0.0]}),(str(-(2**90)),{'text':'原文🙂'})]
        for key,value in values:assert records.append(key,value)
        store.complete(1)
        before=store.read(lambda r:r.live_revision)
        cyclic=[];cyclic.append(cyclic)
        for value in [{'x':float('nan')},{'x':float('inf')},cyclic]:
            with pytest.raises((ValueError,RecursionError)):records.append('invalid',value)
            assert store.read(lambda r:r.live_revision)==before
        assert not records.append(values[0][0],{'replacement':True})
        assert records.facts()==values
    async with compose(tmp_path/'copy',plugins,source=tmp_path/'run',step=1) as host:
        actual=host.service('records','records').facts()
        assert actual==values
        assert math.copysign(1,actual[0][1]['order'][-1])==-1
        assert host.service('records','records').append('next',{'after':True})
        assert host.service('records','records').facts()[-1]==('next',{'after':True})
