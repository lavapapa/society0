"""非作者验证交互分页与动态条件。"""
import json

import pytest

from society0.kernel.interaction import Action, ActionResult, Actions, Information, InteractionScope, Moment, Page, Ref, Unavailable


class Directory:
    def __init__(self, namespace):
        self.namespace = namespace
        self.paths = []
    def ref(self, path):
        return Ref(self.namespace, 'directory', path)
    def list(self, scope, path, *, limit, cursor):
        self.paths.append(path)
        return Page([], 0, None, 0)


def action_service(available=lambda *a: True):
    service = Actions(lambda *a: True)
    for name in ('a', 'b', 'c'):
        service.register(Action(name, ('m', 'thing'), name, {},
                                lambda *a: ActionResult('completed'), available=available))
    return service


@pytest.mark.asyncio
async def test_review_json_cursor_roundtrip_cross_scope_and_dynamic_candidates():
    allowed = True
    service = action_service(lambda *a: allowed)
    scope = InteractionScope('alice', Moment(0, 'p'))
    target = Ref('m', 'thing', 'x')
    first = await service.find(scope, target, limit=1)
    cursor = json.loads(json.dumps(first.next_cursor))
    second = await service.find(scope, target, limit=1, cursor=cursor)
    assert first.total == second.total == 3
    assert first.items[0].name == 'a' and second.items[0].name == 'b'
    with pytest.raises(ValueError, match='cursor'):
        await service.find(InteractionScope('bob', Moment(0, 'p')), target, limit=1, cursor=cursor)
    allowed = False
    with pytest.raises(ValueError, match='candidates'):
        await service.find(scope, target, limit=1, cursor=cursor)
    assert (await service.invoke(scope, 'a', target, {})).status == 'rejected'


@pytest.mark.asyncio
async def test_review_mount_directory_boundary_and_authorized_total():
    a, b = Directory('visible'), Directory('hidden')
    service = Information(lambda scope, operation, ref: ref.namespace == 'visible')
    service.mount('/market', a)
    service.mount('/private', b)
    scope = InteractionScope('alice', Moment(0, 'p'))
    root = await service.list(scope, '/')
    assert root.total == 1 and root.items[0]['path'] == '/market'
    with pytest.raises(Unavailable):
        await service.list(scope, '/marketplace')
    with pytest.raises(Unavailable):
        await service.list(scope, '/private')
    assert a.paths == b.paths == []
    await service.list(scope, '/market/child')
    assert a.paths == ['/market/child']


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['actions', 'root'])
async def test_review_negative_cursor_is_rejected_instead_of_empty_nonprogress_page(kind):
    scope = InteractionScope('alice', Moment(0, 'p'))
    if kind == 'actions':
        service = action_service()
        async def page(cursor=None):
            return await service.find(scope, Ref('m', 'thing', 'x'), limit=1, cursor=cursor)
    else:
        service = Information(lambda *a: True)
        service.mount('/a', Directory('a'))
        service.mount('/b', Directory('b'))
        async def page(cursor=None):
            return await service.list(scope, '/', limit=1, cursor=cursor)
    cursor = json.loads(json.dumps((await page()).next_cursor))
    cursor['offset'] = -1
    with pytest.raises(ValueError, match='cursor|offset'):
        await page(cursor)
