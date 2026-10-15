"""运行映射实际委托无 store 门面，保持原生事实与恢复。"""
import pytest

from society0.kernel.actors import ActorDirectory, ActorRecord, ActorStore, actor_data_plugin, actor_plugin
from society0.kernel.composition import compose
from society0.kernel.interaction import Ref
from society0.kernel.plugins import Plugin
from society0.kernel.selection import select_ids
from society0.kernel.storage import StageReader, StageStore


class DirectoryFacade:
    def __init__(self, directory):
        self._directory = directory
        self.calls = []

    def driver_names(self):
        self.calls.append('driver_names')
        return self._directory.driver_names()

    @property
    def persona_dependencies(self):
        return self._directory.persona_dependencies

    def persona_in(self, view, actor):
        self.calls.append('persona_in')
        return 'sql:' + self._directory.persona_in(view, actor)

    @property
    def config_dependencies(self):
        return self._directory.config_dependencies

    @property
    def state_dependencies(self):
        return self._directory.state_dependencies

    def config_in(self, view, actor):
        return self._directory.config_in(view, actor)

    def state_values_in(self, view, actor, keys=None):
        return self._directory.state_values_in(view, actor, keys)

    def view(self, actor):
        self.calls.append('view')
        original = self._directory.view(actor)
        class WrappedView:
            def __getattr__(self, name):
                return getattr(original, name)
            @property
            def persona(self):
                return 'view:' + original.persona
        return WrappedView()

    def get_record(self, actor):
        self.calls.append('get_record')
        return self._directory.get_record(actor)

    def __len__(self):
        self.calls.append('len')
        return len(self._directory)

    def __iter__(self):
        self.calls.append('iter')
        return iter(self._directory)

    def select(self, **kwargs):
        self.calls.append('select')
        return self._directory.select(**kwargs)

    def add(self, record):
        self.calls.append('add')
        return self._directory.add(record)

    def update(self, actor, **changes):
        self.calls.append('update')
        return self._directory.update(actor, **changes)

    def set_state(self, actor, key, value):
        self.calls.append('set_state')
        return self._directory.set_state(actor, key, value)

    def deactivate(self, actor):
        self.calls.append('deactivate')
        return self._directory.deactivate(actor)


@pytest.mark.asyncio
async def test_act_01_02_03_09_public_directory_is_used_by_every_runtime_consumer(tmp_path):
    def install(ctx):
        facade = DirectoryFacade(ctx.require('actors.data', 'directory'))
        ctx.provide('directory', facade)
    records = [ActorRecord('a', 'rule', persona='完整背景', state={'mood': 'open'}, roles=('buyer',))]
    built = []
    def factory(view):
        built.append(view)
        return ('driver', view.persona)
    plugins = [actor_data_plugin(records=records),
               Plugin('facade', requires=('actors.data',), install=install),
               actor_plugin({'rule': factory}, data=('facade', 'directory'))]
    async with compose(tmp_path / 'run', plugins) as host:
        actors = host.service('actors', 'actors')
        facade = host.service('facade', 'directory')
        assert not hasattr(facade, 'store') and not hasattr(actors, 'store')
        assert actors.directory is facade and built == []
        current = actors['a']
        assert current.driver == ('driver', 'view:完整背景')
        assert current.config is built[0]
        assert current.state_ref == Ref('actors', 'state', 'a')
        assert actors.get_record('a').state == {'mood': 'open'}
        assert len(actors) == 1 and list(actors) == ['a']
        assert actors.select(role='buyer').items == ['a']
        assert actors.driver_names() == ('rule',)
        assert actors.persona_dependencies == ('actor_personas',)
        store = host.service('storage', 'store')
        assert store.read(lambda view: actors.persona_in(view, 'a')) == 'sql:完整背景'
        actors.add(ActorRecord('b', 'rule', persona='第二背景'))
        actors.update('b', roles=('seller',))
        actors.set_state('a', 'mood', 'changed')
        actors.deactivate('b')
        assert actors.get_record('a').state == {'mood': 'changed'}
        assert actors.select(role='seller', active=False).items == ['b']
        assert set(facade.calls) == {'driver_names', 'view', 'get_record', 'len', 'iter',
                                     'select', 'persona_in', 'add', 'update', 'set_state', 'deactivate'}
        store.complete(1)
    async with compose(tmp_path / 'restored', plugins, source=tmp_path / 'run') as host:
        actors = host.service('actors', 'actors')
        assert actors['a'].driver == ('driver', 'view:完整背景')
        assert actors.get_record('a').state == {'mood': 'changed'}
        assert list(actors) == ['a', 'b'] and not actors.view('b').active


@pytest.mark.asyncio
async def test_act_04_05_directory_driver_changes_fail_before_unresolved_factory(tmp_path):
    async with compose(tmp_path / 'run', [actor_data_plugin(records=[ActorRecord('a', 'rule')])]) as host:
        raw = host.service('actors.data', 'directory')
        facade = DirectoryFacade(raw)
        actors = ActorStore(facade, {'rule': lambda record: None})
        before = list(facade.calls)
        with pytest.raises(KeyError):
            actors.add(ActorRecord('b', 'missing'))
        with pytest.raises(KeyError):
            actors.update('a', driver='missing')
        assert facade.calls == before
        raw.update('a', driver='missing', active=False)
        with pytest.raises(KeyError):
            actors['a']
        with pytest.raises(ValueError, match='unresolved actor drivers: missing'):
            ActorStore(facade, {'rule': lambda record: None})
        assert list(raw) == ['a']


@pytest.mark.asyncio
async def test_act_08_native_readonly_selection_uses_readview_run_identity(tmp_path):
    async with compose(tmp_path/'run', [actor_data_plugin(records=[ActorRecord('a', 'rule'), ActorRecord('b', 'rule')])]) as host:
        host.service('storage', 'store').complete(1)
    with StageReader(tmp_path/'run') as reader:
        directory = ActorDirectory(reader)
        first = directory.select(limit=1)
        assert first.items == ['a'] and first.total == 2
        assert directory.select(limit=1, cursor=first.next_cursor).items == ['b']


@pytest.mark.asyncio
async def test_act_06_07_08_readonly_selection_persona_and_small_fields(tmp_path):
    records = [ActorRecord(str(i), 'rule', persona='完整背景', state={'selected': i % 2 == 0},
                           config={'temperature': 0}, roles=('buyer',), active=i != 2)
               for i in range(3)]
    async with compose(tmp_path / 'run', [actor_data_plugin(records=records)]) as host:
        host.service('storage', 'store').complete(1)
    fixed = tmp_path / 'fixed'
    StageStore.prepare_readonly(tmp_path / 'run', fixed, step=1)
    for path in (tmp_path / 'run', fixed):
        with StageReader(path) as reader:
            directory = ActorDirectory(reader)
            actors = ActorStore(DirectoryFacade(directory), {'rule': lambda view: view.id})
            assert actors.driver_names() == ('rule',)
            assert len(actors) == 3 and list(actors) == ['0', '1', '2']
            first = actors.select(role='buyer', active=None, limit=1)
            assert first.items == ['0'] and first.total == 3
            second = actors.select(role='buyer', active=None, limit=1, cursor=first.next_cursor)
            assert second.items == ['1']
            assert actors.select(active=False).items == ['2']
            assert actors.get_record('0').config == {'temperature': 0}
            view = actors.view('0')
            assert view.state_values(('selected',)) == {'selected': True}
            assert list(select_ids(actors, role='buyer', predicate=lambda record: record.state_values(('selected',))['selected'])) == ['0']
            assert reader.read(lambda view: directory.persona_in(view, '0')) == '完整背景'
            assert directory.config_dependencies == ('actor_configs',)
            assert directory.state_dependencies == ('actor_state',)
            assert reader.read(lambda view: directory.config_in(view, '0')) == {'temperature': 0}
            assert reader.read(lambda view: directory.state_values_in(view, '0', ('selected', 'selected', 'missing'))) == {'selected': True}
            assert reader.read(lambda view: directory.state_values_in(view, '0', ())) == {}
            assert reader.read(lambda view: directory.state_values_in(view, '0')) == {'selected': True}
            with pytest.raises(KeyError):
                reader.read(lambda view: directory.config_in(view, 'missing'))
            with pytest.raises(KeyError):
                reader.read(lambda view: directory.persona_in(view, 'missing'))
            with pytest.raises(AttributeError):
                actors.set_state('0', 'selected', False)
        with pytest.raises(RuntimeError):
            view.config


@pytest.mark.asyncio
async def test_external_profile_uses_directory_snapshot_and_declared_versions(tmp_path):
    import json
    from dataclasses import replace
    from society0.kernel.interaction import Information, InteractionScope, Moment, Query, Unavailable, interaction_plugin
    from society0.plugins.social import social_information, social_plugin

    class External(DirectoryFacade):
        persona_dependencies = config_dependencies = state_dependencies = ('external_profiles',)

        def __init__(self, directory):
            super().__init__(directory)
            self.read_views = []

        def persona_in(self, view, actor):
            return view.query('SELECT persona FROM external_profiles WHERE actor=?', (actor,))[0][0]

        def config_in(self, view, actor):
            self.read_views.append(view)
            return json.loads(view.query('SELECT config FROM external_profiles WHERE actor=?', (actor,))[0][0])

        def state_values_in(self, view, actor, keys=None):
            self.read_views.append(view)
            state = json.loads(view.query('SELECT state FROM external_profiles WHERE actor=?', (actor,))[0][0])
            return state if keys is None else {key: state[key] for key in keys if key in state}

        def view(self, actor):
            return self._directory.store.read(lambda view: replace(
                self._directory.get_record(actor), persona=self.persona_in(view, actor),
                config=self.config_in(view, actor), state=self.state_values_in(view, actor)))

    def initialize(w):
        for actor in ('a', 'b'):
            w.execute('INSERT INTO external_profiles VALUES(?,?,?,?)',
                      (actor, 'external persona', json.dumps({'type': 'external', 'archetype': 'external-role'}),
                       json.dumps({'interests': ['external'], 'mood': 'external mood'})))

    plugins = [actor_data_plugin(records=[ActorRecord(actor, 'rule', persona='native',
                config={'type': 'native'}, state={'interests': ['native'], 'mood': 'native'}) for actor in ('a', 'b')]),
               Plugin('external', requires=('actors.data',), schema_requires=('actors.data',),
                      schema=('CREATE TABLE external_profiles(actor TEXT PRIMARY KEY NOT NULL,persona TEXT,config TEXT,state TEXT)',),
                      initialize=initialize, install=lambda ctx: ctx.provide('directory', External(ctx.require('actors.data', 'directory')))),
               actor_plugin({'rule': lambda record: record}, data=('external', 'directory')),
               interaction_plugin(lambda *args: True), social_plugin('ab', actors='external', edges=[])]
    async with compose(tmp_path / 'run', plugins) as host:
        social = host.service('social', 'mechanism')
        directory = host.service('external', 'directory')
        actors = host.service('actors', 'actors')
        store = host.service('storage', 'store')
        info = host.service('interaction', 'information')
        scope = InteractionScope('a', Moment(1, 'read'))
        expected = social.profile('a')
        assert len(directory.read_views) == 2 and directory.read_views[-2] is directory.read_views[-1]
        assert expected['type'] == actors['a'].config.config['type'] == 'external'
        assert expected['interests'] == ['external'] and expected['mood'] == 'external mood'
        assert 'external persona' in social.preference_text('a')
        assert store.read(lambda view: actors.config_in(view, 'a')) == {'type': 'external', 'archetype': 'external-role'}
        assert store.read(lambda view: actors.state_values_in(view, 'a', ('mood',))) == {'mood': 'external mood'}
        assert actors.config_dependencies == actors.state_dependencies == ('external_profiles',)
        first = await info.read(scope, '/social/profiles/a', size=19)
        assert directory.read_views[-2] is directory.read_views[-1]
        rest = await info.read(scope, '/social/profiles/a', offset=first.next_offset, expected_revision=first.revision)
        assert json.loads(first.data + rest.data) == expected
        page = await info.query(scope, '/social/profiles', Query(limit=1))
        assert page.total == 2 and page.next_cursor
        assert (await info.query(scope, '/social/profiles', Query(limit=1, cursor=page.next_cursor))).items[0]['ref'].key == 'b'
        # 原生资料变化不在外部 profile 的依赖中，旧正文版本继续可读。
        host.service('actors.data', 'directory').update('a', config={'type': 'native changed'})
        await info.read(scope, '/social/profiles/a', expected_revision=first.revision)
        store.transaction(lambda w: w.execute('UPDATE external_profiles SET state=? WHERE actor=?',
                          (json.dumps({'interests': ['updated'], 'mood': 'updated mood'}), 'a')))
        assert social.profile('a')['mood'] == 'updated mood'
        with pytest.raises(ValueError, match='document revision changed'):
            await info.read(scope, '/social/profiles/a', offset=first.next_offset, expected_revision=first.revision)
        with pytest.raises(ValueError):
            await info.query(scope, '/social/profiles', Query(limit=1, cursor=page.next_cursor))
        updated = await info.read(scope, '/social/profiles/a')
        assert json.loads(updated.data)['interests'] == ['updated']
        store.transaction(lambda w: w.execute('UPDATE external_profiles SET config=? WHERE actor=?',
                          (json.dumps({'type': 'external changed'}), 'a')))
        assert social.profile('a')['type'] == 'external changed'
        assert json.loads((await info.read(scope, '/social/profiles/a')).data)['type'] == 'external changed'
        with pytest.raises(ValueError, match='document revision changed'):
            await info.read(scope, '/social/profiles/a', expected_revision=updated.revision)
        standalone = Information(lambda *args: True)
        provider = social_information(store, actors=directory)
        standalone.mount('/social', provider, owned=True)
        try:
            assert json.loads((await standalone.read(scope, '/social/profiles/a')).data) == social.profile('a')
            with pytest.raises(Unavailable):
                await standalone.read(InteractionScope('outsider', scope.moment), '/social/profiles/a')
        finally:
            await standalone.close()
