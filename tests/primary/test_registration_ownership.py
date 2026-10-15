"""共享登记的所有权、独立注销与真实索引回归。"""
from contextlib import ExitStack

import pytest

from society0.kernel.interaction import (
    Action, ActionResult, Actions, DocumentChunk, Information,
    InteractionScope, Moment, Ref, Unavailable,
)
from society0.kernel.plugins import Plugin, PluginHost


class Provider:
    def __init__(self):
        self.closed = 0

    def ref(self, path):
        return Ref('demo', 'document', path)

    def read(self, scope, path, *, offset, size):
        body = '完整原文🙂'.encode()
        data = body[offset:offset + size]
        end = offset + len(data)
        return DocumentChunk(data, len(body), end if end < len(body) else None,
                             1, self.ref(path))

    def close(self):
        self.closed += 1


def scope():
    return InteractionScope('a', Moment(1, 'read'))


def action(name, *, description='共享检索词 needle', tags=('shared',), handler=None):
    return Action(name, ('demo', 'item'), description, {'type': 'object'},
                  handler or (lambda *args: ActionResult('completed', {'name': name})),
                  tags=tags)


@pytest.mark.asyncio
async def test_own_01_02_information_handles_are_independent_and_registration_specific():
    provider = Provider()
    info = Information(lambda *args: True)
    try:
        remove_a = info.mount('/a', provider)
        remove_b = info.mount('/b', provider)
        assert callable(remove_a) and callable(remove_b)
        before = await info.list(scope(), '/', limit=1)
        assert before.total == 2
        remove_a()
        remove_a()
        assert [item['path'] for item in (await info.list(scope(), '/')).items] == ['/b']
        with pytest.raises(ValueError, match='mount cursor mismatch'):
            await info.list(scope(), '/', limit=1, cursor=before.next_cursor)
        with pytest.raises(Unavailable):
            await info.read(scope(), '/a/body')
        assert (await info.read(scope(), '/b/body')).data == '完整原文🙂'.encode()
        replacement = info.mount('/a', provider)
        remove_a()  # 同一 provider 重新挂载后，旧登记仍然失效。
        assert (await info.read(scope(), '/a/body')).total_bytes == len('完整原文🙂'.encode())
        replacement()
        assert provider.closed == 0
    finally:
        await info.close()
    remove_a()
    remove_b()
    assert provider.closed == 0


@pytest.mark.asyncio
@pytest.mark.parametrize('same_provider', [False, True])
async def test_root_cursor_rejects_same_path_and_ref_reregistration(same_provider):
    import json
    info = Information(lambda *args: True)
    provider = Provider()
    try:
        info.mount('/a', Provider())
        remove = info.mount('/b', provider)
        first = await info.list(scope(), '/', limit=1)
        assert [item['path'] for item in first.items] == ['/a']
        remove()
        info.mount('/b', provider if same_provider else Provider())
        remove()
        with pytest.raises(ValueError, match='mount cursor mismatch'):
            await info.list(scope(), '/', limit=1, cursor=json.loads(json.dumps(first.next_cursor)))
        fresh = await info.list(scope(), '/', limit=1)
        assert fresh.total == 2 and fresh.items[0]['path'] == '/a'
        assert (await info.list(scope(), '/', limit=1, cursor=fresh.next_cursor)).items[0]['path'] == '/b'
    finally:
        await info.close()


@pytest.mark.asyncio
async def test_root_cursor_is_instance_local_and_ignores_invisible_mount_changes():
    first = Information(lambda scope, op, ref: ref.key != '/hidden')
    second = Information(lambda *args: True)
    try:
        for info in (first, second):
            info.mount('/a', Provider())
            info.mount('/b', Provider())
        page = await first.list(scope(), '/', limit=1)
        remove = first.mount('/hidden', Provider())
        assert (await first.list(scope(), '/', limit=1, cursor=page.next_cursor)).items[0]['path'] == '/b'
        remove()
        assert (await first.list_files(scope(), '/', limit=1, cursor=page.next_cursor)).total == 2
        with pytest.raises(ValueError, match='mount cursor mismatch'):
            await second.list(scope(), '/', limit=1, cursor=page.next_cursor)
    finally:
        await first.close()
        await second.close()


@pytest.mark.asyncio
async def test_own_03_owned_provider_remains_owned_until_router_close():
    provider = Provider()
    info = Information(lambda *args: True)
    first = info.mount('/a', provider, owned=True)
    second = info.mount('/b', provider, owned=True)
    first()
    second()
    assert provider.closed == 0
    await info.close()
    await info.close()
    first()
    second()
    assert provider.closed == 1


@pytest.mark.asyncio
async def test_own_04_05_actions_dispose_fts_tags_dependencies_and_stale_cursor():
    observed = []
    versions = {'shared': 1, 'a_only': 2, 'b_only': 3}

    def revision(current_scope, tables):
        observed.append(tuple(tables))
        return max((versions[table] for table in tables), default=0)

    target = Ref('demo', 'item', 'x')
    with Actions(lambda *args: True, dependency_revision=revision) as registry:
        remove_a = registry.register(action('a', tags=('a_tag', 'shared')),
                                     dependencies=('shared', 'a_only', 'shared'))
        remove_b = registry.register(action('b'), dependencies=('shared', 'b_only'))
        assert callable(remove_a) and callable(remove_b)
        before = await registry.find(scope(), target, query='needle', limit=1)
        assert before.total == 2
        remove_a()
        remove_a()
        remaining = await registry.find(scope(), target, query='needle', tags=('shared',))
        assert [item.name for item in remaining.items] == ['b']
        assert observed[-1] == ('b_only', 'shared')
        assert (await registry.find(scope(), target, tags=('a_tag',))).total == 0
        with pytest.raises(ValueError, match='action cursor mismatch'):
            await registry.find(scope(), target, query='needle', limit=1, cursor=before.next_cursor)
        with pytest.raises(Unavailable):
            await registry.describe(scope(), 'a', target)
        assert (await registry.invoke(scope(), 'a', target, {})).status == 'rejected'
        assert (await registry.invoke(scope(), 'b', target, {})).value == {'name': 'b'}
        registry.register(action('c'), dependencies=('shared',))
        first_page = await registry.find(scope(), target, limit=1)
        remove_a()  # 幂等注销不得更新 generation。
        assert [item.name for item in (await registry.find(
            scope(), target, limit=1, cursor=first_page.next_cursor)).items] == ['c']
        remove_b()
        assert observed  # shared 仍由 c 使用。
        assert (await registry.find(scope(), target, query='needle')).total == 1
        assert observed[-1] == ('shared',)
    remove_a()
    remove_b()
    assert len(registry) == 0


@pytest.mark.asyncio
async def test_own_06_old_action_handle_preserves_replacement_identity_and_tags():
    target = Ref('demo', 'item', 'x')
    with Actions(lambda *args: True) as registry:
        remove = registry.register(action('same', description='old needle', tags=('old',)))
        assert callable(remove)
        remove()
        new_remove = registry.register(action('same', description='new token', tags=('new',)))
        remove()
        assert (await registry.find(scope(), target, query='needle')).total == 0
        assert (await registry.find(scope(), target, query='token', tags=('new',))).total == 1
        assert (await registry.find(scope(), target, tags=('old',))).total == 0
        assert (await registry.describe(scope(), 'same', target)).description == 'new token'
        new_remove()
        assert (await registry.find(scope(), target, query='token')).total == 0


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['duplicate', 'schema', 'body', 'cancel'])
async def test_own_07_08_partial_installer_and_body_failure_clean_before_dependencies(failure):
    import asyncio

    providers = []
    registries = []
    calls = []

    def api(ctx):
        info = Information(lambda *args: True)
        actions = Actions(lambda *args: True)
        registries.append((info, actions))
        ctx.provide('information', info)
        ctx.provide('actions', actions)
        async def close_api():
            assert (await info.list(scope(), '/')).total == 0
            assert len(actions) == 0
            calls.append('api')
            await info.close()
            actions.close()
        ctx.on_close(close_api)

    def install(ctx):
        provider = Provider()
        providers.append(provider)
        def close_provider():
            calls.append('provider')
            provider.close()
        ctx.on_close(close_provider)
        info = ctx.require('api', 'information')
        actions = ctx.require('api', 'actions')
        ctx.on_close(info.mount('/demo', provider))
        ctx.on_close(actions.register(action('one')))
        ctx.on_close(actions.register(action('two')))
        if failure == 'duplicate':
            actions.register(action('one'))
        elif failure == 'schema':
            actions.register(Action('bad', ('demo', 'item'), '', {'type': 'bad'}, lambda *args: None))

    if failure == 'duplicate':
        expected = ValueError
    elif failure == 'schema':
        from jsonschema.exceptions import SchemaError
        expected = SchemaError
    else:
        expected = RuntimeError if failure == 'body' else asyncio.CancelledError
    with pytest.raises(expected):
        async with PluginHost([Plugin('owner', requires=('api',), install=install),
                               Plugin('api', install=api)]):
            if failure == 'body':
                raise RuntimeError('body failed')
            if failure == 'cancel':
                raise asyncio.CancelledError()
    assert calls == ['provider', 'api']
    assert providers[0].closed == 1
    info, actions = registries[0]
    await info.close()
    actions.close()
    assert providers[0].closed == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('domain', ['conversation', 'social'])
async def test_own_07_domain_register_failure_cleans_its_prior_contributions(tmp_path, monkeypatch, domain):
    from society0.kernel.actors import actor_data_plugin, ActorRecord
    from society0.kernel.composition import compose
    from society0.kernel.interaction import interaction_plugin
    from society0.plugins import round_robin, social

    plugins = [actor_data_plugin(records=[ActorRecord('a', 'rule'), ActorRecord('b', 'rule')]),
               interaction_plugin(lambda *args: True)]
    if domain == 'conversation':
        plugins.append(round_robin.round_robin_plugin('ab', group_size=2))
        module, factory = round_robin, 'round_robin_information'
        conflict = 'conversation.broadcast_to_group'
    else:
        plugins.append(social.social_plugin('ab'))
        module, factory = social, 'SocialInformation'
        conflict = 'social.comment'
    async with compose(tmp_path / 'run', plugins) as host:
        mechanism = host.service(domain, 'mechanism')
        original = getattr(module, factory)
        closed = []
        def tracked(*args, **kwargs):
            provider = original(*args, **kwargs)
            close = provider.close
            def release():
                closed.append(provider)
                close()
            provider.close = release
            return provider
        monkeypatch.setattr(module, factory, tracked)
        info = Information(lambda *args: True)
        try:
            with Actions(lambda *args: True) as registry:
                registry.register(action(conflict))
                with ExitStack() as resources:
                    with pytest.raises(ValueError, match='already registered'):
                        mechanism.register(info, registry, on_close=resources.callback)
                assert len(registry) == 1
                assert (await info.list(scope(), '/')).total == 0
                assert len(closed) == 1
        finally:
            await info.close()
