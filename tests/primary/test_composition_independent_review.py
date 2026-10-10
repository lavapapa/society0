"""非作者探针：只验证公开组合与信息消费，不修改实现。"""
import pytest

from society0.kernel.actors import ActorRecord, actor_data_plugin
from society0.kernel.composition import compose
from society0.kernel.information_sql import SQLInformation, DatasetSpec
from society0.kernel.interaction import Information, InteractionScope, Moment, Query, interaction_plugin
from society0.kernel.plugins import Plugin
from society0.plugins.round_robin import round_robin_plugin


@pytest.mark.asyncio
async def test_shared_provider_mount_must_not_pollute_other_router_cursor(tmp_path):
    schema = (
        'CREATE TABLE rows(id INTEGER PRIMARY KEY)',
        'CREATE TABLE acl_a(id INTEGER PRIMARY KEY, enabled INTEGER)',
        'CREATE TABLE acl_b(id INTEGER PRIMARY KEY, enabled INTEGER)',
    )
    def initialize(w):
        w.execute('INSERT INTO rows VALUES(1),(2)')
        w.execute('INSERT INTO acl_a VALUES(1,1)')
        w.execute('INSERT INTO acl_b VALUES(1,1)')
    async with compose(tmp_path/'run', [Plugin('data', schema=schema, initialize=initialize)]) as host:
        store = host.service('storage', 'store')
        provider = SQLInformation('data', store, {'rows': DatasetSpec('rows', 'id', ('id',))})
        def allow_a(*args):
            return store.read(lambda r: bool(r.query('SELECT enabled FROM acl_a WHERE id=1')[0][0]))
        def allow_b(*args):
            return store.read(lambda r: bool(r.query('SELECT enabled FROM acl_b WHERE id=1')[0][0]))
        first = Information(allow_a, access_dependencies=('acl_a',))
        second = Information(allow_b, access_dependencies=('acl_b',))
        first.mount('/data', provider)
        second.mount('/data', provider)
        try:
            scope = InteractionScope('a', Moment(1, 'read'))
            page = await first.bound(scope).query('/data/rows', Query(limit=1))
            store.transaction(lambda w: w.execute('UPDATE acl_b SET enabled=0'))
            # 第一条路由的资料与权限均未变化，应能继续读取第二条记录。
            tail = await first.bound(scope).query('/data/rows', Query(limit=1, cursor=page.next_cursor))
            assert [row['id'] for row in tail.items] == [2]
        finally:
            await second.close()
            await first.close()
            provider.close()


@pytest.mark.asyncio
async def test_two_named_rr_instances_share_data_without_drivers_and_restore(tmp_path):
    directory = actor_data_plugin(records=[ActorRecord(x, 'uninstalled') for x in 'abcd'])
    plan = [Plugin('bundle', includes=(directory, round_robin_plugin('abcd', group_size=4, name='one'),
                                    round_robin_plugin('abcd', group_size=2, name='two'))),
            interaction_plugin(lambda *args: True)]
    for name, source in [('run', None), ('restored', tmp_path/'run')]:
        async with compose(tmp_path/name, plan, source=source) as host:
            one, two = (host.service(name, 'mechanism') for name in ('one', 'two'))
            if source is None:
                one.start_round(1)
                two.start_round(1)
            scope = InteractionScope('a', Moment(1, 'read'))
            info = host.service('interaction', 'information').bound(scope)
            assert (await info.query('/one/participants', Query())).items[0]['partner'] == ('d' if source is None else 'c')
            assert (await info.query('/two/participants', Query())).items[0]['partner'] == 'b'
            if source is None:
                one.start_round(2)
                assert two.pairing('a')['current_round'] == 1
                host.service('storage', 'store').complete(1)
            else:
                assert one.pairing('a')['current_round'] == 2


@pytest.mark.asyncio
async def test_same_moment_llm_thread_survives_complete_restore(tmp_path):
    from dataclasses import replace
    from society0.kernel.llm import LLMDriver
    from society0.kernel.runtime import Actor
    from society0.kernel.threads import THREAD_SCHEMA, ThreadStore
    from tests.primary.test_kernel_llm import FakeProvider, call, reply
    from tests.primary.test_round_robin_information import plugins, session

    plan = [*plugins(), Plugin('threads', schema=THREAD_SCHEMA)]
    async with compose(tmp_path/'run', plan) as host:
        host.service('conversation', 'mechanism').start_round(1)
        threads = ThreadStore(host.service('storage', 'store'))
        provider = FakeProvider(threads, [reply(call('head', 'read', {'path': '/world/conversation/group/1'})), reply(text='seen')])
        driver = LLMDriver(provider, threads, input_builder=lambda s: [])
        current = replace(session(host), actor=Actor('a', driver))
        assert (await driver.run(current)).status == 'completed'
        tid = current.cursors['thread_id']
        before = threads.read_messages(tid)
        host.service('storage', 'store').complete(1)
    async with compose(tmp_path/'restore', plan, source=tmp_path/'run') as host:
        threads = ThreadStore(host.service('storage', 'store'))
        provider = FakeProvider(threads, [reply(text='continue same moment')])
        driver = LLMDriver(provider, threads, input_builder=lambda s: [])
        current = replace(session(host), actor=Actor('a', driver))
        assert (await driver.run(current)).status == 'completed'
        assert current.cursors['thread_id'] == tid
        assert provider.requests[-1][2][:len(before)] == before
