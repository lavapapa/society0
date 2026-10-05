"""空白基准机制通过正式组合入口创建与恢复。"""
import pytest
from society0.kernel.composition import compose
from society0.plugins.plain import plain_plugin


@pytest.mark.asyncio
async def test_plain_plugin_has_no_domain_state_and_restores(tmp_path):
    plugin=plain_plugin()
    assert plugin.schema==() and plugin.initialize is None
    async with compose(tmp_path/'run',[plugin]) as host:
        store=host.service('storage','store')
        store.complete(1)
    async with compose(tmp_path/'restored',[plugin],source=tmp_path/'run') as host:
        assert host.service('storage','store').complete_step==1
