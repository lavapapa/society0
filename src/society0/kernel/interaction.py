"""共享环境的信息路由与动作模板；数据查询由机制提供者执行。"""
from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import asdict, dataclass, field, replace
from inspect import isawaitable
from typing import Any, Callable

from jsonschema.validators import validator_for

from ..function_registry import validate_strict_function_parameters


class ScopeClosed(RuntimeError):
    """交互所属的激活作用域已经结束。"""


class Unavailable(LookupError):
    """资源不存在或当前主体不可访问。"""


@dataclass(frozen=True)
class Ref:
    namespace: str
    kind: str
    key: str


@dataclass(frozen=True)
class Moment:
    time: Any
    phase: str


@dataclass(frozen=True)
class InteractionScope:
    actor: str
    moment: Moment
    revision: Any = None
    _active: bool = field(default=True, init=False, repr=False, compare=False)

    def check_active(self):
        if not self._active:
            raise ScopeClosed('interaction scope is closed')

    def close(self):
        object.__setattr__(self, '_active', False)

    def __enter__(self):
        self.check_active()
        return self

    def __exit__(self, *exc):
        self.close()


@dataclass(frozen=True)
class Page:
    items: list
    total: int
    next_cursor: Any
    revision: Any


@dataclass(frozen=True)
class DocumentChunk:
    data: bytes
    total_bytes: int
    next_offset: int | None
    revision: Any
    source: Ref


@dataclass(frozen=True)
class ResourceStat:
    kind: str
    total_bytes: int | None
    revision: Any
    source: Ref


@dataclass(frozen=True)
class Query:
    fields: tuple = ()
    filters: tuple = ()
    order: tuple = ()
    limit: int = 100
    cursor: Any = None
    sample_seed: int | None = None
    max_bytes: int = 65536


async def _resolve(value):
    return await value if isawaitable(value) else value


def _path(path):
    if not path.startswith('/') or any(p in ('.', '..') for p in path.split('/')):
        raise ValueError('an absolute resource path without dot segments is required')
    return '/' + '/'.join(p for p in path.split('/') if p)


def _cursor_offset(cursor):
    offset = cursor['offset']
    if type(offset) is not int or offset < 0:
        raise ValueError('cursor offset must be a nonnegative integer')
    return offset


class Information:
    def __init__(self, allows: Callable, *, access_dependencies=()):
        self._allows = allows
        self._mounts = {}
        self.access_dependencies=tuple(access_dependencies)

    def mount(self, prefix, provider):
        prefix = _path(prefix)
        if prefix in self._mounts:
            raise ValueError('resource prefix is already mounted')
        bind=getattr(provider,'bind_access_dependencies',None)
        if bind is not None:bind(self.access_dependencies)
        self._mounts[prefix] = provider

    async def _provider(self, scope, path, operation):
        scope.check_active()
        path = _path(path)
        parts = path.strip('/').split('/')
        for count in range(len(parts), -1, -1):
            prefix = '/' + '/'.join(parts[:count])
            provider = self._mounts.get(prefix)
            if provider is not None:
                ref = provider.ref(path)
                if not await _resolve(self._allows(scope, operation, ref)):
                    raise Unavailable('resource unavailable')
                scope.check_active()
                return provider, path
        raise Unavailable('resource unavailable')

    async def list(self, scope, path, *, limit=100, cursor=None):
        if limit < 1:
            raise ValueError('limit must be positive')
        if _path(path) == '/' and '/' not in self._mounts:
            scope.check_active()
            items = []
            for prefix, provider in self._mounts.items():
                ref = provider.ref(prefix)
                if await _resolve(self._allows(scope, 'discover', ref)):
                    items.append({'path': prefix, 'ref': ref})
                scope.check_active()
            identity = json.dumps([scope.actor, asdict(scope.moment), scope.revision,
                                   [item['path'] for item in items]], sort_keys=True)
            offset = 0
            if cursor is not None:
                if cursor['identity'] != identity:
                    raise ValueError('mount cursor mismatch')
                offset = _cursor_offset(cursor)
            end = min(offset + limit, len(items))
            next_cursor = {'identity': identity, 'offset': end} if end < len(items) else None
            return Page(items[offset:end], len(items), next_cursor, scope.revision)
        provider, path = await self._provider(scope, path, 'discover')
        result = await _resolve(provider.list(scope, path, limit=limit, cursor=cursor))
        scope.check_active()
        return result

    async def list_files(self,scope,path,*,limit=100,cursor=None):
        if _path(path)=='/' and '/' not in self._mounts:
            page=await self.list(scope,path,limit=limit,cursor=cursor)
            return Page([dict(item,kind='directory') for item in page.items],page.total,page.next_cursor,page.revision)
        provider,path=await self._provider(scope,path,'discover')
        result=await _resolve(provider.list_files(scope,path,limit=limit,cursor=cursor))
        scope.check_active();return result

    async def stat(self, scope, path):
        if _path(path)=='/' and '/' not in self._mounts:
            scope.check_active()
            return ResourceStat('directory',None,scope.revision,Ref('','directory',''))
        provider,path=await self._provider(scope,path,'discover')
        result=await _resolve(provider.stat(scope,path))
        if result.kind=='file' and not await _resolve(self._allows(scope,'read',result.source)):
            raise Unavailable('resource unavailable')
        scope.check_active()
        return result

    async def read(self, scope, path, *, offset=0, size=65536, expected_revision=None):
        if offset < 0 or size < 1:
            raise ValueError('offset must be nonnegative and size positive')
        provider, path = await self._provider(scope, path, 'read')
        result = await _resolve(provider.read(scope, path, offset=offset, size=size))
        scope.check_active()
        if expected_revision is not None and json.dumps(result.revision,sort_keys=True)!=json.dumps(expected_revision,sort_keys=True):
            raise ValueError('document revision changed; locate the document again')
        return result

    async def query(self, scope, path, query: Query):
        if query.limit < 1:
            raise ValueError('limit must be positive')
        provider, path = await self._provider(scope, path, 'read')
        result = await _resolve(provider.query(scope, path, query))
        scope.check_active()
        return result

    def bound(self, scope):
        return _BoundInformation(self, scope)


@dataclass(frozen=True)
class _BoundInformation:
    information: Information
    scope: InteractionScope

    async def list(self, path, *, limit=100, cursor=None):
        return await self.information.list(self.scope, path, limit=limit, cursor=cursor)

    async def list_files(self,path,*,limit=100,cursor=None):
        return await self.information.list_files(self.scope,path,limit=limit,cursor=cursor)

    async def stat(self,path):
        return await self.information.stat(self.scope,path)

    async def read(self, path, *, offset=0, size=65536, expected_revision=None):
        return await self.information.read(self.scope, path, offset=offset, size=size, expected_revision=expected_revision)

    async def query(self, path, query):
        return await self.information.query(self.scope, path, query)


@dataclass(frozen=True)
class Action:
    name: str
    target_kind: tuple[str, str]
    description: str
    parameters: dict
    handler: Callable
    terminal: bool = False
    available: Callable | None = None
    tags: tuple[str, ...] = ()
    strict: bool = False
    read_only: bool = False

    def __post_init__(self):
        object.__setattr__(self, 'tags', tuple(self.tags))


@dataclass(frozen=True)
class ActionSummary:
    name: str
    description: str
    terminal: bool
    tags: tuple[str, ...] = ()


@dataclass(frozen=True)
class ActionDescription:
    name: str
    target_kind: tuple[str, str]
    description: str
    parameters: dict
    terminal: bool
    tags: tuple[str, ...] = ()
    strict: bool = False
    read_only: bool = False


@dataclass(frozen=True)
class ActionResult:
    status: str
    value: Any = None
    process: Ref | None = None
    terminal: bool = False

    def __post_init__(self):
        if self.status not in ('completed', 'accepted', 'rejected'):
            raise ValueError('unknown action status')


def _normalize_strict_value(value, schema):
    """保留原 ActionSet 的严格工具 nullable 字符串合同，不放宽 schema。"""
    if not isinstance(schema, dict):
        return value
    if isinstance(value, dict):
        properties = schema.get('properties', {})
        return {key: _normalize_strict_value(item, properties.get(key))
                for key, item in value.items()}
    if isinstance(value, list):
        return [_normalize_strict_value(item, schema.get('items'))
                if isinstance(item, (dict, list)) else item for item in value]
    types = schema.get('type', [])
    types = [types] if isinstance(types, str) else types
    enum = schema.get('enum')
    nullable = 'null' in types or (isinstance(enum, list) and None in enum)
    if isinstance(value, str) and nullable:
        explicit = isinstance(enum, list) and value in enum
        if value.strip().casefold() in ('null', 'none') and not explicit:
            return None
        minimum = schema.get('minLength', 0)
        accepts_string = ('string' in types and (not isinstance(enum, list) or value in enum)
                          and not isinstance(minimum, bool) and len(value) >= int(minimum))
        if not value.strip() and not accepts_string:
            return None
    return value


class Actions:
    def __init__(self, allows: Callable, *, revision: Callable | None = None, dependency_revision=None, access_dependencies=()):
        self._allows = allows
        self._revision = revision
        self._actions = {}
        self._types = {}
        self._generation = 0
        self._dependency_revision=dependency_revision
        self._access_dependencies=tuple(access_dependencies)
        self._dependencies={}

    def __len__(self):
        return len(self._actions)

    def register(self, action: Action, *, dependencies=()):
        if action.name in self._actions:
            raise ValueError('action name is already registered')
        parameters = deepcopy(action.parameters)
        validator_type = validator_for(parameters)
        validator_type.check_schema(parameters)
        if action.strict:
            validate_strict_function_parameters(parameters)
        action = replace(action, parameters=parameters, target_kind=tuple(action.target_kind))
        self._actions[action.name] = (action, validator_type(parameters))
        self._types.setdefault(action.target_kind, []).append(action.name)
        self._dependencies.setdefault(action.target_kind,set()).update(dependencies)
        self._generation += 1

    async def _eligible(self, scope, action, target):
        scope.check_active()
        if action is None or action.target_kind != (target.namespace, target.kind):
            return False
        for operation in ('discover', 'invoke'):
            if not await _resolve(self._allows(scope, operation, target)):
                return False
            scope.check_active()
        if action.available is not None:
            available = await _resolve(action.available(scope, target))
            scope.check_active()
            return bool(available)
        return True

    @staticmethod
    def _description(action):
        return ActionDescription(action.name, action.target_kind, action.description,
                                 deepcopy(action.parameters), action.terminal, action.tags, action.strict, action.read_only)

    async def find(self, scope, target, *, query='', limit=100, cursor=None, names=None, tags=None):
        scope.check_active()
        if limit < 1:
            raise ValueError('limit must be positive')
        async def version():
            if self._dependency_revision is not None:
                tables=(*self._access_dependencies,*sorted(self._dependencies.get((target.namespace,target.kind),())))
                return await _resolve(self._dependency_revision(scope,tables))
            return await _resolve(self._revision(scope)) if self._revision else scope.revision
        names = None if names is None else frozenset(names)
        tags = None if tags is None else frozenset(tags)
        revision = await version()
        scope.check_active()
        identity = json.dumps([scope.actor, asdict(scope.moment), revision, asdict(target),
                               query, self._generation, None if names is None else sorted(names),
                               None if tags is None else sorted(tags)], ensure_ascii=False, sort_keys=True)
        offset = 0
        if cursor is not None:
            if cursor['identity'] != identity:
                raise ValueError('action cursor mismatch')
            offset = _cursor_offset(cursor)
        # 模板数量随类型能力增长，不展开每个对象的动作组合。
        selected = []
        total = 0
        candidates = []
        for name in self._types.get((target.namespace, target.kind), ()):
            action = self._actions[name][0]
            if names is not None and name not in names:
                continue
            if tags is not None and not tags.intersection(action.tags):
                continue
            if query.casefold() not in (name + ' ' + action.description).casefold():
                continue
            if await self._eligible(scope, action, target):
                if revision is None:
                    candidates.append(name)
                if offset <= total < offset + limit:
                    selected.append(ActionSummary(action.name, action.description, action.terminal, action.tags))
                total += 1
        if self._revision is not None or self._dependency_revision is not None:
            current = await version()
            scope.check_active()
            if json.dumps(current, sort_keys=True) != json.dumps(revision, sort_keys=True):
                raise ValueError('action discovery revision changed; restart discovery')
        if revision is None and cursor is not None and cursor['candidates'] != candidates:
            raise ValueError('action cursor candidates changed; restart discovery')
        end = offset + len(selected)
        next_cursor = {'identity': identity, 'offset': end} if end < total else None
        if next_cursor is not None and revision is None:
            next_cursor['candidates'] = candidates
        return Page(selected, total, next_cursor, revision)

    async def describe(self, scope, name, target):
        registered = self._actions.get(name)
        action = registered[0] if registered else None
        if not await self._eligible(scope, action, target):
            raise Unavailable('action unavailable')
        return self._description(action)

    async def invoke(self, scope, name, target, arguments):
        registered = self._actions.get(name)
        action = registered[0] if registered else None
        if not await self._eligible(scope, action, target):
            return ActionResult('rejected', {'reason': 'unavailable'})
        if action.strict:
            arguments = _normalize_strict_value(arguments, action.parameters)
        if not registered[1].is_valid(arguments):
            return ActionResult('rejected', {'reason': 'invalid_arguments'})
        scope.check_active()
        result = await _resolve(action.handler(scope, target, arguments))
        if not isinstance(result, ActionResult):
            raise TypeError('action handler must return ActionResult')
        return replace(result, terminal=action.terminal and result.status == 'completed')

    def bound(self, scope):
        return _BoundActions(self, scope)


@dataclass(frozen=True)
class _BoundActions:
    actions: Actions
    scope: InteractionScope

    async def find(self, target, *, query='', limit=100, cursor=None, names=None, tags=None):
        return await self.actions.find(self.scope, target, query=query, limit=limit, cursor=cursor, names=names, tags=tags)

    async def describe(self, name, target):
        return await self.actions.describe(self.scope, name, target)

    async def invoke(self, name, target, arguments):
        return await self.actions.invoke(self.scope, name, target, arguments)


def interaction_plugin(allows, *, name='interaction', storage='storage', access_dependencies=()):
    """为一个共享运行安装信息与行动服务，领域插件注册其自身内容。"""
    from .plugins import Plugin
    def install(context):
        store=context.require(storage,'store')
        context.provide('information',Information(allows,access_dependencies=access_dependencies))
        context.provide('actions',Actions(allows,access_dependencies=access_dependencies,
            dependency_revision=lambda scope,tables:store.read(lambda r:[r.run_id,r.revision_for(tables)],expected_revision=scope.revision)))
    return Plugin(name,(storage,),install)
