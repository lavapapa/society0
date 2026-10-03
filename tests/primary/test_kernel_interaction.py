"""共享环境交互合同：先以两个机制、两个主体验证公开语义。"""
import asyncio
from dataclasses import FrozenInstanceError

import pytest

from society0.kernel.interaction import (
    Action, ActionResult, Actions, DocumentChunk, Information,
    InteractionScope, Moment, Page, Query, Ref, ScopeClosed, Unavailable,
)


class MarketMessages:
    """两个机制共享同一订单事实，信息提供者按主体给出已授权页面。"""
    def __init__(self):
        self.orders = {'one': {'buyer': 'alice', 'price': 12, 'stock': 2}}
        self.messages = {'alice': [], 'bob': []}
        self.calls = []
        self.revision = 0

    def ref(self, path):
        parts = path.strip('/').split('/')
        return Ref(parts[0], 'directory' if len(parts) == 1 else 'order', parts[-1])

    def allows(self, scope, operation, ref):
        if ref.kind == 'directory':
            return True
        if ref.namespace == 'messages':
            return ref.key == scope.actor
        if ref.key not in self.orders:
            return False
        if operation == 'discover':
            return True
        if operation == 'read':
            return scope.actor == self.orders[ref.key]['buyer']
        return scope.actor == 'alice'

    def list(self, scope, path, *, limit, cursor):
        self.calls.append(('list', scope.actor, path, limit))
        if path == '/messages':
            return Page([Ref('messages', 'order', scope.actor)], 1, None, self.revision)
        keys = tuple(self.orders)
        offset = cursor or 0
        selected = keys[offset:offset + limit]
        return Page([Ref('market', 'order', key) for key in selected], len(keys),
                    offset + len(selected) if offset + len(selected) < len(keys) else None, self.revision)

    def read(self, scope, path, *, offset, size):
        self.calls.append(('read', scope.actor, path, size))
        ref = self.ref(path)
        text = ('价格12元；完整说明🙂' if ref.namespace == 'market'
                else '\n'.join(self.messages[scope.actor])).encode()
        end = min(len(text), offset + size)
        return DocumentChunk(text[offset:end], len(text), end if end < len(text) else None,
                             self.revision, ref)

    def query(self, scope, path, query):
        self.calls.append(('query', scope.actor, path, query))
        return Page([{'price': 12}], 1, None, self.revision)

    def purchase(self, scope, target, arguments):
        order = self.orders.get(target.key)
        if not order or order['stock'] < arguments['quantity']:
            return ActionResult('rejected', {'reason': 'insufficient_stock'})
        order['stock'] -= arguments['quantity']
        self.messages['bob'].append(f"{scope.actor}购买{arguments['quantity']}件")
        self.revision += 1
        return ActionResult('completed', {'remaining': order['stock']})


def setup_market():
    mechanism = MarketMessages()
    information = Information(mechanism.allows)
    information.mount('/market', mechanism)
    information.mount('/messages', mechanism)
    actions = Actions(mechanism.allows)
    actions.register(Action('market.purchase', ('market', 'order'), '购买订单中的商品', {
        'type': 'object', 'properties': {'quantity': {'type': 'integer', 'minimum': 1}},
        'required': ['quantity'], 'additionalProperties': False,
    }, mechanism.purchase, terminal=True,
        available=lambda scope, target: target.key in mechanism.orders and mechanism.orders[target.key]['stock'] > 0))
    return mechanism, information, actions


@pytest.mark.asyncio
async def test_shared_market_action_produces_message_for_another_actor():
    env, info, actions = setup_market()
    target = Ref('market', 'order', 'one')
    with InteractionScope('alice', Moment(0, 'trade')) as alice:
        action_tools = actions.bound(alice)
        candidates = await action_tools.find(target)
        assert candidates.total == 1
        assert (await action_tools.describe('market.purchase', target)).parameters['required'] == ['quantity']
        result = await action_tools.invoke('market.purchase', target, {'quantity': 1})
        assert result.status == 'completed' and result.terminal
        assert env.orders['one']['stock'] == 1
    with InteractionScope('bob', Moment(0, 'trade')) as bob:
        messages = await info.bound(bob).read('/messages/bob', size=100)
        assert messages.data.decode() == 'alice购买1件'
        assert messages.revision == 1
        assert (await actions.bound(bob).find(target)).total == 0


@pytest.mark.asyncio
async def test_discover_read_and_invoke_are_independent():
    env, info, actions = setup_market()
    target = Ref('market', 'order', 'one')
    with InteractionScope('bob', Moment(0, 'trade')) as scope:
        assert (await info.list(scope, '/market')).total == 1
        with pytest.raises(Unavailable):
            await info.read(scope, '/market/one')
        assert (await actions.find(scope, target)).total == 0
        with pytest.raises(Unavailable):
            await actions.describe(scope, 'market.purchase', target)
        result = await actions.invoke(scope, 'market.purchase', target, {'quantity': 1})
        assert result.status == 'rejected'
    assert env.orders['one']['stock'] == 2
    assert not any(call[0] == 'read' for call in env.calls)


@pytest.mark.asyncio
async def test_bound_tools_do_not_accept_model_supplied_actor():
    _, info, actions = setup_market()
    with InteractionScope('bob', Moment(0, 'trade')) as scope:
        with pytest.raises(TypeError):
            await actions.bound(scope).invoke('market.purchase', Ref('market', 'order', 'one'),
                                             {'quantity': 1}, actor='alice')
        with pytest.raises(TypeError):
            await info.bound(scope).read('/market/one', actor='alice')
        with pytest.raises(FrozenInstanceError):
            scope.actor = 'alice'
        with pytest.raises(FrozenInstanceError):
            scope.moment = Moment(1, 'trade')


@pytest.mark.asyncio
async def test_expired_scope_stops_before_any_provider_or_action():
    env, info, actions = setup_market()
    scope = InteractionScope('alice', Moment(0, 'trade'))
    read_tools, action_tools = info.bound(scope), actions.bound(scope)
    scope.close()
    with pytest.raises(ScopeClosed):
        await read_tools.read('/market/one')
    with pytest.raises(ScopeClosed):
        await action_tools.invoke('market.purchase', Ref('market', 'order', 'one'), {'quantity': 1})
    assert env.calls == [] and env.orders['one']['stock'] == 2


@pytest.mark.asyncio
async def test_document_range_reads_reassemble_all_original_utf8_bytes():
    _, info, _ = setup_market()
    with InteractionScope('alice', Moment(0, 'trade')) as scope:
        offset, blocks = 0, []
        while True:
            block = await info.read(scope, '/market/one', offset=offset, size=5)
            assert len(block.data) <= 5 and block.source == Ref('market', 'order', 'one')
            blocks.append(block.data)
            if block.next_offset is None:
                break
            offset = block.next_offset
    assert b''.join(blocks).decode() == '价格12元；完整说明🙂'


@pytest.mark.asyncio
async def test_provider_owns_authorized_total_cursor_and_query_execution():
    class LazyProvider:
        def __init__(self):
            self.calls = []
        def ref(self, path):
            return Ref('large', 'dataset', path)
        def list(self, scope, path, *, limit, cursor):
            self.calls.append((scope.actor, limit, cursor))
            start = cursor or 0
            return Page(list(range(start, start + limit)), 10**9, start + limit, 'v1')
        def query(self, scope, path, query):
            self.calls.append(query)
            return Page([{'id': 900000001}], 1, None, 'v1')
    provider = LazyProvider()
    info = Information(lambda scope, op, ref: True)
    info.mount('/large', provider)
    assert provider.calls == []
    with InteractionScope('alice', Moment(0, 'read')) as scope:
        first = await info.list(scope, '/large', limit=3)
        second = await info.list(scope, '/large', limit=3, cursor=first.next_cursor)
        query = Query(fields=('id',), filters=(('id', 'ge', 900000000),), limit=1, sample_seed=42)
        selected = await info.query(scope, '/large', query)
    assert first.items == [0, 1, 2] and first.total == 10**9
    assert second.items == [3, 4, 5] and selected.total == 1
    assert provider.calls == [('alice', 3, None), ('alice', 3, 3), query]


@pytest.mark.asyncio
async def test_mount_resolution_uses_path_segments_and_longest_prefix():
    class Provider:
        def __init__(self, name): self.name = name
        def ref(self, path): return Ref(self.name, 'doc', path)
        def read(self, scope, path, *, offset, size):
            return DocumentChunk(self.name.encode(), len(self.name), None, 0, self.ref(path))
    info = Information(lambda *args: True)
    info.mount('/market', Provider('root'))
    info.mount('/market/reports', Provider('child'))
    with InteractionScope('alice', Moment(0, 'read')) as scope:
        assert (await info.read(scope, '/market/reports/today')).data == b'child'
        with pytest.raises(Unavailable):
            await info.read(scope, '/market-private/one')
    with pytest.raises(ValueError):
        info.mount('/market', Provider('duplicate'))


@pytest.mark.asyncio
async def test_action_registry_is_type_sized_and_rechecks_current_conditions():
    env, _, actions = setup_market()
    target = Ref('market', 'order', 'one')
    with InteractionScope('alice', Moment(0, 'trade')) as scope:
        assert (await actions.find(scope, target)).total == 1
        env.orders['one']['stock'] = 0
        result = await actions.invoke(scope, 'market.purchase', target, {'quantity': 1})
        assert result.status == 'rejected' and not result.terminal
        assert (await actions.find(scope, target)).total == 0
        assert not env.messages['bob']
    assert len(actions) == 1


@pytest.mark.asyncio
async def test_action_schema_checked_at_registration_and_before_handler():
    from jsonschema.exceptions import SchemaError
    called = []
    actions = Actions(lambda *args: True)
    with pytest.raises(SchemaError):
        actions.register(Action('bad', ('m', 'order'), 'bad', {'type': 'not-a-json-type'},
                                lambda *args: called.append(1)))
    actions.register(Action('good', ('m', 'order'), 'good', {
        'type': 'object', 'properties': {'q': {'type': 'integer'}},
        'required': ['q'], 'additionalProperties': False,
    }, lambda *args: called.append(1)))
    with InteractionScope('alice', Moment(0, 'trade')) as scope:
        for args in [{}, {'q': True}, {'q': 1, 'actor': 'bob'}]:
            result = await actions.invoke(scope, 'good', Ref('m', 'order', '1'), args)
            assert result.status == 'rejected' and result.value['reason'] == 'invalid_arguments'
    assert called == []


@pytest.mark.asyncio
@pytest.mark.parametrize('status, terminal', [('accepted', False), ('rejected', False), ('completed', True)])
async def test_terminal_requires_completed_registry_action(status, terminal):
    actions = Actions(lambda *args: True)
    actions.register(Action('work', ('m', 'job'), 'work', {'type': 'object'},
                            lambda *args: ActionResult(status, process=Ref('m', 'process', 'p'), terminal=True),
                            terminal=True))
    with InteractionScope('alice', Moment(0, 'act')) as scope:
        result = await actions.invoke(scope, 'work', Ref('m', 'job', 'j'), {})
    assert result.terminal is terminal
    assert result.process == Ref('m', 'process', 'p')


@pytest.mark.asyncio
async def test_nonterminal_handler_cannot_end_activation_and_failure_propagates():
    actions = Actions(lambda *args: True)
    actions.register(Action('normal', ('m', 'job'), 'normal', {'type': 'object'},
                            lambda *args: ActionResult('completed', terminal=True)))
    async def broken(*args):
        await asyncio.sleep(0)
        raise RuntimeError('partial world write')
    actions.register(Action('broken', ('m', 'job'), 'broken', {'type': 'object'}, broken))
    with InteractionScope('alice', Moment(0, 'act')) as scope:
        assert not (await actions.invoke(scope, 'normal', Ref('m', 'job', 'j'), {})).terminal
        with pytest.raises(RuntimeError, match='partial world write'):
            await actions.invoke(scope, 'broken', Ref('m', 'job', 'j'), {})


@pytest.mark.asyncio
async def test_action_pages_are_bound_to_type_target_scope_and_registration():
    actions = Actions(lambda *args: True)
    handler = lambda *args: ActionResult('completed')
    for name in ['a', 'b', 'c']:
        actions.register(Action(name, ('m', 'job'), name, {'type': 'object'}, handler))
    target = Ref('m', 'job', 'one')
    with InteractionScope('alice', Moment(0, 'act'), revision='v1') as scope:
        first = await actions.find(scope, target, limit=1)
        second = await actions.find(scope, target, limit=1, cursor=first.next_cursor)
        assert first.total == 3 and first.items[0].name == 'a' and second.items[0].name == 'b'
        with pytest.raises(ValueError, match='cursor'):
            await actions.find(scope, Ref('m', 'job', 'two'), cursor=first.next_cursor)
        with InteractionScope('bob', Moment(0, 'act'), revision='v1') as other:
            with pytest.raises(ValueError, match='cursor'):
                await actions.find(other, target, cursor=first.next_cursor)
        assert (await actions.find(scope, Ref('other', 'job', 'one'))).total == 0


@pytest.mark.asyncio
async def test_same_moment_new_scope_reads_new_version_and_investigation_is_action():
    env, info, actions = setup_market()
    costs = []
    actions.register(Action('market.investigate', ('market', 'order'), '付费调查', {'type': 'object'},
        lambda scope, target, args: (costs.append(3) or ActionResult('accepted', process=Ref('market', 'investigation', 'i')))))
    moment = Moment(5, 'trade')
    with InteractionScope('alice', moment) as first:
        before = await info.read(first, '/market/one')
        assert costs == []
        result = await actions.invoke(first, 'market.investigate', Ref('market', 'order', 'one'), {})
        assert result.status == 'accepted' and costs == [3]
    env.revision += 1
    with InteractionScope('alice', moment) as second:
        after = await info.read(second, '/market/one')
    assert after.revision == before.revision + 1
    assert moment.time == 5


@pytest.mark.asyncio
async def test_registered_schema_and_descriptions_do_not_share_mutable_aliases():
    parameters = {'type': 'object', 'required': ['quantity']}
    actions = Actions(lambda *args: True)
    actions.register(Action('buy', ('m', 'order'), 'buy', parameters,
                            lambda *args: ActionResult('completed')))
    parameters['required'].clear()
    with InteractionScope('alice', Moment(0, 'trade')) as scope:
        description = await actions.describe(scope, 'buy', Ref('m', 'order', '1'))
        description.parameters['required'].clear()
        result = await actions.invoke(scope, 'buy', Ref('m', 'order', '1'), {})
    assert result.status == 'rejected'


@pytest.mark.asyncio
async def test_scope_closed_during_async_permission_does_not_start_handler():
    calls = []
    async def allows(scope, operation, ref):
        await asyncio.sleep(0)
        scope.close()
        return True
    actions = Actions(allows)
    actions.register(Action('run', ('m', 'job'), 'run', {},
                            lambda *args: calls.append('handler')))
    scope = InteractionScope('alice', Moment(0, 'work'))
    with pytest.raises(ScopeClosed):
        await actions.invoke(scope, 'run', Ref('m', 'job', '1'), {})
    assert calls == []


@pytest.mark.asyncio
async def test_unknown_and_denied_action_have_same_public_result():
    _, _, actions = setup_market()
    with InteractionScope('bob', Moment(0, 'trade')) as scope:
        denied = await actions.invoke(scope, 'market.purchase', Ref('market', 'order', 'one'), {})
        unknown = await actions.invoke(scope, 'missing', Ref('market', 'order', 'one'), {})
    assert denied == unknown == ActionResult('rejected', {'reason': 'unavailable'})


@pytest.mark.asyncio
async def test_async_information_provider_returns_its_actual_revision():
    class AsyncProvider:
        def ref(self, path): return Ref('m', 'doc', path)
        async def read(self, scope, path, *, offset, size):
            await asyncio.sleep(0)
            return DocumentChunk(b'hello'[offset:offset + size], 5, None, 'actual-v2', self.ref(path))
    async def allows(*args): return True
    info = Information(allows)
    info.mount('/m', AsyncProvider())
    with InteractionScope('alice', Moment(0, 'read')) as scope:
        chunk = await info.read(scope, '/m/one')
    assert chunk.data == b'hello' and chunk.revision == 'actual-v2'


@pytest.mark.asyncio
async def test_action_page_detects_changed_candidate_set_in_current_view():
    enabled = {'a': True, 'b': True, 'c': True}
    actions = Actions(lambda *args: True)
    for name in enabled:
        actions.register(Action(name, ('m', 'job'), name, {},
                                lambda *args: ActionResult('completed'),
                                available=lambda scope, target, name=name: enabled[name]))
    with InteractionScope('alice', Moment(0, 'act')) as scope:
        first = await actions.find(scope, Ref('m', 'job', '1'), limit=1)
        enabled['a'] = False
        with pytest.raises(ValueError, match='cursor'):
            await actions.find(scope, Ref('m', 'job', '1'), limit=1, cursor=first.next_cursor)


@pytest.mark.asyncio
async def test_action_discovery_is_light_and_cursor_survives_json_roundtrip():
    import json
    actions = Actions(lambda *args: True)
    for name in ['a', 'b']:
        actions.register(Action(name, ('m', 'job'), name, {'description': 'large schema'},
                                lambda *args: ActionResult('completed')))
    with InteractionScope('alice', Moment(0, 'act')) as scope:
        target = Ref('m', 'job', '1')
        first = await actions.find(scope, target, limit=1)
        assert not hasattr(first.items[0], 'parameters')
        cursor = json.loads(json.dumps(first.next_cursor))
        assert (await actions.find(scope, target, cursor=cursor)).items[0].name == 'b'
        with pytest.raises(ValueError, match='cursor'):
            await actions.find(scope, target, query='b', cursor=cursor)
        actions.register(Action('c', ('m', 'job'), 'c', {}, lambda *args: ActionResult('completed')))
        with pytest.raises(ValueError, match='cursor'):
            await actions.find(scope, target, cursor=cursor)


@pytest.mark.asyncio
async def test_action_cursor_binds_actual_revision_with_current_scope():
    version = [1]
    actions = Actions(lambda *args: True, revision=lambda scope: version[0])
    for name in ['a', 'b']:
        actions.register(Action(name, ('m', 'job'), name, {}, lambda *args: ActionResult('completed')))
    with InteractionScope('alice', Moment(0, 'act')) as scope:
        first = await actions.find(scope, Ref('m', 'job', '1'), limit=1)
        assert first.revision == 1
        version[0] = 2
        with pytest.raises(ValueError, match='cursor'):
            await actions.find(scope, Ref('m', 'job', '1'), cursor=first.next_cursor)


@pytest.mark.asyncio
async def test_information_closed_during_provider_does_not_return_result():
    class Provider:
        def ref(self, path): return Ref('m', 'doc', path)
        async def read(self, scope, path, *, offset, size):
            await asyncio.sleep(0)
            scope.close()
            return DocumentChunk(b'secret', 6, None, 1, self.ref(path))
    info = Information(lambda *args: True)
    info.mount('/m', Provider())
    scope = InteractionScope('alice', Moment(0, 'read'))
    with pytest.raises(ScopeClosed):
        await info.read(scope, '/m/one')


@pytest.mark.asyncio
async def test_discovery_rejects_revision_change_during_async_candidate_check():
    version = [1]
    async def available(scope, target):
        await asyncio.sleep(0)
        version[0] += 1
        return True
    actions = Actions(lambda *args: True, revision=lambda scope: version[0])
    actions.register(Action('a', ('m', 'job'), 'a', {},
                            lambda *args: ActionResult('completed'), available=available))
    with InteractionScope('alice', Moment(0, 'act')) as scope:
        with pytest.raises(ValueError, match='revision'):
            await actions.find(scope, Ref('m', 'job', '1'))


@pytest.mark.asyncio
async def test_root_listing_discovers_only_authorized_mounts_with_refs():
    env, info, _ = setup_market()
    with InteractionScope('alice', Moment(0, 'read')) as scope:
        page = await info.list(scope, '/', limit=1)
        assert page.total == 2 and page.items[0]['path'] == '/market'
        assert page.items[0]['ref'] == env.ref('/market')
        next_page = await info.list(scope, '/', limit=1, cursor=page.next_cursor)
        assert next_page.items[0]['path'] == '/messages'


@pytest.mark.asyncio
async def test_action_metadata_tags_frozen_and_strict_nullable_normalization():
    tags = ['trade']
    observed = []
    schema = {'type': 'object', 'properties': {
        'note': {'type': ['string', 'null']},
        'code': {'type': ['string', 'null'], 'enum': ['null', None]},
    }, 'required': ['note', 'code'], 'additionalProperties': False}
    action = Action('strict', ('m', 'job'), 'strict', schema,
                    lambda scope, target, args: observed.append(args) or ActionResult('completed'),
                    tags=tags, strict=True, read_only=True)
    tags.append('leak')
    actions = Actions(lambda *args: True)
    actions.register(action)
    with InteractionScope('a', Moment(0, 'p')) as scope:
        target = Ref('m', 'job', '1')
        summary = (await actions.find(scope, target)).items[0]
        assert summary.tags == ('trade',)
        description = await actions.describe(scope, 'strict', target)
        assert description.tags == ('trade',) and description.strict and description.read_only
        args = {'note': ' None ', 'code': 'null'}
        assert (await actions.invoke(scope, 'strict', target, args)).status == 'completed'
        assert args == {'note': ' None ', 'code': 'null'}
        assert (await actions.invoke(scope, 'strict', target, {})).status == 'rejected'
    assert observed == [{'note': None, 'code': 'null'}]


def test_strict_action_requires_explicit_strict_schema():
    actions = Actions(lambda *args: True)
    with pytest.raises(ValueError, match='strict'):
        actions.register(Action('strict', ('m', 'job'), '', {'type': 'object'}, lambda *args: None, strict=True))
