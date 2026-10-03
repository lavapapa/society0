"""同一主体的冷字段按需读取，短视图明确版本。"""
import pytest
from society0.kernel.actors import ActorRecord,actor_plugin
from society0.kernel.composition import compose

@pytest.mark.asyncio
async def test_activation_reads_small_head_without_materializing_large_cold_actor_fields(tmp_path):
    big='完整资料🙂'*200000
    async with compose(tmp_path/'run',[actor_plugin({'rule':lambda record:record.id},records=[
        ActorRecord('a','rule',persona=big,state={'balance':7,'archive':big},config={'large':big})])]) as host:
        actors=host.service('actors','actors');store=host.service('storage','store');sql=[]
        original=store.read
        def read(callback,**kwargs):
            def traced(view):
                view._connection.set_exec_trace(lambda cursor,statement,bindings:sql.append(statement) or True)
                return callback(view)
            return original(traced,**kwargs)
        store.read=read
        actor=actors['a']
        assert actor.driver=='a'
        assert not any('actor_personas' in item or 'actor_configs' in item or 'FROM actor_state' in item for item in sql)
        sql.clear()
        assert actor.config.state_values(('balance',))=={'balance':7}
        assert actor.config.persona==big
        assert actor.config.state['archive']==big
        assert actors.get_record('a').config=={'large':big}

@pytest.mark.asyncio
async def test_actor_field_view_is_same_actor_version_bound_and_history_survives_deactivation(tmp_path):
    records=[ActorRecord('a','rule',state={'n':1},roles=('buyer',)),ActorRecord('b','rule')]
    plugins=[actor_plugin({'rule':lambda record:None,'other':lambda record:record.id},records=records)]
    async with compose(tmp_path/'run',plugins) as host:
        actors=host.service('actors','actors');store=host.service('storage','store')
        view=actors['a'].config
        actors.set_state('b','n',8)
        assert view.state_values(('n',))=={'n':1}
        actors.set_state('a','n',2)
        with pytest.raises(ValueError,match='revision'):view.state_values(('n',))
        new=actors['a'].config
        with pytest.raises(Exception):actors.update('a',roles=('x','x'))
        assert new.state=={'n':2}
        actors.update('a',driver='other',roles=('seller',))
        actors.deactivate('a')
        assert actors.select().items==['b']
        assert actors.get_record('a').state=={'n':2}
        assert actors['a'].driver=='a'
        store.complete(1)
    async with compose(tmp_path/'restored',plugins,source=tmp_path/'run') as host:
        record=host.service('actors','actors').get_record('a')
        assert record.active is False and record.driver=='other' and record.roles==('seller',)
        assert record.state=={'n':2}
