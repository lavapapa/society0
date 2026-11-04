"""完整 Thread 上的模型决定循环与实际领域行动账本。"""
from __future__ import annotations

from collections import Counter
from contextvars import ContextVar
from contextlib import contextmanager, AsyncExitStack
from dataclasses import asdict, dataclass, is_dataclass, field
import json
import math
import re
import base64
import codecs
import asyncio
import time
from pathlib import Path
from typing import Any

from jsonschema.validators import validator_for

from ..async_utils import invoke_maybe_async
from ..function_registry import normalize_strict_function_parameters
from .interaction import ActionResult, Page, Query, Ref, Unavailable
from .cognition import InputBatch
from .models import ProviderFailure, ModelProvider
from .action_feedback import rejection_feedback, tool_feedback
from .request_budget import RequestBudget, current_request_budget, ActivationBudgetExhausted
from .runtime import DriverResult

current_action_call_id = ContextVar('kernel_action_call_id', default=None)


@dataclass(frozen=True)
class DecisionScope:
    """context.prepare() 之前确定的本次决策输入与资源，供召回等辅助请求原样复用。"""
    thread_id: str
    through: int
    provider: Any
    model_route: float
    endpoint: str
    tools: tuple
    lease: Any = None

    @property
    def tool_names(self):
        return tuple(tool['function']['name'] for tool in self.tools)


def _json(value):
    return json.dumps(value, ensure_ascii=False,
                      default=lambda obj: asdict(obj) if is_dataclass(obj) else str(obj))


class _ToolInputError(ValueError):
    pass


def _decode(value):
    try:
        return json.loads(value)
    except (ValueError, TypeError) as error:
        raise _ToolInputError(str(error)) from error


async def _read_tool(fn, *args, **kwargs):
    try:
        return await fn(*args, **kwargs)
    except (Unavailable, ValueError, TypeError, KeyError, FileNotFoundError, IsADirectoryError, NotADirectoryError, PermissionError) as error:
        return {'error': type(error).__name__, 'message': str(error)}


def _text_range(data, offset, total, encoding):
    if encoding == 'base64':
        text, consumed = base64.b64encode(data).decode('ascii'), len(data)
    else:
        decoder = codecs.getincrementaldecoder('utf-8')()
        try:
            text = decoder.decode(data, final=offset + len(data) == total)
        except UnicodeDecodeError as error:
            raise _ToolInputError('binary content requires encoding=base64') from error
        consumed = len(data) - len(decoder.getstate()[0])
    end = offset + consumed
    return {'data': text, 'encoding': encoding, 'total_bytes': total, 'next_offset': end if end < total else None}


_TOOL_DESCRIPTIONS = {
    'action_find': '查找当前目标可用的业务行动，返回行动名、用途和必填参数名及类型。query 按名称和说明做忽略大小写的字面子串搜索，空字符串分页列出全部；不支持语义搜索或通配符。按 next_cursor 续读。选定行动后先 action_describe，再 action_invoke；业务行动名仅放进 name 参数，勿当作直接工具名。',
    'action_describe': '查看一个或多个业务行动的完整参数 schema、说明和示例。批量形式 actions:["industry.resolve_agenda", "industry.update_plan"]，target 省略时使用当前主体上下文；也支持原单个 {name,target} 形式。调用任何行动之前，必须先用 action_describe 查看该行动的参数，再按返回的参数名调用 action_invoke；不要凭行动名猜参数。',
    'action_invoke': '执行一个业务行动，name 使用发现或描述返回的行动名，target 使用对应目标，arguments 严格按该行动 schema 填写。调用任何行动之前，必须先用 action_describe 查看该行动的参数，再按返回的参数名调用 action_invoke；不要凭行动名猜参数。返回 message 说明结果；accepted 为已受理、completed 为已完成。已保存的状态无需反复确认，判断完成后用 end_activation 或纯文字结束。',
    'read': '按绝对路径读取原始文件；先从 /context、/world、/workspace 或 /results 定位资料。offset 和 size 按字节计，默认 UTF-8，二进制用 base64。next_offset 非空时可继续读取，并沿用 expected_revision；读完所需事实后再决定是否调用业务行动。',
    'ls': '列出授权目录的直接子项，用于定位 read 要读取的文件；不读取文件正文。返回 total、revision 和 next_cursor，按该游标续读可取得全部子项。',
    'find': '在 path 下按 glob 模式查找原始逻辑文件路径，适合已知文件命名规律时使用；找到路径后用 read 读取。返回 total 和 next_cursor，按游标续读。传输分块和元数据不会重复计入原文件。',
    'grep': '按正则逐行搜索原始文本，literal=true 时按字面文本搜索，glob 用于筛选文件路径。返回行号、字节位置、完整匹配总数及持久 JSONL result_path；用 read 读取该结果文件可取得全部匹配。源文件改变时重新搜索。',
    'end_activation': '判断完成后结束本次激活，状态为 completed；也可直接以纯文字回复、不调用工具自然结束。可选 note 是本次小结，仅供审计，不进入记忆和后续简报。已保存的计划会自动执行，无需再次确认或保存；同一回复有其他工具调用时，先执行其他工具，再结束。',
    'bash': 'Run shell text with read-only /world, /context and /results files, data/action commands, and private /workspace files. With persistent workspace enabled, cwd is /workspace. Shell commands may materialize whole selected files; use read for byte ranges. Domain actions share the action budget. Complete outputs have persistent /results paths.',
    'submit_result': 'Submit the final structured result matching this schema. Success completes this measurement or decision.',
}


@dataclass(frozen=True)
class LLMPolicy:
    mode: str = 'decision'
    max_turns: int | None = None
    max_action_calls: int | None = None
    per_action_limits: dict = field(default_factory=dict)
    parallel_tool_calls: bool = False
    empty_retries: int = 1
    repeated_response_limit: int | None = None
    empty_retry_temperature_delta: float | None = None
    empty_retry_temperature_max: float = 1.0
    repeated_read_temperature_delta: float | None = None
    repeated_read_temperature_max: float = 1.0
    allowed_names: tuple | None = None
    allowed_tags: tuple | None = None
    required_names: tuple = ()
    required_tags: tuple = ()
    completion_names: tuple = ()
    completion_tags: tuple = ()
    strict_tools: bool = False
    result_schema: dict | None = None
    direct_json: bool = False
    request_options: dict = field(default_factory=dict)
    reasoning_stages: tuple = ()

    def __post_init__(self):
        for stage in self.reasoning_stages:
            if not isinstance(stage,dict) or not isinstance(stage.get('name'),str) or not stage['name'] or not isinstance(stage.get('desc',''),str):
                raise ValueError('reasoning stage requires name and optional desc strings')
        if self.mode not in ('decision', 'interview'):
            raise ValueError('mode must be decision or interview')
        for value in (self.max_turns, self.max_action_calls, self.repeated_response_limit, *self.per_action_limits.values()):
            if value is not None and (type(value) is not int or value < 0):
                raise ValueError('budgets must be nonnegative integers or None')
        if type(self.empty_retries) is not int or self.empty_retries < 0:
            raise ValueError('empty_retries must be nonnegative')
        for name in ('empty_retry_temperature_delta', 'empty_retry_temperature_max',
                     'repeated_read_temperature_delta', 'repeated_read_temperature_max'):
            value = getattr(self, name)
            if value is None and name.endswith('_delta'):
                continue
            if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
                raise ValueError(f'{name} must be a positive finite number')
        if self.direct_json and self.result_schema is None:
            raise ValueError('direct_json requires result_schema')


@contextmanager
def _timed(timings,name):
    started=time.perf_counter()
    try:yield
    finally:timings[name]=timings.get(name,0.)+time.perf_counter()-started


class _BudgetReached(RuntimeError):
    pass


class _Ledger:
    def __init__(self, driver, session, thread_id):
        self.driver, self.session, self.thread_id = driver, session, thread_id
        self.base = session.actions
        self.counts = Counter()
        self.completed = set()
        self.tags = set()
        self.terminal = False
        self.facts = set()
        # 本次激活已完整读取的事实；业务写入清空重复读取提示时仍保留消费凭据。
        session.cursors['read_facts'] = set()
        self.read_ranges = {}
        self.turn_repeated_read = False
        self.turn_progress = False
        self.feedback = None
        self.call_id = None
        self.shell_call = False
        self.shell_index = 0
        self.effects = []
        self.applied_receipts = set()
        self.ended = False

    @property
    def used(self):
        return sum(self.counts.values())

    def allowed(self, action):
        policy = self.driver.policy
        return ((policy.allowed_names is None or action.name in policy.allowed_names)
                and (policy.allowed_tags is None or bool(set(action.tags) & set(policy.allowed_tags))))

    async def find(self, target, *, query='', limit=100, cursor=None):
        identity=_json([self.session.actor.id,asdict(self.session.moment),
                        self.driver.policy.allowed_names,self.driver.policy.allowed_tags])
        if not _ACTION_CURSOR_VALIDATOR.is_valid(cursor):
            raise ValueError('Invalid action_find cursor. ' + _ACTION_CURSOR_HELP)
        if cursor is not None and cursor['identity']!=identity:
            raise ValueError('Action cursor belongs to a different activation. ' + _ACTION_CURSOR_HELP)
        try:
            page=await self.base.find(target,query=query,limit=limit,cursor=None if cursor is None else cursor['cursor'],
                                      names=self.driver.policy.allowed_names,tags=self.driver.policy.allowed_tags)
        except ValueError as error:
            if str(error).startswith('action cursor'):
                raise ValueError(str(error) + '. ' + _ACTION_CURSOR_HELP) from error
            raise
        continuation=None if page.next_cursor is None else {'identity':identity,'cursor':page.next_cursor}
        return Page(page.items,page.total,continuation,page.revision)

    async def describe(self, name, target=None):
        description = await self.base.describe(name, target)
        if not self.allowed(description):
            raise Unavailable('action outside activation selection')
        return description

    def preflight(self, names):
        policy = self.driver.policy
        extra = Counter(names)
        if policy.max_action_calls is not None and self.used + sum(extra.values()) > policy.max_action_calls:
            raise _BudgetReached('action_budget_exhausted')
        for name, amount in extra.items():
            maximum = policy.per_action_limits.get(name)
            if maximum is not None and self.counts[name] + amount > maximum:
                raise _BudgetReached('per_action_budget_exhausted')

    async def invoke(self, name, target, arguments):
        if self.terminal and self.requirements_met():
            return ActionResult('rejected', {'reason': 'activation_completed'}, message='本次激活已完成，无需继续调用行动。')
        self.preflight([name])
        self.counts[name] += 1
        identifier = self.call_id
        if self.shell_call:
            self.shell_index += 1
            identifier = f'{identifier}:{self.shell_index}'
        token = current_action_call_id.set(identifier)
        threads = self.driver.threads
        started=time.perf_counter()
        description=None
        try:
            start_seq=threads.start_action(self.thread_id, {'call_id': identifier, 'name': name,
                          'target': asdict(target), 'arguments': arguments})
            try:
                try:
                    description = await self.describe(name, target)
                except Unavailable:
                    description = None
                if description is None:
                    value = {'reason': 'unavailable'}
                    result = ActionResult('rejected', value, message=rejection_feedback(name, {}, arguments, value,
                        f'行动 {name} 或目标当前不可用；请先用 action_find 查询可用行动。'))
                else:
                    result = await self.base.invoke(name, target, arguments)
            except BaseException as error:
                threads.finish_action(self.thread_id,start_seq,{'call_id': identifier, 'name': name,
                              'error_type': type(error).__name__, 'error': str(error)},
                              status='cancelled' if isinstance(error,asyncio.CancelledError) else 'error',
                              tags=description.tags if description is not None else (),elapsed_s=time.perf_counter()-started)
                raise
            threads.finish_action(self.thread_id,start_seq,{'call_id': identifier, 'name': name,
                          'result': asdict(result)},status=result.status,
                          tags=description.tags if description is not None else (),elapsed_s=time.perf_counter()-started)
            effect = {'name': name, 'status': result.status,
                      'tags': list(description.tags) if description is not None else [],
                      'terminal': bool(result.terminal),
                      'read_only': bool(description and description.read_only),
                      'facts': result.value.get('facts', []) if isinstance(result.value, dict) else [],
                      'changed': result.value.get('changed', True) if isinstance(result.value, dict) else True}
            self.effects.append(effect)
            self.apply_effect(effect)
            return result
        finally:
            current_action_call_id.reset(token)

    def apply_effect(self, effect):
        self.feedback = None
        if effect['status'] != 'completed':
            return
        self.completed.add(effect['name'])
        self.tags.update(effect['tags'])
        policy = self.driver.policy
        self.terminal |= bool(effect['terminal'] or effect['name'] in policy.completion_names
                              or set(effect['tags']) & set(policy.completion_tags))
        if effect['read_only']:
            refs = {Ref(**item) for item in effect['facts']}
            repeated = refs & self.facts
            self.turn_progress |= bool(refs - self.facts)
            if refs:
                self.turn_repeated_read = not self.turn_progress
            self.facts.update(refs)
            self.session.cursors['read_facts'].update(refs)
            self.feedback = {'known_facts': [asdict(ref) for ref in sorted(self.facts, key=lambda r: (r.namespace, r.kind, r.key))],
                             'repeated_facts': [asdict(ref) for ref in sorted(repeated, key=lambda r: (r.namespace, r.kind, r.key))]}
        elif effect['changed']:
            self.turn_progress = True
            self.facts.clear()
            self.turn_repeated_read = False

    def read_effect(self, result, offset):
        """原生 read 的实际呈现区间；同一来源、版本全文覆盖后发布事实。"""
        source = result.get('source')
        if not isinstance(source, Ref):
            return
        identity = (source, _json(result['revision']), result['total_bytes'])
        end = result['next_offset']
        if end is None:
            end = result['total_bytes']
        ranges = sorted((*self.read_ranges.get(identity, ()), (offset, end)))
        merged = []
        for start, stop in ranges:
            if merged and start <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(merged[-1][1], stop))
            else:
                merged.append((start, stop))
        self.read_ranges[identity] = merged
        if merged == [(0, result['total_bytes'])]:
            self.session.cursors['read_facts'].add(source)

    def replay(self, identifier, metadata):
        if identifier in self.applied_receipts:
            return
        for effect in metadata.get('actions', ()):
            self.counts[effect['name']] += 1
            self.apply_effect(effect)
        self.applied_receipts.add(identifier)

    def requirements_met(self):
        policy = self.driver.policy
        return set(policy.required_names) <= self.completed and set(policy.required_tags) <= self.tags


_REF = {'type': 'object', 'properties': {key: {'type': 'string'} for key in ('namespace', 'kind', 'key')},
        'required': ['namespace', 'kind', 'key'], 'additionalProperties': False}


_ACTION_CURSOR_HELP = ('For the first page or a new query/target, omit cursor or set it to null. '
                       'To continue the same query and target, copy the complete next_cursor from the previous result unchanged; '
                       'do not use an empty object or invent a cursor.')
_ACTION_CURSOR_SCHEMA = {'type': ['object', 'null'], 'description': _ACTION_CURSOR_HELP,
    'properties': {'identity': {'type': 'string'}, 'cursor': {
        'type': 'object', 'properties': {'identity': {'type': 'string'},
            'offset': {'type': 'integer', 'minimum': 0},
            'candidates': {'type': 'array', 'items': {'type': 'string'}}},
        'required': ['identity', 'offset'], 'additionalProperties': False}},
    'required': ['identity', 'cursor'], 'additionalProperties': False}
_ACTION_CURSOR_VALIDATOR = validator_for(_ACTION_CURSOR_SCHEMA)(_ACTION_CURSOR_SCHEMA)


def _schema(properties):
    return {'type': 'object', 'properties': properties, 'required': list(properties), 'additionalProperties': False}


def _stage_segments(content, stages):
    """阶段是输出解释信息，原始正文继续保留在 Thread。"""
    names={stage['name'].casefold():stage['name'] for stage in stages}
    matches=list(re.finditer(r'(?im)^[ \t]*->[ \t]*stage_begin[ \t]*:[ \t]*([^\s:]+)[ \t]*(?:\r?\n|$)',content))
    segments=[]
    first=matches[0].start() if matches else len(content)
    if first:segments.append({'name':'default','known':True,'start':0,'end':first})
    for index,match in enumerate(matches):
        name=match.group(1)
        end=matches[index+1].start() if index+1<len(matches) else len(content)
        segments.append({'name':names.get(name.casefold(),name),'known':name.casefold() in names,
                         'start':match.end(),'end':end})
    return segments


class LLMDriver:
    def __init__(self, provider, threads, *, input_builder, policy=None, shell_factory=None, extensions=(),
                 provider_selector=None, activation_slots=None):
        self.provider, self.threads = provider, threads
        self.input_builder = input_builder
        self.provider_selector = provider_selector
        self.activation_slots = activation_slots
        self.policy = policy or LLMPolicy()
        self.shell_factory = shell_factory
        self.extensions = tuple(extensions)

    def _tool_schemas(self):
        """工具实际参数合同；提供方 strict 传输约束在声明边界附加。"""
        dynamic = {'type': 'string'} if self.policy.strict_tools else {'type': 'object', 'additionalProperties': True}
        cursor = {**dynamic, 'type': [dynamic['type'], 'null']}
        schemas = {
            'action_find': {**_schema({'target': _REF, 'query': {'type': 'string', 'description': 'Case-insensitive literal substring of name and description; use the empty string to list all available actions. No wildcard or semantic matching.'}, 'limit': {'type': 'integer'},
                                    'cursor': {**cursor, 'description': _ACTION_CURSOR_HELP} if self.policy.strict_tools else _ACTION_CURSOR_SCHEMA}),
                            'required': ['target', 'query', 'limit']},
            'action_describe': {**_schema({'name': {'type': 'string'}, 'target': _REF,
                'actions': {'type': 'array', 'items': {'type': 'string'}, 'minItems': 1}}), 'required': []},
            'action_invoke': _schema({'name': {'type': 'string'}, 'target': _REF, 'arguments': dynamic}),
            'ls': {**_schema({'path': {'type':'string'}, 'limit': {'type':'integer'}, 'cursor': cursor if self.policy.strict_tools else {}}), 'required':['path']},
            'read': {**_schema({'path': {'type':'string'}, 'offset': {'type':'integer'}, 'size': {'type':'integer'},
                'encoding': {'type':'string','enum':['utf-8','base64']}, 'expected_revision': {}}), 'required':['path']},
            'find': {**_schema({'pattern': {'type':'string'}, 'path': {'type':'string'}, 'limit': {'type':'integer'},
                'cursor': cursor if self.policy.strict_tools else {}}), 'required':['pattern','path']},
            'end_activation': {**_schema({'note': {'type': 'string'}}), 'required': []},
            'grep': {**_schema({'pattern': {'type':'string'}, 'path': {'type':'string'}, 'glob': {'type':['string','null']},
                'literal': {'type':'boolean'}, 'ignore_case': {'type':'boolean'}}), 'required':['pattern','path']},
        }
        if self.policy.allowed_names == () or self.policy.allowed_tags == ():
            schemas = {name: value for name, value in schemas.items() if not name.startswith('action_')}
        if self.shell_factory is not None:
            schemas['bash'] = _schema({'script': {'type': 'string'}})
        if self.policy.result_schema is not None:
            schemas['submit_result'] = self.policy.result_schema
        if self.policy.mode == 'interview':
            schemas = {name: schema for name, schema in schemas.items() if name == 'submit_result'}
        if self.policy.direct_json:
            schemas = {}
        return schemas

    def _tools(self):
        tools = [{'type': 'function', 'function': {'name': name, 'description': _TOOL_DESCRIPTIONS[name] + (' Dynamic objects use JSON-encoded strings in this strict tool protocol.' if self.policy.strict_tools and name in ('action_invoke', 'action_find', 'ls', 'find') else '') + ('' if self.policy.parallel_tool_calls else ' 每次回复至多调用一个工具；若同时提交 end_activation 与其他工具，其他工具先执行。'),
                'parameters': normalize_strict_function_parameters(schema) if self.policy.strict_tools else schema,
                'strict': self.policy.strict_tools}} for name, schema in self._tool_schemas().items()]
        return tools

    async def _dispatch(self, name, arguments, session, ledger, shell):
        field = {'action_invoke': 'arguments', 'action_find': 'cursor', 'ls': 'cursor', 'find': 'cursor'}.get(name)
        if self.policy.strict_tools and field and arguments.get(field) is not None:
            arguments = dict(arguments)
            try:
                arguments[field] = _decode(arguments[field])
            except _ToolInputError:
                if name != 'action_invoke': raise
                arguments[field] = None  # 严格协议中无效行动文本仍记一次失败尝试。
        if name == 'action_invoke':
            result = await ledger.invoke(arguments['name'], Ref(**arguments['target']), arguments['arguments'])
            return {'result': asdict(result), 'observation': ledger.feedback}
        if name == 'action_find':
            return await _read_tool(ledger.find, Ref(**arguments['target']), query=arguments['query'], limit=arguments['limit'],
                                    cursor=arguments.get('cursor'))
        if name == 'action_describe':
            target = Ref(**arguments['target']) if arguments.get('target') is not None else None
            if arguments.get('actions') is not None:
                return {'actions': [await _read_tool(ledger.describe, action, target) for action in arguments['actions']]}
            if not arguments.get('name'):
                raise _ToolInputError('请传 actions:[行动名]，或传 name 和 target 查看单个行动。')
            return await _read_tool(ledger.describe, arguments['name'], target)
        if name == 'end_activation':
            self.threads.event(ledger.thread_id, 'end_activation', {'call_id': ledger.call_id, 'note': arguments.get('note')})
            ledger.ended = True
            return {'status': 'completed', 'message': '本次激活已结束，已生效的行动和现有状态保留。'}
        if name in ('read','ls','find','grep'):
            from .actor_files import ActorFiles
            files=session.cursors.get('files')
            if files is None:
                files=ActorFiles(session,self.threads,shell=shell)
                session.cursors['files']=files
            result = await _read_tool(getattr(files,name),**{key:value for key,value in arguments.items() if value is not None})
            if name == 'read' and 'error' not in result:
                ledger.read_effect(result, arguments.get('offset') or 0)
            return result
        if name == 'bash':
            result = await shell.execute(arguments['script'])
            for reference in (result.stdout_ref, result.stderr_ref, *result.receipts):
                with (shell.result_dir.parent / reference).open('rb') as stream:
                    artifact = session.prepare_artifact(iter(lambda: stream.read(65536), b''))
                self.threads.register_artifact(ledger.thread_id, reference, artifact, actor=session.actor.id)
            from .actor_files import ActorFiles
            return {**asdict(result),'stdout_path':ActorFiles.result_path(result.stdout_ref),
                    'stderr_path':ActorFiles.result_path(result.stderr_ref),
                    'receipt_paths':[ActorFiles.result_path(reference) for reference in result.receipts]}
        raise _ToolInputError('unknown tool')

    def read_result(self, session, reference, *, offset=0, size=65536, encoding='utf-8'):
        session.scope.check_active()
        encoding = encoding or 'utf-8'
        if encoding not in ('utf-8', 'base64') or (encoding == 'utf-8' and size < 4):
            raise ValueError('invalid result encoding or byte budget')
        chunk = self.threads.read_actor_artifact(reference, actor=session.actor.id,
                                           offset=offset, size=size)
        return {**_text_range(chunk['data'], offset, chunk['total_bytes'], encoding), 'source': chunk['source']}

    async def run(self, session):
        session.scope.check_active()
        activation_started=time.perf_counter()
        thread_id = self.threads.find(session.actor.id, session.moment, kind=self.policy.mode)
        if thread_id is None:
            # 新 Thread 显式继承本次激活的抽样点；同 Moment 续会沿用已持久化路由。
            thread_id = self.threads.open(session.actor.id, session.moment, self.policy.mode,
                                          model_route=session.cursors.get('model_route'))
        else:
            head = self.threads.describe(thread_id)
            if head['status'] == 'incomplete' and self.threads.close_reason(thread_id) == 'budget_exhausted':
                return DriverResult('incomplete', {'thread_id': thread_id, 'requests': 0, 'retry_suppressed': True}, 'budget_exhausted')
            if head['status'] not in ('completed', 'waiting'):
                raise RuntimeError('unfinished Thread requires explicit recovery; budgets are not reset')
            verify = getattr(self.provider, 'verify_route', None)
            if verify is not None:
                # 续会与恢复沿用已持久化路由；端点配置变化时明确失败，不静默改投。
                verify(thread_id)
            self.threads.reopen(thread_id)
        session.cursors['thread_id'] = thread_id
        ledger = _Ledger(self, session, thread_id)
        shell = None
        status, reason, structured = 'incomplete', 'driver_error', None
        prepared = False
        reasoning=[]
        timings={}
        budget = RequestBudget(self.policy.max_turns)
        budget_token = None
        from .activation import ActivationContext, activation_scope
        context=ActivationContext(session,thread_id,self.policy.mode,timings=timings)
        extension_scope=AsyncExitStack()
        try:
            await extension_scope.enter_async_context(activation_scope(context,self.extensions))
            with _timed(timings,'prompt_s'):
                provider=self.provider if self.provider_selector is None else await invoke_maybe_async(self.provider_selector,session)
                inputs = await invoke_maybe_async(self.input_builder, session)
                context.inputs=inputs
                if isinstance(inputs, InputBatch):
                    self.threads.append_input(thread_id, inputs.messages, inputs.consumer, inputs.cursor, context=inputs.context)
                    context.preparations.extend(inputs.effects)
                else:
                    for message in inputs:
                        self.threads.append_message(thread_id, message)
            from pydantic_ai.exceptions import UsageLimitExceeded, UnexpectedModelBehavior
            # 准备阶段先于设施与工具装配，生命周期顺序与原先一致；协议错误与模型循环共用
            # 同一收束边界，其他异常仍按原类型向外传播，不在准备阶段扩大捕获面。
            try:
                with _timed(timings,'setup_s'):
                    # prepare() 之前确定决策 Thread、输入水位、提供方、有序工具与激活租约；
                    # 召回等辅助请求据此复用同一实例、同一路由与同一前缀，不重建经营输入。
                    tools = self._tools()
                    semantic_schemas = self._tool_schemas()
                    def execution_schema(semantic, transport):
                        # strict wire 将可选项变成 required nullable；执行层保留可省略语义。
                        schema = dict(transport)
                        if 'properties' in semantic:
                            schema['required'] = semantic.get('required', [])
                            schema['properties'] = {key: execution_schema(child, transport['properties'][key])
                                for key, child in semantic['properties'].items()}
                        if isinstance(semantic.get('items'), dict):
                            schema['items'] = execution_schema(semantic['items'], transport['items'])
                        for keyword in ('anyOf', 'oneOf', 'allOf'):
                            if keyword in semantic:
                                schema[keyword] = [execution_schema(child, transport[keyword][index])
                                    for index, child in enumerate(semantic[keyword])] + transport[keyword][len(semantic[keyword]):]
                        return schema
                    execution_schemas = {tool['function']['name']: execution_schema(
                        semantic_schemas[tool['function']['name']], tool['function']['parameters']) for tool in tools}
                    schemas = {name: validator_for(schema)(schema) for name, schema in execution_schemas.items()}
                    options = dict(self.policy.request_options)
                    options['tools'] = tools
                    options['parallel_tool_calls'] = self.policy.parallel_tool_calls
                    if self.policy.direct_json:
                        options.pop('tools')
                        options.pop('parallel_tool_calls')
                        options['response_format'] = {'type': 'json_schema', 'json_schema': {
                            'name': 'result', 'strict': True, 'schema': self.policy.result_schema}}
                    route = self.threads.model_route(thread_id)
                    endpoint = provider.endpoints_for(route).id if hasattr(provider,'endpoints_for') else ''
                    lease = session.cursors.get('activation_lease')
                    if lease is None and self.activation_slots is not None and endpoint:
                        # 未由工作池入场时，在路由确定后申请激活租约；已入场则不重复占用同一槽位。
                        lease_stack = AsyncExitStack()
                        lease = await lease_stack.enter_async_context(self.activation_slots.lease(endpoint))
                        session.cursors['activation_lease_stack'] = lease_stack
                        session.cursors['activation_lease'] = lease
                    context.decision = DecisionScope(thread_id=thread_id,
                        through=self.threads.describe(thread_id)['last_seq'], provider=provider,
                        model_route=route, endpoint=endpoint, tools=tuple(tools),
                        lease=session.cursors.get('activation_lease'))
                budget_token = current_request_budget.set(budget)
                await context.prepare()
                prepared = True
                for message in context.messages:
                    self.threads.append_message(thread_id,message)
                with _timed(timings,'setup_s2'):
                    if self.policy.reasoning_stages and self.policy.result_schema is None:
                        guidance={'role':'user','content':'按任务需要思考并行动，可参考以下阶段：\n'+
                            '\n'.join(stage['name']+': '+stage.get('desc','') for stage in self.policy.reasoning_stages)+
                            '\n若按阶段组织正文，每段以单独一行 -> stage_begin: 阶段名 开始；直接工具调用仍使用工具接口。'}
                        if self.threads.input_context(thread_id,'reasoning_stages')!=guidance:
                            self.threads.append_input(thread_id,[],'reasoning_stages',None,context=guidance)
                    if self.shell_factory is not None:
                        shell = await invoke_maybe_async(self.shell_factory, session, ledger)
                    from .actor_files import ActorFiles
                    files=ActorFiles(session,self.threads,shell=shell)
                    session.cursors['files']=files
                    if shell is not None:shell.bind_files(files)
                from pydantic_ai import Agent, Tool, ModelRequestNode
                from pydantic_ai.messages import ModelRequest, UserPromptPart, ToolReturnPart
                from .models import ThreadModel
                from pydantic_ai.usage import UsageLimits
                from .model_messages import history, display_response

                empty = parallel_errors = repeated_read_streak = empty_retry_attempt = repeated_response_streak = 0
                last_response_signature = None
                pending_calls = {}
                incomplete = None

                async def request_model(messages):
                    nonlocal empty_retry_attempt, incomplete
                    session.scope.check_active()
                    budget.check()
                    if not isinstance(provider, ModelProvider):
                        budget.take()
                    turn_options = dict(options)
                    adjustments = (
                        (empty_retry_attempt, self.policy.empty_retry_temperature_delta, self.policy.empty_retry_temperature_max,
                         'provider_empty_response_retry', 'attempt'),
                        (repeated_read_streak, self.policy.repeated_read_temperature_delta, self.policy.repeated_read_temperature_max,
                         'provider_repeated_read_diversification', 'streak'),
                    )
                    for count, delta, maximum, event, counter in adjustments:
                        if count and delta is not None:
                            defaults = getattr(provider, 'request_options', {})
                            before = float(turn_options.get('temperature', defaults.get('temperature')) or 0)
                            after = round(min(before + delta * (count if counter == 'streak' else 1), maximum), 12)
                            turn_options['temperature'] = after
                            self.threads.event(thread_id, event, {counter: count, 'temperature_before': before,
                                'temperature_after': after, 'retry_scope': 'agent_activation'})
                    empty_retry_attempt = 0
                    with _timed(timings, 'model_s'):
                        response, sequence, incomplete = await provider.request_model(thread_id, turn_options, model_messages=messages)
                    session.scope.check_active()
                    if self.policy.reasoning_stages:
                        content = display_response(response)['content']
                        if content: reasoning.append({'message_seq': sequence, 'segments': _stage_segments(content, self.policy.reasoning_stages)})
                    return response

                async def execute(ctx, **arguments):
                    nonlocal structured
                    session.scope.check_active()
                    identifier, name = ctx.tool_call_id, ctx.tool_name
                    item = pending_calls[identifier]
                    receipt_item = {**item, 'function': {**item['function'], 'arguments': '{}'}} if name == 'end_activation' else item
                    receipt = self.threads.get_tool_result(thread_id, identifier)
                    ledger.effects = []
                    if receipt is not None:
                        if receipt['call'] != receipt_item: raise ValueError('one tool call id has conflicting payloads')
                        ledger.replay(identifier, receipt['metadata'])
                        ledger.ended |= bool(receipt['metadata'].get('ended'))
                        if receipt['metadata'].get('structured') is not None: structured = receipt['metadata']['structured']
                        feedback = receipt['content']
                        self.threads.append_message(thread_id, {'role': 'tool', 'tool_call_id': identifier, 'content': feedback})
                        return feedback
                    if name != 'end_activation' and (structured is not None or (ledger.terminal and ledger.requirements_met())):
                        feedback = _json({'status': 'not_executed', 'reason': 'activation_completed', 'message': '本次激活已完成，该工具未执行，无需继续提交。'})
                    elif errors := list(schemas[name].iter_errors(arguments)):
                        feedback = _json({'error': 'invalid_tool_arguments', 'details': [
                            {'path': list(error.absolute_path), 'message': error.message,
                             'validator': error.validator, 'expected': error.validator_value} for error in errors],
                            'message': rejection_feedback(name, schemas[name].schema, arguments, {'reason': 'invalid_tool_arguments',
                                'details': [{'path': list(error.absolute_path), 'message': error.message} for error in errors]}) +
                                (_ACTION_CURSOR_HELP if name == 'action_find' and any(list(error.absolute_path)[:1] == ['cursor'] for error in errors) else '')})
                    elif name == 'submit_result':
                        structured = arguments
                        feedback = _json({'status': 'completed', 'result': arguments})
                    else:
                        ledger.call_id, ledger.shell_call = identifier, name == 'bash'
                        try:
                            with _timed(timings, 'tools_s'):
                                feedback = _json(tool_feedback(name, await self._dispatch(name, arguments, session, ledger, shell), arguments))
                        except _ToolInputError as error:
                            feedback = _json({'error': 'invalid_tool_input', 'message': f'工具 {name} 的输入无法处理：{error}；请核对工具参数后重试。'})
                    if budget.warning_pending and not budget.warning_sent:
                        payload = json.loads(feedback)
                        message_target = payload.get('result', payload) if isinstance(payload, dict) else None
                        if isinstance(message_target, dict):
                            remaining = budget.remaining
                            message_target['message'] = message_target.get('message', '') + f'本次激活还剩 {remaining} 次调用机会，请尽快完成并调用 end_activation。'
                            feedback = _json(payload)
                            budget.warning_sent, budget.warning_pending = True, False
                    with _timed(timings, 'tool_save_s'):
                        self.threads.save_tool_result(thread_id, receipt_item, feedback,
                            metadata={'actions': ledger.effects, 'structured': structured if name == 'submit_result' else None,
                                      'ended': name == 'end_activation' and ledger.ended})
                    ledger.applied_receipts.add(identifier)
                    return feedback

                def feedback_node(content):
                    nonlocal repeated_read_streak
                    repeated_read_streak = 0
                    self.threads.append_message(thread_id, {'role': 'user', 'content': content})
                    return ModelRequestNode(ModelRequest(parts=[UserPromptPart(content)]))

                agent_tools = [Tool.from_schema(execute, item['function']['name'], item['function']['description'],
                                execution_schemas[item['function']['name']], takes_ctx=True, sequential=True) for item in tools]
                # 协议错误直接失败；业务纠正使用普通反馈，不额外消耗 SDK 的独立重试预算。
                agent = Agent(ThreadModel(request_model), tools=agent_tools, retries=0)
                # 全历史仅在激活入口解码一次，后续模型请求直接借用 Agent 持有的同一消息列表。
                with _timed(timings, 'decode_s'):
                    initial = history(self.threads.snapshot_messages(thread_id, raw=True)['messages'])
                    if not initial:
                        self.threads.append_message(thread_id, {'role': 'user', 'content': ''})
                        initial = [ModelRequest(parts=[UserPromptPart('')])]
                async with agent.iter(message_history=initial, conversation_id=thread_id,
                                      usage_limits=UsageLimits(request_limit=None)) as run:
                    del initial
                    node = ModelRequestNode(ModelRequest(parts=[]))
                    while not Agent.is_end_node(node):
                        if Agent.is_call_tools_node(node):
                            if incomplete:
                                reason = incomplete
                                break
                            response = display_response(node.model_response)
                            calls, content = response.get('tool_calls', []), response.get('content', '')
                            signature=(content,tuple((item['function']['name'],item['function']['arguments']) for item in calls))
                            if (content.strip() or calls) and signature==last_response_signature:
                                repeated_response_streak+=1
                            else:
                                repeated_response_streak=0
                            last_response_signature=signature
                            if self.policy.repeated_response_limit is not None and repeated_response_streak>=self.policy.repeated_response_limit:
                                reason='repeated_response'
                                break
                            pending_calls = {item['id']: item for item in calls}
                            # SDK 的 sequential 工具按响应顺序执行；结束调用放到本回复最后。
                            node.model_response.parts.sort(key=lambda part: getattr(part, 'tool_name', None) == 'end_activation')
                            if self.policy.direct_json:
                                try:
                                    candidate = json.loads(content)
                                    validator_for(self.policy.result_schema)(self.policy.result_schema).validate(candidate)
                                except Exception:
                                    node = feedback_node('Return a result matching the supplied JSON schema.')
                                    continue
                                structured, status, reason = candidate, 'completed', 'structured_result'
                                break
                            if not calls:
                                if not content.strip():
                                    empty += 1
                                    if empty > self.policy.empty_retries:
                                        reason = 'empty_response'
                                        break
                                    empty_retry_attempt = empty
                                    node = feedback_node('The response was empty. Continue the current decision.')
                                    continue
                                if not ledger.requirements_met() or self.policy.result_schema is not None:
                                    node = feedback_node(_json({'required_names': self.policy.required_names,
                                        'required_tags': self.policy.required_tags, 'completed': sorted(ledger.completed),
                                        'submit_result_required': self.policy.result_schema is not None}))
                                    continue
                            else:
                                parsed = []
                                for item in calls:
                                    function = item['function']
                                    try:
                                        arguments = json.loads(function['arguments'])
                                        valid = function['name'] in schemas and schemas[function['name']].is_valid(arguments)
                                    except (ValueError, TypeError): arguments, valid = None, False
                                    parsed.append((item, arguments, valid))
                                if not self.policy.parallel_tool_calls and len(calls) > 1 and not any(item['function']['name'] == 'end_activation' for item in calls):
                                    parallel_errors += 1
                                    if parallel_errors > 1 or not all(valid for _, _, valid in parsed):
                                        reason = 'parallel_tool_contract'
                                        break
                                    parts = []
                                    for item in calls:
                                        feedback = 'parallel_tool_calls is false; submit exactly one tool call.'
                                        self.threads.append_message(thread_id, {'role': 'tool', 'tool_call_id': item['id'], 'content': feedback})
                                        parts.append(ToolReturnPart(item['function']['name'], feedback, item['id']))
                                    repeated_read_streak = 0
                                    node = ModelRequestNode(ModelRequest(parts=parts))
                                    continue
                                ledger.preflight(arguments['name'] for item, arguments, valid in parsed
                                    if valid and item['function']['name'] == 'action_invoke'
                                    and self.threads.get_tool_result(thread_id, item['id']) is None)
                                ledger.turn_repeated_read = ledger.turn_progress = False
                        if Agent.is_model_request_node(node):
                            budget.check()
                        executing_tools = Agent.is_call_tools_node(node)
                        node = await run.next(node)
                        if executing_tools and Agent.is_model_request_node(node):
                            repeated_read_streak = repeated_read_streak + 1 if ledger.turn_repeated_read else 0
                            if ledger.ended:
                                status, reason = 'completed', 'end_activation'
                                break
                            if (structured is not None or (ledger.terminal and self.policy.result_schema is None)) and ledger.requirements_met():
                                status, reason = 'completed', 'structured_result' if structured is not None else 'terminal_action'
                                break
                            if self.policy.max_action_calls is not None and ledger.used >= self.policy.max_action_calls and ledger.used:
                                reason = 'action_budget_exhausted'
                                break
                    else:
                        status, reason = 'completed', 'natural_completion'
            except ProviderFailure as error:
                if not prepared: raise
                reason = error.reason
            except _BudgetReached as error: reason = str(error)
            except (UsageLimitExceeded, ActivationBudgetExhausted): reason = 'budget_exhausted'
            except UnexpectedModelBehavior as error:
                reason = 'model_protocol_error'
                self.threads.event(thread_id, reason, {'error': str(error)})
            finally:
                # 记忆阶段会重新读取完整 Thread；进入前释放 Agent 的激活历史。
                run = node = initial = None
                if budget_token is not None:
                    current_request_budget.reset(budget_token)
                    budget_token = None
                self.threads.event(thread_id, 'activation_budget', {'limit': budget.limit,
                    'requests': budget.requests, 'remaining': budget.remaining, 'status': status, 'reason': reason,
                    'counting': 'physical_attempts' if isinstance(provider, ModelProvider) else 'provider_calls'})
            result = DriverResult(status, {'thread_id': thread_id, 'result': structured,
                                  'content':content if reason=='natural_completion' else None,
                                 'requests': budget.requests, 'request_limit': budget.limit, 'action_counts': dict(ledger.counts), 'reasoning_stages':reasoning,'phase_timings':timings}, reason)
            context.through=self.threads.describe(thread_id)['last_seq']
            context.result=result
            await extension_scope.aclose()
            return result
        except BaseException as error:
            import sys
            context.result=None
            status,reason = 'incomplete',type(error).__name__
            await extension_scope.__aexit__(*sys.exc_info())
            raise
        finally:
            try:
                with _timed(timings,'cleanup_s'):
                    lease_stack=session.cursors.pop('activation_lease_stack',None)
                    session.cursors.pop('activation_lease',None)
                    if lease_stack is not None:await lease_stack.aclose()
                    files=session.cursors.pop('files',None)
                    if files is not None:await files.close()
                    if shell is not None:
                        try:
                            if status in ('completed', 'waiting') and shell.has_workspace:
                                shell.save_workspace()
                        finally:
                            await shell.aclose()
            except BaseException as error:
                status,reason='incomplete',type(error).__name__
                raise
            finally:
                elapsed=time.perf_counter()-activation_started
                timings['other_s']=max(0.,elapsed-sum(timings.values()))
                try:
                    self.threads.close(thread_id,status,reason=reason,elapsed_s=elapsed,phase_timings=timings)
                finally:
                    await extension_scope.aclose()
