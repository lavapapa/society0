"""主体按字段读取的非作者快照与活动键复杂度验收。"""
import pytest
from society0.kernel.actors import ActorRecord,actor_plugin
from society0.kernel.composition import compose
from society0.kernel.storage import ReadView


@pytest.mark.asyncio
async def test_review_actor_revision_check_and_field_read_share_snapshot(tmp_path,monkeypatch):
    plugins=[actor_plugin({'rule':lambda record:None},records=[ActorRecord('a','rule',persona='old')])]
    async with compose(tmp_path/'run',plugins) as host:
        actors=host.service('actors','actors');record=actors.view('a')
        original=ReadView.query;changed=False
        def query(view,sql,*args,**kwargs):
            nonlocal changed
            result=original(view,sql,*args,**kwargs)
            if sql.startswith('SELECT data_revision') and not changed:
                changed=True
                actors.update('a',persona='new')
            return result
        monkeypatch.setattr(ReadView,'query',query)
        assert record.persona=='old'
        with pytest.raises(ValueError,match='revision'):record.persona
        assert actors.view('a').persona=='new'


@pytest.mark.asyncio
async def test_review_selected_state_keys_do_not_scan_unrelated_cold_fields(tmp_path):
    costs=[]
    for count in (100,10000):
        state={f'field-{i}':i for i in range(count)}
        plugins=[actor_plugin({'rule':lambda record:None},records=[ActorRecord('a','rule',state=state)])]
        async with compose(tmp_path/str(count),plugins) as host:
            actors=host.service('actors','actors');store=host.service('storage','store')
            record=actors.view('a');original=store.read;vm=[0]
            def step():vm[0]+=1;return False
            def counted(callback,**kwargs):
                def run(view):
                    view._connection.set_progress_handler(step,1)
                    return callback(view)
                return original(run,**kwargs)
            store.read=counted
            assert record.state_values(('field-0',f'field-{count-1}'))=={'field-0':0,f'field-{count-1}':count-1}
            costs.append(vm[0])
    assert costs[1] <= costs[0]*3, costs
