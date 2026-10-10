"""主体使用安装期依赖构造驱动工厂，权威目录仍由同一 Actor 插件拥有。"""
import pytest
from society0.kernel.actors import ActorRecord,actor_plugin
from society0.kernel.composition import compose
from society0.kernel.plugins import Plugin


@pytest.mark.asyncio
async def test_actor_factory_resolves_declared_resources_once_and_builds_lazily(tmp_path):
    installed=[];created=[];resource=object()
    def build(ctx):
        assert ctx.require('provider','service') is resource
        installed.append(ctx.name if hasattr(ctx,'name') else 'actors')
        def driver(record):
            created.append(record.id)
            return resource
        return {'llm':driver}
    def plugins():
        return [actor_plugin(('llm',),records=[ActorRecord('a','llm'),ActorRecord('b','llm')],
            requires=('provider',),driver_factory=build),
            Plugin('provider',install=lambda ctx:ctx.provide('service',resource))]
    async with compose(tmp_path/'run',plugins()) as host:
        actors=host.service('actors','actors')
        assert created==[] and len(installed)==1
        assert actors['a'].driver is resource and created==['a']
        host.service('storage','store').complete(1)
    async with compose(tmp_path/'restore',plugins(),source=tmp_path/'run') as host:
        assert host.service('actors','actors')['b'].driver is resource
    assert len(installed)==2 and created==['a','b']


@pytest.mark.asyncio
async def test_actor_factory_requires_exact_declared_names(tmp_path):
    plugin=actor_plugin(('llm',),records=[ActorRecord('a','llm')],driver_factory=lambda ctx:{'other':lambda r:None})
    with pytest.raises(ValueError,match='driver names'):
        async with compose(tmp_path/'bad',[plugin]):pass


@pytest.mark.asyncio
async def test_workspace_requires_actor_schema_before_creating_run(tmp_path):
    from society0.kernel.workspace import workspace_plugin
    with pytest.raises(ValueError,match='actors.data'):
        async with compose(tmp_path/'missing',[workspace_plugin()]):pass
    assert not (tmp_path/'missing').exists()
