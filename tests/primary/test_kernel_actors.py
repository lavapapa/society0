"""持久主体目录、按需驱动与私有工作区。"""
import pytest
from society0.kernel.actors import ActorStore, ActorRecord, actor_plugin
from society0.kernel.composition import compose


@pytest.mark.asyncio
async def test_actor_selector_continues_after_activation_thread_writes(tmp_path):
    from society0.kernel.plugins import Plugin
    from society0.kernel.threads import THREAD_SCHEMA,ThreadStore
    plugins=[actor_plugin({'rule':lambda r:None},records=[ActorRecord(str(i),'rule',roles=('buyer',)) for i in range(3)]),
             Plugin('thread_data',schema=THREAD_SCHEMA)]
    async with compose(tmp_path/'run',plugins) as host:
        actors=host.service('actors','actors');store=host.service('storage','store')
        first=actors.select(role='buyer',limit=1)
        threads=ThreadStore(store)
        thread=threads.open(first.items[0],1,'decision')
        threads.append_message(thread,{'role':'user','content':'first actor activation'})
        second=actors.select(role='buyer',limit=1,cursor=first.next_cursor)
        assert second.items==['1'] and second.revision==first.revision
        actors.update('2',roles=('seller',))
        with pytest.raises(ValueError,match='cursor'):
            actors.select(role='buyer',limit=1,cursor=second.next_cursor)


@pytest.mark.asyncio
async def test_actor_catalog_builds_only_requested_driver_and_preserves_subjective_fields(tmp_path):
    created=[]
    def driver(record):
        created.append(record.id)
        return ('driver',record.id)
    records=(ActorRecord(str(i),'rule',persona={'type':'type persona','instance':str(i)},
                         state={'mood':'open'},config={'temperature':0},roles=('buyer',)) for i in range(1000))
    async with compose(tmp_path/'run',[actor_plugin({'rule':driver},records=records)]) as host:
        actors=host.service('actors','actors')
        assert len(actors)==1000 and created==[]
        actor=actors['7']
        assert actor.id=='7' and actor.driver==('driver','7') and created==['7']
        assert actor.config.persona=={'type':'type persona','instance':'7'}
        assert actor.config.state=={'mood':'open'} and actor.config.config=={'temperature':0}
        page=actors.select(role='buyer',limit=2)
        assert page.items==['0','1'] and page.total==1000 and page.next_cursor
        assert created==['7']


@pytest.mark.asyncio
async def test_actor_updates_selection_cursor_and_restore_preserve_roles_and_order(tmp_path):
    plugin=actor_plugin({'rule':lambda r:None},records=[ActorRecord('b','rule',roles=('buyer',)),ActorRecord('a','rule',roles=('seller',))])
    async with compose(tmp_path/'run',[plugin]) as host:
        actors=host.service('actors','actors')
        cursor=actors.select(limit=1).next_cursor
        actors.update('b',state={'mood':'changed'},active=False,roles=('seller',))
        with pytest.raises(ValueError,match='cursor'): actors.select(limit=1,cursor=cursor)
        assert actors.select().items==['a']
        assert actors.select(role='seller',active=False).items==['b']
        host.service('storage','store').complete(1)
    async with compose(tmp_path/'restored',[plugin],source=tmp_path/'run') as host:
        actors=host.service('actors','actors')
        assert list(actors)==['b','a']
        assert actors.get_record('b').state=={'mood':'changed'}
        assert actors.get_record('b').roles==('seller',)


@pytest.mark.asyncio
async def test_workspace_is_actor_owned_and_survives_new_moment_and_source_removal(tmp_path):
    import shutil
    from society0.kernel.workspace import workspace_plugin
    from society0.kernel.interaction import InteractionScope,Moment
    plugin=actor_plugin({'rule':lambda r:None},records=[ActorRecord('a','rule'),ActorRecord('b','rule')])
    body=b'complete snapshot\x00\xff'*10000
    async with compose(tmp_path/'run',[plugin,workspace_plugin()]) as host:
        workspace=host.service('workspace','workspace')
        lease=workspace.open(InteractionScope('a',Moment(1,'work')))
        lease.save(b'shell-state',{'removed':[],'entries':[{'path':'/raw','kind':'file','mode':420,'modified_ns':0,'created_ns':0,'content':body}]})
        assert await lease.callback('read','/raw')==body
        assert not await workspace.open(InteractionScope('b',Moment(1,'work'))).callback('exists','/raw')
        host.service('storage','store').complete(1)
    async with compose(tmp_path/'restored',[plugin,workspace_plugin()],source=tmp_path/'run') as host:
        shutil.rmtree(tmp_path/'run')
        lease=host.service('workspace','workspace').open(InteractionScope('a',Moment(2,'work')))
        assert await lease.callback('read','/raw')==body


@pytest.mark.asyncio
async def test_actor_update_is_atomic_on_invalid_json_and_unknown_driver_is_explicit(tmp_path):
    plugin=actor_plugin({'rule':lambda r:None},records=[ActorRecord('a','rule')])
    async with compose(tmp_path/'run',[plugin]) as host:
        actors=host.service('actors','actors')
        before=actors.get_record('a')
        with pytest.raises(TypeError): actors.update('a',persona='new',state={'invalid':object()})
        assert actors.get_record('a')==before
        with pytest.raises(KeyError): actors.add(ActorRecord('b','missing'))
        assert len(actors)==1


@pytest.mark.asyncio
async def test_active_actor_selector_work_does_not_scan_inactive_population(tmp_path):
    measured=[]
    for history in (100,10000):
        records=[ActorRecord('active'+str(i),'rule',roles=('buyer',)) for i in range(3)]
        records.extend(ActorRecord('inactive'+str(i),'rule',roles=('buyer',),active=False) for i in range(history))
        async with compose(tmp_path/str(history),[actor_plugin({'rule':lambda r:None},records=records)]) as host:
            actors=host.service('actors','actors')
            original=actors.store.read
            count=[0]
            def read(callback,**kwargs):
                def instrument(view):
                    view._connection.set_progress_handler(lambda:count.__setitem__(0,count[0]+1) or False,1)
                    return callback(view)
                return original(instrument,**kwargs)
            actors.store.read=read
            page=actors.select(role='buyer',limit=2)
            assert page.total==3 and len(page.items)==2
            measured.append(count[0])
    print({'inactive_counts':[100,10000],'selector_vm':measured})
    assert measured[1]<=measured[0]*2


@pytest.mark.asyncio
async def test_hot_actor_flag_does_not_capture_cold_persona_and_config(tmp_path):
    giant='原文'*500000
    async with compose(tmp_path/'run',[actor_plugin({'rule':lambda r:None},records=[
        ActorRecord('a','rule',persona=giant,config={'original':giant})])]) as host:
        actors=host.service('actors','actors')
        store=host.service('storage','store')
        actors.update('a',active=False)
        assert store._session.memory_used < 65536
        assert actors.get_record('a').persona==giant


@pytest.mark.asyncio
async def test_fixed_role_selection_seeks_without_scanning_unrelated_active_actors(tmp_path):
    measured=[]
    for count in (100,10000):
        records=[ActorRecord('other'+str(i),'rule',roles=('other',)) for i in range(count)]
        records.extend(ActorRecord('buyer'+str(i),'rule',roles=('buyer',)) for i in range(3))
        async with compose(tmp_path/str(count),[actor_plugin({'rule':lambda r:None},records=records)]) as host:
            actors=host.service('actors','actors')
            original=actors.store.read
            vm=[0]
            def read(callback,**kwargs):
                def instrument(view):
                    view._connection.set_progress_handler(lambda:vm.__setitem__(0,vm[0]+1) or False,1)
                    return callback(view)
                return original(instrument,**kwargs)
            actors.store.read=read
            page=actors.select(role='buyer',limit=2)
            assert page.total==3 and len(page.items)==2
            measured.append(vm[0])
    print({'unrelated_active':[100,10000],'role_selector_vm':measured})
    assert measured[1]<=measured[0]*2


@pytest.mark.asyncio
async def test_subjective_state_key_update_preserves_order_and_skips_cold_neighbor(tmp_path):
    state={'z':'原文'*500000,'a':1}
    async with compose(tmp_path/'run',[actor_plugin({'rule':lambda r:None},records=[ActorRecord('a','rule',state=state)])]) as host:
        actors=host.service('actors','actors')
        actors.set_state('a','a',2)
        actors.set_state('a','new',3)
        assert host.service('storage','store')._session.memory_used<65536
        restored=actors.get_record('a').state
        assert list(restored)==['z','a','new']
        assert restored=={**state,'a':2,'new':3}
