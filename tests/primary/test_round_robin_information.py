"""RR 的主体公开资料合同；旧 v4.1.14 的倒退/清空缺陷不作为兼容要求。"""
import json
from types import SimpleNamespace

import pytest

from society0.kernel.actor_files import ActorFiles
from society0.kernel.actors import ActorRecord, actor_plugin
from society0.kernel.composition import compose
from society0.kernel.interaction import InteractionScope, Moment, Query, Ref, Unavailable, interaction_plugin
from society0.kernel.runtime import Actor, Session
from society0.plugins.round_robin import round_robin_plugin


def plugins():
    return [actor_plugin({'rule': lambda record: None}, records=[ActorRecord(x, 'rule') for x in 'abcdefghi']),
            interaction_plugin(lambda *args: True), round_robin_plugin('abcdefgh', group_size=4, session_duration_minutes=17)]


def session(host, actor='a'):
    scope = InteractionScope(actor, Moment(1, 'talk'))
    return Session(Actor(actor, None), scope, host.service('interaction', 'information').bound(scope),
                   host.service('interaction', 'actions').bound(scope), {}, None, (),
                   SimpleNamespace(prepare_artifact=host.service('storage', 'store').prepare_artifact), step=1)


async def all_rows(info, route):
    rows = []; cursor = None
    while True:
        page = await info.query('/conversation/' + route, Query(limit=1, cursor=cursor))
        rows.extend(page.items)
        assert page.total >= len(rows)
        if page.next_cursor is None:
            assert page.total == len(rows)
            return rows
        cursor = page.next_cursor


@pytest.mark.asyncio
async def test_discoverable_authorized_pairing_group_plan_and_history(tmp_path):
    async with compose(tmp_path/'run', plugins()) as host:
        env = host.service('conversation', 'mechanism'); env.start_round(1)
        current = session(host); info = current.information
        routes = {item['path'] for item in (await info.list('/conversation')).items}
        assert {'/conversation/group', '/conversation/members', '/conversation/partners', '/conversation/plan'} <= routes
        head = (await all_rows(info, 'group'))[0]
        assert (head['total_rounds'], head['duration']) == (3, 17)
        assert [row['id'] for row in await all_rows(info, 'members')] == list('abcd')
        assert [row['partner'] for row in await all_rows(info, 'partners')] == ['d']
        plan = await all_rows(info, 'plan')
        assert [(row['round'], row['first'], row['second']) for row in plan] == [(1,'a','d'), (2,'a','c'), (3,'a','b')]
        files = ActorFiles(current, None)
        assert '/world/conversation/group' in {item['path'] for item in (await files.ls('/world/conversation')).items}
        assert json.loads((await files.read('/world/conversation/group/1'))['data'])['total_rounds'] == 3
        for route, key in [('members','e'), ('partners','5'), ('partners','3'), ('plan','7'), ('plan','2')]:
            with pytest.raises(Unavailable): await info.read('/conversation/'+route+'/'+key)
        for route in ('group','members','partners','plan','participants'):
            assert await all_rows(session(host, 'i').information, route) == []
        assert (await info.query('/conversation/members', Query(filters=(('id','eq','e'),)))).total == 0


@pytest.mark.asyncio
async def test_round_boundaries_same_moment_originals_and_restore(tmp_path):
    body = '完整原文🙂' * 300000
    plan = plugins()
    async with compose(tmp_path/'run', plan) as host:
        env = host.service('conversation', 'mechanism'); env.start_round(1)
        a = session(host); d = session(host, 'd')
        sent = await a.actions.invoke('conversation.send_message_to_partner', Ref('conversation','participants','a'), {'content':body})
        path = '/conversation/content/' + str(sent.value['message_id'])
        files = ActorFiles(d, None)
        import base64
        chunk = await files.read('/world'+path, offset=800000, size=32, encoding='base64')
        assert base64.b64decode(chunk['data']) == body.encode()[800000:800032]
        before = env.pairing('a'); env.start_round(1)
        assert env.pairing('a') == before
        assert (await session(host,'d').information.query('/conversation/messages', Query())).total == 1
        with pytest.raises(ValueError): env.initialize_round_messages(2)
        assert (await d.information.query('/conversation/messages', Query())).total == 1
        env.initialize_round_messages(1)
        assert (await d.information.query('/conversation/messages', Query())).total == 0
        assert (await d.information.query('/conversation/history', Query())).total == 1
        env.start_round(2)
        before = env.pairing('a')
        with pytest.raises(ValueError): env.start_round(1)
        assert env.pairing('a') == before
        env.start_round(3)
        assert [row['partner'] for row in await all_rows(a.information,'partners')] == ['d','c','b']
        host.service('storage','store').complete(1)
    async with compose(tmp_path/'restored', plan, source=tmp_path/'run') as host:
        assert host.service('conversation','mechanism').pairing('a')['partner_history'] == ['d','c','b']
        info = session(host,'d').information
        pieces = []; offset = 0
        while True:
            part = await info.read(path, offset=offset, size=65536); pieces.append(part.data)
            if part.next_offset is None: break
            offset = part.next_offset
        assert b''.join(pieces) == body.encode()
        with pytest.raises(Unavailable): await session(host,'e').information.read(path, size=32)


@pytest.mark.asyncio
async def test_scripted_llm_discovers_rr_files_and_retains_same_moment_thread(tmp_path):
    from dataclasses import replace
    from society0.kernel.plugins import Plugin
    from society0.kernel.threads import THREAD_SCHEMA, ThreadStore
    from society0.kernel.llm import LLMDriver
    from tests.primary.test_kernel_llm import FakeProvider, call, reply

    async with compose(tmp_path/'run', [*plugins(), Plugin('threads', schema=THREAD_SCHEMA)]) as host:
        host.service('conversation','mechanism').start_round(1)
        threads = ThreadStore(host.service('storage','store'))
        provider = FakeProvider(threads, [
            reply(call('discover','ls',{'path':'/world/conversation'})),
            reply(call('head','read',{'path':'/world/conversation/group/1'})),
            reply(call('members','read',{'path':'/world/conversation/members/a'})),
            reply(call('history','read',{'path':'/world/conversation/partners/1'})),
            reply(call('future','read',{'path':'/world/conversation/plan/3'})),
            reply(text='资料读取完成'), reply(text='同一时点继续')])
        driver = LLMDriver(provider, threads, input_builder=lambda s: [])
        current = replace(session(host), actor=Actor('a',driver))
        assert (await driver.run(current)).status == 'completed'
        tid = current.cursors['thread_id']
        before = threads.read_messages(tid)
        tool_outputs = [json.loads(message['content']) for message in before if message['role']=='tool']
        assert all('error' not in output for output in tool_outputs)
        assert json.loads(tool_outputs[1]['data'])['total_rounds'] == 3
        assert json.loads(tool_outputs[3]['data'])['partner'] == 'd'
        assert json.loads(tool_outputs[4]['data'])['round'] == 2
        again = replace(session(host), actor=Actor('a',driver))
        assert (await driver.run(again)).status == 'completed'
        assert again.cursors['thread_id'] == tid
        assert provider.requests[-1][2][:len(before)] == before


@pytest.mark.asyncio
async def test_actor_driver_can_depend_on_rr_without_data_cycle_and_restore(tmp_path):
    from dataclasses import replace
    from society0.kernel.drivers import RuleDriver
    from society0.kernel.plugins import Plugin
    from society0.kernel.runtime import DriverResult

    def install(ctx):
        mechanism = ctx.require('conversation', 'mechanism')
        async def run(current):
            head = (await all_rows(current.information, 'group'))[0]
            return DriverResult('completed', (mechanism.pairing(current.actor.id), head))
        ctx.provide('factory', lambda record: RuleDriver(run))
    plan = [actor_plugin({'rule': ('rr_driver','factory')}, records=[ActorRecord(x,'rule') for x in 'abcd']),
            Plugin('rr_driver', ('conversation',), install), interaction_plugin(lambda *args: True),
            round_robin_plugin('abcd', group_size=4)]
    for directory, source in [('run', None), ('restored', tmp_path/'run')]:
        async with compose(tmp_path/directory, plan, source=source) as host:
            if source is None: host.service('conversation','mechanism').start_round(1)
            actor = host.service('actors','actors')['a']
            result = await actor.driver.run(replace(session(host), actor=actor))
            assert result.value[0]['current_partner'] == 'd'
            assert result.value[1]['total_rounds'] == 3
            if source is None: host.service('storage','store').complete(1)
