"""公开多来源 RunPlan：完整模型输入、持久水位及受控失败恢复。"""
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import asdict, replace
import json
import sys
import uuid

from society0.async_utils import invoke_maybe_async
from society0.kernel.actors import ActorRecord
from society0.kernel.cognition import InputBatch
from society0.kernel.plugins import Plugin
from society0.kernel.runner import RunContract, RunPlan
from society0.kernel.runtime import Phase
from society0.kernel.schedule import StepPlan
from society0.plugins import cognition_plugin, social_plugin, social_source_plugin
from society0.plugins.cognition import SourceSpec
from society0.presets import llm_preset
from .composition_support import notification_data_plugin, notification_source_plugin

SOCIAL_ID = 'social.main.feed.v1'
NOTICE_ID = 'notice.main.v1'
LIFECYCLE_IDS = ('notice.a.v1', 'notice.b.v1')
FAULTS = ('none', 'source-read', 'append-invalid-context', 'append-invalid-message', 'append-invalid-cursor',
          'after-callback-partial-write', 'after-step-flush', 'provider-error', 'before-complete')
SCHEMA = (
    'CREATE TABLE composition_requests(id INTEGER PRIMARY KEY,step INTEGER NOT NULL,actor TEXT NOT NULL,thread_id TEXT NOT NULL,request_seq INTEGER NOT NULL,through INTEGER NOT NULL,typed BLOB NOT NULL,options BLOB NOT NULL)',
    'CREATE TABLE composition_observations(id INTEGER PRIMARY KEY,step INTEGER NOT NULL,actor TEXT NOT NULL,thread_id TEXT NOT NULL,consumer TEXT NOT NULL,cursor BLOB NOT NULL,receipts BLOB NOT NULL)',
    'CREATE TABLE composition_diagnostics(id INTEGER PRIMARY KEY,step INTEGER NOT NULL,actor TEXT,kind TEXT NOT NULL,detail BLOB NOT NULL)',
    'CREATE TABLE composition_outcomes(id INTEGER PRIMARY KEY,step INTEGER NOT NULL,actor TEXT NOT NULL,result BLOB NOT NULL)',
)


def encoded(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False).encode()


def original(domain, number, activation=1):
    marker = f'{domain}/{number}/{activation}'
    return f'原文开始[{marker}]\n' + ('完整中文原文、emoji🙂、逐条保留。'*80) + f'\n原文结束[{marker}]'


class CompositionFault(RuntimeError):
    pass


class ScriptedProvider:
    """捕获真实 typed 入参；诊断明确标记为本地脚本响应。"""
    def __init__(self, store, threads, config):
        self.store, self.threads, self.config = store, threads, config
        self.current = ContextVar('composition-session')

    @asynccontextmanager
    async def activation(self, context):
        token = self.current.set(context.session)
        try:
            yield
            self.store.transaction(lambda writer: writer.execute('INSERT INTO composition_outcomes(step,actor,result) VALUES(?,?,?)',
                (context.session.step, context.session.actor.id, encoded(asdict(context.result)))))
        finally:
            self.current.reset(token)

    async def request_model(self, thread_id, options, *, model_messages=None):
        from pydantic_ai.messages import ModelMessagesTypeAdapter, ModelResponse, TextPart
        current = self.current.get()
        actual = model_messages() if callable(model_messages) else model_messages
        typed = ModelMessagesTypeAdapter.dump_python(actual, mode='json')
        through = self.threads.describe(thread_id)['last_seq']
        request = self.threads.record_request(thread_id, provider_options=options,
            physical_request_id=uuid.uuid4().hex, through=through)
        self.store.transaction(lambda writer: writer.execute('INSERT INTO composition_requests(step,actor,thread_id,request_seq,through,typed,options) VALUES(?,?,?,?,?,?,?)',
            (current.step, current.actor.id, thread_id, request, through, encoded(typed), encoded(options))))
        if should_fail(self.config, current) and self.config['fault'] == 'provider-error':
            diagnose(self.store, current, 'provider-error')
            raise CompositionFault('provider-error')
        if self.config['variant'] == 'interview':
            from pydantic_ai.messages import ToolCallPart
            response = ModelResponse(parts=[ToolCallPart('submit_result', {'measurement': '完整测量🙂'}, tool_call_id=uuid.uuid4().hex)], finish_reason='tool_call')
        else:
            response = ModelResponse(parts=[TextPart('确定性完整决定🙂')], finish_reason='stop')
        message = ModelMessagesTypeAdapter.dump_python([response], mode='json')[0]
        sequence = self.threads.append_message(thread_id, {'model_message': message})
        self.threads.event(thread_id, 'scripted_response', {'request_seq': request, 'message_seq': sequence})
        return response, sequence, None


def should_fail(config, current):
    return current.step == config['fault_step'] and current.signals[0]['activation_in_step'] == 1


def diagnose(store, current, kind, **detail):
    store.transaction(lambda writer: writer.execute('INSERT INTO composition_diagnostics(step,actor,kind,detail) VALUES(?,?,?,?)',
        (current.step, current.actor.id, kind, encoded(detail))))


def _configuration(config):
    release = config['release']
    if not release.get('commit'):
        raise ValueError('release.commit is required')
    if config.get('provider') != 'scripted':
        raise ValueError('composition fixture provider must be scripted')
    case = config.get('case', 'multi-source')
    if case not in ('multi-source', 'source-lifecycle'):
        raise ValueError('unsupported composition case: '+case)
    variant = config.get('variant', 'default')
    if variant not in ('default', 'reordered', 'discarded', 'interview'):
        raise ValueError('unsupported composition variant: '+variant)
    available = (SOCIAL_ID, NOTICE_ID) if case == 'multi-source' else LIFECYCLE_IDS
    sources = tuple(config.get('sources', available[::-1] if variant == 'reordered' else available))
    if len(set(sources)) != len(sources) or any(identifier not in available for identifier in sources):
        raise ValueError('sources must be unique IDs from '+str(available))
    if config.get('start', 1) != 1 or config.get('activations_per_step', 2) != 2:
        raise ValueError('fixture requires start=1 and activations_per_step=2')
    end = config.get('end', 2)
    if type(end) is not int or end < 1:
        raise ValueError('end must be a positive complete-step number')
    fault, fault_step = config.get('fault', 'none'), config.get('fault_step')
    if fault not in FAULTS or (fault != 'none' and (type(fault_step) is not int or fault_step < 2)):
        raise ValueError('fault requires a supported name and fault_step>=2')
    return {**config, 'case': case, 'variant': variant, 'sources': list(sources), 'start': 1, 'end': end,
        'moment': config.get('moment', {'time': 1, 'phase': 'read'}), 'activations_per_step': 2,
        'fault': fault, 'fault_step': fault_step}


def build(config):
    config = _configuration(config)
    def provider(ctx):
        actual = ScriptedProvider(ctx.require('storage', 'store'), ctx.require('threads', 'threads'), config)
        ctx.provide('provider', actual)
        ctx.provide('activation', actual.activation)
    roots = [Plugin('capture', ('storage', 'threads'), provider, schema=SCHEMA),
        social_plugin('abc', config={'embeddings': {'semantic_embeddings': False}}, content_length_limit=-1), social_source_plugin()]
    domains = ['notice.data'] if config['case'] == 'multi-source' else ['notice.a.data', 'notice.b.data']
    bindings = {SOCIAL_ID: ('social.source', 'source')}
    for identifier, domain in zip((NOTICE_ID,) if config['case'] == 'multi-source' else LIFECYCLE_IDS, domains):
        source = domain.removesuffix('.data')+'.source'
        roots.extend((notification_data_plugin(name=domain), notification_source_plugin(name=source, data=(domain, 'notifications'))))
        bindings[identifier] = (source, 'source')
    def controlled(ctx):
        store = ctx.require('storage', 'store')
        for identifier, reference in bindings.items():
            source = ctx.require(*reference)
            async def read(current, previous, source=source):
                if should_fail(config, current) and config['fault'] == 'source-read':
                    diagnose(store, current, 'source-read')
                    raise CompositionFault('source-read')
                return await invoke_maybe_async(source, current, previous)
            ctx.provide(identifier, read)
    roots.append(Plugin('controlled.sources', tuple(dict.fromkeys(('storage', *(ref[0] for ref in bindings.values())))), controlled))
    roots.append(cognition_plugin(sources=tuple(SourceSpec(identifier, ('controlled.sources', identifier)) for identifier in config['sources']),
                                  environment='同一共享环境；保留全部已呈现原文与正常行动能力。'))
    def observed(ctx):
        original_builder = ctx.require('cognition', 'input_builder')
        store, threads = ctx.require('storage', 'store'), ctx.require('threads', 'threads')
        notes = [ctx.require(domain, 'notifications') for domain in domains]
        async def builder(current):
            batch = await original_builder(current)
            fail = should_fail(config, current)
            fault = config['fault'] if fail else 'none'
            if fault.startswith('append-invalid-'):
                diagnose(store, current, fault, expected_exception='TypeError')
                field = fault.removeprefix('append-invalid-')
                value = {'bad': object()} if field != 'message' else [*batch.messages, {'role': 'user', 'content': object()}]
                return replace(batch, **{('messages' if field == 'message' else field): value})
            if config['variant'] == 'discarded':
                return InputBatch([{'role': 'user', 'content': '独立选中材料'}], consumer='selected', cursor={'position': 1})
            def after_saved(context):
                receipts = {note.domain: note.receipts(current.actor.id) for note in notes}
                cursor = threads.input_cursor(context.thread_id, batch.consumer)
                store.transaction(lambda writer: writer.execute('INSERT INTO composition_observations(step,actor,thread_id,consumer,cursor,receipts) VALUES(?,?,?,?,?,?)',
                    (current.step, current.actor.id, context.thread_id, batch.consumer, encoded(cursor), encoded(receipts))))
                if fault == 'after-callback-partial-write':
                    diagnose(store, current, 'after-callback-partial-write', thread_id=context.thread_id)
                    raise CompositionFault('after-callback-partial-write')
            def sentinel(context):
                diagnose(store, current, 'sentinel', thread_id=context.thread_id)
            return replace(batch, effects=(*batch.effects, after_saved, sentinel))
        ctx.provide('input_builder', builder)
        def after_flush():
            number = store.complete_step+1
            if config['fault'] == 'after-step-flush' and number == config['fault_step']:
                store.transaction(lambda writer: writer.execute('INSERT INTO composition_diagnostics(step,kind,detail) VALUES(?,?,?)',
                    (number, 'after-step-flush', encoded({'expected_exception': 'CompositionFault'}))))
                raise CompositionFault('after-step-flush')
        ctx.on_step(after=after_flush)
    roots.append(Plugin('observed.input', ('cognition', 'storage', 'threads', *domains), observed))
    def scheduled(ctx):
        social = ctx.require('social', 'mechanism')
        notes = [ctx.require(domain, 'notifications') for domain in domains]
        store = ctx.require('storage', 'store')
        class Schedule:
            async def next_step(self, complete):
                number = complete+1
                if number > config['end']: return None
                async def phase(context):
                    for activation in (1, 2):
                        if config['case'] == 'source-lifecycle':
                            if activation == 1:
                                for note in notes: note.publish('a', original(note.domain, number))
                        else:
                            if number == 1 and activation == 1:
                                social.execute('publish_post', 'b', 'b', {'content': original('social', 1, 1)}, 1)
                                notes[0].publish('a', original('notice', 1, 1))
                            elif activation == 2:
                                social.execute('publish_post', 'b', 'b', {'content': original('social', number, 2)}, 1)
                            else:
                                notes[0].publish('a', original('notice', number, 1))
                        context.activate('a', {'activation_in_step': activation})
                        await context.drain()
                    if number == config['fault_step'] and config['fault'] == 'before-complete':
                        store.transaction(lambda writer: writer.execute('INSERT INTO composition_diagnostics(step,kind,detail) VALUES(?,?,?)',
                            (number, 'before-complete', encoded({'expected_exception': 'CompositionFault'}))))
                        raise CompositionFault('before-complete')
                return StepPlan(config['moment']['time'], (Phase(config['moment']['phase'], phase),))
        ctx.provide('schedule', Schedule())
    roots.append(Plugin('schedule', ('social', 'storage', *domains), scheduled))
    from society0.kernel.llm import LLMPolicy
    plugins = llm_preset(records=(ActorRecord(actor, 'llm', persona='完整主体背景🙂：共享社会中的独立主体 '+actor, state={'主观状态': actor}) for actor in 'abc'),
        provider=('capture', 'provider'), input_builder=('observed.input', 'input_builder'), allows=lambda *args: True,
        plugins=roots[:-1], schedule=roots[-1], extensions=(('capture', 'activation'),),
        policy=LLMPolicy(mode='interview' if config['variant'] == 'interview' else 'decision',
            result_schema={'type': 'object', 'properties': {'measurement': {'type': 'string'}}, 'required': ['measurement'], 'additionalProperties': False}
                if config['variant'] == 'interview' else None))
    return RunPlan(plugins, RunContract(release=config['release'], dependencies={'python': sys.version}, configuration=config,
        time={'start': 1, 'end': config['end'], 'moment': config['moment']}, budgets={}))
