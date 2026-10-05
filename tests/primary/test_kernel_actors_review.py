"""主体目录的非作者身份与事务边界验收。"""
import json
import pytest
from society0.kernel.actors import ActorRecord, actor_plugin
from society0.kernel.composition import compose


@pytest.mark.asyncio
async def test_review_selector_cursor_is_bound_to_run(tmp_path):
    def plugin():
        return actor_plugin({'rule': lambda r: None}, records=[ActorRecord('a','rule'),ActorRecord('b','rule')])
    async with compose(tmp_path/'first',[plugin()]) as first:
        cursor=json.loads(json.dumps(first.service('actors','actors').select(limit=1).next_cursor))
        async with compose(tmp_path/'second',[plugin()]) as second:
            with pytest.raises(ValueError,match='cursor'):
                second.service('actors','actors').select(limit=1,cursor=cursor)


@pytest.mark.asyncio
async def test_review_duplicate_role_update_rolls_back_all_subjective_changes(tmp_path):
    plugin=actor_plugin({'rule':lambda r:None},records=[ActorRecord('a','rule',state={'old':1},roles=('old',))])
    async with compose(tmp_path/'run',[plugin]) as host:
        actors=host.service('actors','actors')
        before=actors.get_record('a')
        with pytest.raises(Exception):
            actors.update('a',state={'new':2},roles=('duplicate','duplicate'))
        assert actors.get_record('a')==before
        assert actors.select(role='old').items==['a']


@pytest.mark.asyncio
async def test_review_workspace_of_unknown_actor_never_enters_actor_projection(tmp_path):
    from society0.kernel.workspace import workspace_plugin
    from society0.kernel.interaction import InteractionScope,Moment
    plugin=actor_plugin({'rule':lambda r:None},records=[ActorRecord('a','rule')])
    async with compose(tmp_path/'run',[plugin,workspace_plugin()]) as host:
        actors=host.service('actors','actors');workspace=host.service('workspace','workspace')
        with pytest.raises(KeyError):workspace.open(InteractionScope('missing',Moment(1,'work')))
        assert list(actors)==['a'] and workspace.open(InteractionScope('a',Moment(1,'work'))).state is None


@pytest.mark.asyncio
async def test_review_role_counts_state_order_and_restore_follow_independent_model(tmp_path):
    import random
    rng=random.Random(17)
    expected={str(i):{'active':True,'roles':('r0',),'state':{'z':0,'a':1}} for i in range(12)}
    plugin=actor_plugin({'rule':lambda r:None},records=[ActorRecord(key,'rule',state=data['state'],roles=data['roles']) for key,data in expected.items()])
    def verify(actors):
        assert len(actors)==len(expected)
        for role in (None,'r0','r1','r2'):
            for active in (None,False,True):
                wanted=[key for key,data in expected.items() if (role is None or role in data['roles']) and (active is None or active==data['active'])]
                cursor=None; actual=[]
                while True:
                    page=actors.select(role=role,active=active,limit=3,cursor=cursor)
                    assert page.total==len(wanted)
                    actual.extend(page.items)
                    cursor=json.loads(json.dumps(page.next_cursor))
                    if cursor is None:break
                assert actual==wanted
        for key,data in expected.items():
            record=actors.get_record(key)
            assert list(record.state.items())==list(data['state'].items())
    async with compose(tmp_path/'run',[plugin]) as host:
        actors=host.service('actors','actors')
        for index in range(60):
            key=str(rng.randrange(12)); data=expected[key]
            data['roles']=tuple(sorted(rng.sample(['r0','r1','r2'],rng.randrange(4))))
            data['active']=bool(rng.randrange(2))
            actors.update(key,roles=data['roles'],active=data['active'])
            field=rng.choice(['z','a','later'])
            data['state'][field]=index
            actors.set_state(key,field,index)
            verify(actors)
        host.service('storage','store').complete(1)
    async with compose(tmp_path/'restored',[plugin],source=tmp_path/'run') as host:
        verify(host.service('actors','actors'))
