import pytest
from examples.core_next.typed_records import record_plugin
from society0.kernel.composition import compose
from society0.kernel.interaction import InteractionScope,Moment,Ref,interaction_plugin


@pytest.mark.asyncio
async def test_typed_record_actions_preserve_facts_keys_order_precision_and_restore(tmp_path):
    plugins=[interaction_plugin(lambda *a:True),record_plugin(owner='owner')]
    async with compose(tmp_path/'run',plugins) as host:
        records=host.service('records','records');actions=host.service('interaction','actions')
        scope=InteractionScope('owner',Moment(1,'write'));target=Ref('records','ledger','main')
        values=[(1,{'large':2**80,'unicode':'完整🙂'}),('1',{'n':1.2345678901234567}),('second',{'list':[3,1,2]})]
        for key,value in values:
            assert (await actions.invoke(scope,'records.append',target,{'key':key,'value':value})).status=='completed'
        assert (await actions.invoke(scope,'records.append',target,{'key':1,'value':{'changed':True}})).status=='rejected'
        outsider=InteractionScope('other',Moment(1,'write'))
        assert (await actions.invoke(outsider,'records.append',target,{'key':'forbidden','value':{}})).status=='rejected'
        assert (await actions.invoke(scope,'records.project',target,{'key':1,'value':{'balance':3}})).status=='completed'
        records.project('1',{'balance':7})
        assert records.facts()==values
        host.service('storage','store').complete(1)
    async with compose(tmp_path/'restored',plugins,source=tmp_path/'run') as host:
        restored=host.service('records','records')
        assert restored.facts()==values
        assert restored.projection(1)=={'balance':3} and restored.projection('1')=={'balance':7}


@pytest.mark.asyncio
async def test_record_projection_and_fact_partial_transaction_rolls_back(tmp_path):
    async with compose(tmp_path/'run',[interaction_plugin(lambda *a:True),record_plugin(owner='owner')]) as host:
        store=host.service('storage','store');records=host.service('records','records')
        records.append('original',{'n':1});records.project('current',{'n':2})
        store.complete(1)
        def failing(writer):
            records.append_to(writer,'uncommitted',{'n':3})
            records.project_to(writer,'current',{'n':4})
            raise ValueError('domain consistency failed')
        with pytest.raises(ValueError):store.transaction(failing)
        assert records.facts()==[('original',{'n':1})]
        assert records.projection('current')=={'n':2}
        with pytest.raises(TypeError):records.append(True,{})


@pytest.mark.asyncio
async def test_small_projection_change_does_not_recapture_immutable_fact_body(tmp_path):
    async with compose(tmp_path/'run',[interaction_plugin(lambda *a:True),record_plugin(owner='owner')]) as host:
        store=host.service('storage','store');records=host.service('records','records')
        records.append('cold',{'body':'x'*2_000_000});records.project('hot',{'n':0})
        store.complete(1)
        for n in range(100):records.project('hot',{'n':n})
        assert store._session.memory_used<65536
        store.complete(2)
    async with compose(tmp_path/'restored',[interaction_plugin(lambda *a:True),record_plugin(owner='different config')],source=tmp_path/'run') as host:
        records=host.service('records','records')
        assert records.facts()==[('cold',{'body':'x'*2_000_000})] and records.projection('hot')=={'n':99}
        assert records.owns(InteractionScope('owner',Moment(3,'read')),Ref('records','ledger','main'))


@pytest.mark.asyncio
async def test_non_json_nested_keys_are_rejected_without_silent_coercion(tmp_path):
    async with compose(tmp_path/'run',[interaction_plugin(lambda *a:True),record_plugin(owner='owner')]) as host:
        records=host.service('records','records')
        with pytest.raises(TypeError):records.append('bad',{'nested':{1:'number','1':'string'}})
        assert records.facts()==[]
        assert records.append(2**80,{'ok':True})
        assert records.facts()==[(2**80,{'ok':True})]
