"""E2E-18/20 的普通计划：受控关闭、完整驱动扩展和独立记忆策略。"""
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import replace
from datetime import datetime, timezone
from importlib.metadata import version
import asyncio
import json
from pathlib import Path
import sys

from society0.activation_pool import DEFAULT_MAX_ACTIVATIONS
from society0.kernel.actors import ActorRecord
from society0.kernel.plugins import Plugin, PluginHost
from society0.kernel.runner import RunContract, RunPlan
from society0.kernel.runtime import DriverResult, Phase, Runtime
from society0.kernel.schedule import StepPlan
from society0.kernel.storage import StageReader
from society0.presets import llm_preset, rule_preset

from .composed_cognition import SCHEMA as REQUEST_SCHEMA, ScriptedProvider, encoded, original
from .composition_support import notification_data_plugin, notification_source_plugin

SCHEMA = ('CREATE TABLE shutdown_extension_events(id INTEGER PRIMARY KEY,event TEXT NOT NULL,detail BLOB NOT NULL)',)
SEED = original('先前完整记忆', 0)
EXTENSIONS = ('default', 'memory-write', 'memory-recall', 'memory-tools', 'memory-all', 'runtime-replacement')
SHUTDOWN = ('runtime-first', 'worker-first', 'runtime-error', 'worker-error')


class ShutdownFault(RuntimeError):
    pass


class Journal:
    def __init__(self, store):
        self.store = store
        self.events = []
        self.writable = True

    def record(self, event, **detail):
        self.events.append({'event': event, 'detail': detail})
        if self.writable:
            self.store.transaction(lambda writer: writer.execute(
                'INSERT INTO shutdown_extension_events(event,detail) VALUES(?,?)', (event, encoded(detail))))

    def close(self):
        self.writable = False
        self.record('journal.close')
        # compose 原生地在 PluginHost 退出后关闭 StageStore；此文件保留本次进程关闭事实。
        (self.store.path / 'shutdown-exit.json').write_text(json.dumps(self.events, ensure_ascii=False))


def journal_plugin():
    def install(ctx):
        journal = Journal(ctx.require('storage', 'store'))
        ctx.provide('journal', journal)
        ctx.on_close(journal.close)
    return Plugin('shutdown.events', ('storage',), install, schema=SCHEMA)


class PublicRuntime:
    """包装公开 run_step；所捕获 task 属于本次调用，无 Runtime 私有字段。"""
    def __init__(self, inner, journal, variant):
        self.inner, self.journal, self.variant = inner, journal, variant
        self.call_task = None
        self.finished = asyncio.Event()
        self.finished.set()

    @property
    def capacity(self): return self.inner.capacity
    @property
    def max_activations(self): return self.inner.max_activations
    @property
    def last_timing(self): return self.inner.last_timing

    async def run_step(self, number, time, phases):
        self.call_task = asyncio.current_task()
        self.finished.clear()
        self.journal.record('runtime.call', number=number)
        try:
            return await self.inner.run_step(number, time, phases)
        except BaseException:
            # abort_step 后诊断 SQL 禁写；本次资源日志由已声明的 Journal owner 保留。
            self.journal.writable = False
            raise
        finally:
            self.journal.record('runtime.finally', number=number)
            self.finished.set()

    async def close(self):
        self.journal.record('runtime.stop')
        await self.inner.close()
        await self.finished.wait()
        # 公开消费者实测关闭后拒绝新步骤，避免把日志顺序等同于停止接收。
        try:
            await self.inner.run_step(self.inner.store.complete_step + 1, 1, ())
        except RuntimeError as error:
            self.journal.record('runtime.rejected', error=str(error))
        else:
            raise AssertionError('closed Runtime accepted a step')
        self.journal.record('runtime.drained')
        if self.variant == 'runtime-error':
            self.journal.record('runtime.error', error='ShutdownFault', message='runtime-error')
            raise ShutdownFault('runtime-error')


def runtime_plugin(variant, *, name='shutdown.runtime', replacement=False):
    dependencies = ('actors', 'interaction', 'storage', 'results', 'shutdown.events')
    def install(ctx):
        journal = ctx.require('shutdown.events', 'journal')
        journal.record('install.runtime')
        inner = Runtime(ctx.require('actors', 'actors'), information=ctx.require('interaction', 'information'),
            actions=ctx.require('interaction', 'actions'), store=ctx.require('storage', 'store'),
            results=ctx.require('results', 'results'), step_hooks=ctx.step_hooks, capacity=3 if replacement else 1)
        runtime = PublicRuntime(inner, journal, variant)
        ctx.provide('runtime', runtime)
        ctx.on_quiesce(runtime.close, stage='runtime')
        ctx.on_close(journal.record, 'runtime.resource.close')
    return Plugin(name, dependencies, install)


def shutdown_plan(config):
    pending = asyncio.Event()
    worker_task = None
    runtime_name = 'shutdown.runtime'
    runtime = runtime_plugin(config['variant'])
    # 独立 Host 的资源确实可调用；其退出由本运行的原生资源栈管理。
    async def install_worker(ctx):
        journal = ctx.require('shutdown.events', 'journal')
        journal.record('install.worker')
        class Resource:
            def __init__(self): self.closed = False
            def use(self):
                if self.closed: raise RuntimeError('sibling resource closed')
                return '独立资源仍可用🙂'
            def close(self):
                self.closed = True
                journal.record('sibling.resource.close')
        def sibling_install(other):
            resource = Resource()
            other.provide('resource', resource)
            other.on_close(resource.close)
        sibling = await ctx.enter_context(PluginHost((Plugin('sibling', install=sibling_install),)))
        async def work():
            journal.record('worker.pending', complete_step=ctx.require('storage', 'store').complete_step)
            pending.set()
            try:
                await asyncio.Future()
            finally:
                journal.record('worker.finally')
        async def start():
            nonlocal worker_task
            worker_task = asyncio.create_task(work(), name='shutdown-owned-worker')
            await pending.wait()
        async def stop():
            journal.record('worker.stop')
            if worker_task is not None:
                worker_task.cancel()
                await asyncio.gather(worker_task, return_exceptions=True)
            journal.record('worker.drained')
            journal.record('sibling.use', value=sibling.service('sibling', 'resource').use())
            if config['variant'] == 'worker-error':
                journal.record('worker.error', error='ShutdownFault', message='worker-error')
                raise ShutdownFault('worker-error')
        ctx.provide('start', start)
        ctx.on_quiesce(stop, stage='worker')
        ctx.on_close(journal.record, 'worker.resource.close')
    worker = Plugin('shutdown.worker', ('actors', 'interaction', 'storage', 'results', 'shutdown.events'), install_worker)
    def install_control(ctx):
        journal = ctx.require('shutdown.events', 'journal')
        public = ctx.require(runtime_name, 'runtime')
        async def control():
            await pending.wait()
            journal.record('control.cancel', task_name=public.call_task.get_name())
            public.call_task.cancel()
            await public.finished.wait()
            journal.record('control.finished')
        task = asyncio.create_task(control(), name='shutdown-owned-control')
        async def stop():
            if not task.done(): task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            journal.record('control.drained')
        ctx.on_quiesce(stop, stage='worker')
        ctx.on_close(journal.record, 'control.resource.close')
    control = Plugin('shutdown.control', (runtime_name, 'shutdown.worker', 'shutdown.events'), install_control)
    def install_schedule(ctx):
        journal = ctx.require('shutdown.events', 'journal')
        start = ctx.require('shutdown.worker', 'start')
        class Schedule:
            async def next_step(self, completed):
                number = completed + 1
                if number > config['end']: return None
                async def phase(current):
                    current.activate('a')
                    await current.drain()
                    if number == 2:
                        await start()
                        await asyncio.Future()
                    journal.record('phase.done', number=number)
                return StepPlan(1, (Phase('read', phase),))
        ctx.provide('schedule', Schedule())
    schedule = Plugin('shutdown.schedule', ('shutdown.worker', 'shutdown.events'), install_schedule)
    def rule(current): return DriverResult('completed', {'actor': current.actor.id}, '受控关闭前正常完成')
    roots = list(rule_preset(records=(ActorRecord('a', 'rule'),), run=rule, allows=lambda *args: True,
        schedule=schedule, runtime=runtime, plugins=(journal_plugin(), worker, control)))
    if config['variant'] == 'worker-first':
        roots.remove(worker)
        roots.insert(roots.index(runtime), worker)
    return roots, ('shutdown.schedule', 'schedule'), (runtime_name, 'runtime')


class ExtensionProvider(ScriptedProvider):
    """标准 typed provider；脚本经元工具发现、描述、执行真实领域动作。"""
    def __init__(self, store, threads, config, journal):
        super().__init__(store, threads, config)
        self.journal = journal
        self.turn = ContextVar('extension-provider-turn')

    @asynccontextmanager
    async def activation(self, context):
        token = self.turn.set({'number': 0})
        try:
            async with super().activation(context): yield
        finally:
            self.turn.reset(token)

    async def request_model(self, thread_id, options, *, model_messages=None):
        from pydantic_ai.messages import ModelMessagesTypeAdapter, ModelResponse, TextPart, ToolCallPart
        current = self.current.get()
        actual = model_messages() if callable(model_messages) else model_messages
        typed = ModelMessagesTypeAdapter.dump_python(actual, mode='json')
        through = self.threads.describe(thread_id)['last_seq']
        request = self.threads.record_request(thread_id, provider_options=options,
            physical_request_id=f'{thread_id}:{through}', through=through)
        self.store.transaction(lambda writer: writer.execute(
            'INSERT INTO composition_requests(step,actor,thread_id,request_seq,through,typed,options) VALUES(?,?,?,?,?,?,?)',
            (current.step, current.actor.id, thread_id, request, through, encoded(typed), encoded(options))))
        state = self.turn.get()
        turn = state['number']
        state['number'] += 1
        self.journal.record('provider.request', step=current.step, activation=current.signals[0]['activation_in_step'], turn=turn)
        target = {'namespace': 'probe', 'kind': 'actor', 'key': current.actor.id}
        memory_target = {'namespace': 'memory', 'kind': 'actor', 'key': current.actor.id}
        script = [('ls', {'path': '/context'}), ('read', {'path': '/context/messages.json'}),
            ('action_find', {'target': target, 'query': '', 'limit': 100, 'cursor': None}),
            ('action_describe', {'name': 'probe.record', 'target': target}),
            ('action_invoke', {'name': 'probe.record', 'target': target, 'arguments': {'body': original('实际工具动作', current.step, current.signals[0]['activation_in_step'])}})]
        if self.config['variant'] in ('memory-tools', 'memory-all'):
            script += [('action_find', {'target': memory_target, 'query': '', 'limit': 100, 'cursor': None}),
                ('action_describe', {'name': 'memory.recall', 'target': memory_target}),
                ('action_invoke', {'name': 'memory.recall', 'target': memory_target, 'arguments': {'query': SEED}}),
                ('action_describe', {'name': 'memory.remember', 'target': memory_target}),
                ('action_invoke', {'name': 'memory.remember', 'target': memory_target, 'arguments': {'content': original('主动记忆', current.step, current.signals[0]['activation_in_step'])}})]
        if self.config['mode'] == 'interview':
            parts = [ToolCallPart('submit_result', {'measurement': '完整访谈结果🙂'}, tool_call_id=f'interview-{current.step}-{current.signals[0]["activation_in_step"]}')]
        elif turn < len(script):
            name, arguments = script[turn]
            parts = [ToolCallPart(name, arguments, tool_call_id=f's{current.step}a{current.signals[0]["activation_in_step"]}t{turn}')]
        else:
            parts = [TextPart('完整最终决定🙂；保留本次全部材料与工具结果。')]
        response = ModelResponse(parts=parts, finish_reason='tool_call' if isinstance(parts[0], ToolCallPart) else 'stop',
            timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc))
        sequence = self.threads.append_message(thread_id, {'model_message': ModelMessagesTypeAdapter.dump_python([response], mode='json')[0]})
        return response, sequence, None


def extensions_plan(config):
    from society0.kernel.interaction import Action, ActionResult
    from society0.kernel.llm import LLMPolicy
    from society0.plugins import cognition_plugin, social_plugin, social_source_plugin
    from society0.plugins.cognition import SourceSpec
    roots = [journal_plugin(), social_plugin('ab', config={'embeddings': {'semantic_embeddings': False}}, content_length_limit=-1),
        social_source_plugin(), notification_data_plugin(), notification_source_plugin()]
    def install_capture(ctx):
        provider = ExtensionProvider(ctx.require('storage', 'store'), ctx.require('threads', 'threads'), config, ctx.require('shutdown.events', 'journal'))
        ctx.provide('provider', provider)
        ctx.provide('activation', provider.activation)
        ctx.on_close(ctx.require('shutdown.events', 'journal').record, 'provider.resource.close')
    roots.append(Plugin('capture', ('storage', 'threads', 'shutdown.events'), install_capture, schema=REQUEST_SCHEMA))
    for name in ('A', 'B'):
        def install(ctx, name=name):
            journal, threads = ctx.require('shutdown.events', 'journal'), ctx.require('threads', 'threads')
            @asynccontextmanager
            async def extension(context):
                identity = {'step': context.session.step, 'activation': context.session.signals[0]['activation_in_step'], 'name': name}
                journal.record('extension.enter', **identity)
                context.mounts[name] = original('扩展'+name, context.session.step, identity['activation'])
                def prepare(current):
                    cursor = threads.input_cursor(current.thread_id, 'operating_context')
                    journal.record('extension.prepare', **identity, cursor=cursor)
                    current.messages.append({'role': 'user', 'content': current.mounts[name]})
                context.preparations.append(prepare)
                try:
                    yield
                    journal.record('extension.result', **identity, status=context.result.status)
                finally:
                    journal.record('extension.exit', **identity)
            ctx.provide('extension', extension)
            ctx.on_close(journal.record, 'extension.resource.close', name=name)
        roots.append(Plugin('extension.'+name, ('shutdown.events', 'threads'), install))
    roots.append(cognition_plugin(sources=(SourceSpec('social.main.feed.v1', ('social.source', 'source')),
        SourceSpec('notice.main.v1', ('notice.source', 'source'))), environment='共享环境；保留完整材料。目标 probe/actor/a 支持 probe.record。'))
    def install_observed(ctx):
        builder = ctx.require('cognition', 'input_builder')
        journal, threads = ctx.require('shutdown.events', 'journal'), ctx.require('threads', 'threads')
        async def observed(current):
            batch = await builder(current)
            def effect(context):
                journal.record('source.effects', step=current.step, activation=current.signals[0]['activation_in_step'],
                    cursor=threads.input_cursor(context.thread_id, batch.consumer),
                    receipts=ctx.require('notice.data', 'notifications').receipts(current.actor.id),
                    request_count=ctx.require('storage', 'store').read(lambda view: view.query('SELECT count(*) FROM composition_requests')[0][0]))
            return replace(batch, effects=(*batch.effects, effect))
        ctx.provide('input_builder', observed)
    roots.append(Plugin('observed.input', ('cognition', 'shutdown.events', 'threads', 'notice.data', 'storage'), install_observed))
    def install_action(ctx):
        journal = ctx.require('shutdown.events', 'journal')
        def record(scope, target, arguments):
            journal.record('action.actual', actor=scope.actor, body=arguments['body'])
            return ActionResult('completed', {'body': arguments['body'], 'changed': True})
        disposer = ctx.require('interaction', 'actions').register(Action('probe.record', ('probe', 'actor'), '保存完整正文',
            {'type': 'object', 'properties': {'body': {'type': 'string'}}, 'required': ['body'], 'additionalProperties': False}, record))
        ctx.on_close(disposer)
    roots.append(Plugin('probe.action', ('interaction', 'shutdown.events'), install_action))
    extensions = [('extension.A', 'extension'), ('extension.B', 'extension')]
    memory_enabled = config['variant'].startswith('memory-')
    if memory_enabled:
        from types import SimpleNamespace
        from society0.kernel.memory import MemoryPolicy
        from society0.kernel.services import memory_plugin
        import chromadb
        owned = {}
        def resources(ctx):
            journal = ctx.require('shutdown.events', 'journal')
            owned['journal'] = journal
            owned['threads'] = ctx.require('threads', 'threads')
            from chromadb.config import Settings
            client = chromadb.EphemeralClient(settings=Settings(anonymized_telemetry=False))
            ctx.provide('client', client)
            async def embed(texts, *, metadata):
                journal.record('memory.embed', texts=list(texts), metadata=metadata)
                return [[float(len(text) % 7), 1.0] for text in texts]
            ctx.provide('profiles', {'main': SimpleNamespace(embed=embed)})
            def release():
                name = 'society-memory-' + journal.store.run_id
                for collection in client.list_collections():
                    if collection.name == name: client.delete_collection(name=name)
                journal.record('memory.client.release')
                owned.clear()
            ctx.on_close(release)
        roots.append(Plugin('memory.resources', ('shutdown.events', 'threads'), resources))
        async def extract(actor, tid, messages, *, metadata):
            journal = owned['journal']
            journal.record('memory.extract', actor=actor, messages=messages, metadata=metadata)
            return [{'content': original('自动记忆', journal.store.complete_step + 1, metadata['through'])}]
        def recall_query(current):
            threads = owned['threads']
            tid = current.cursors['thread_id']
            owned['journal'].record('memory.recall.inputs', step=current.step, activation=current.signals[0]['activation_in_step'],
                messages=threads.snapshot_messages(tid)['messages'], cursor=threads.input_cursor(tid, 'operating_context'))
            return SEED
        roots.append(memory_plugin(client=('memory.resources', 'client'), embedding=('memory.resources', 'profiles', 'main'),
            extract=extract, policy=MemoryPolicy(auto_write=config['variant'] in ('memory-write', 'memory-all'),
                auto_recall=config['variant'] in ('memory-recall', 'memory-all'),
                active_tools=config['variant'] in ('memory-tools', 'memory-all')), recall_query=recall_query))
        extensions.append(('memory', 'extension'))
    extensions.append(('capture', 'activation'))
    runtime_name = 'replacement.runtime' if config['variant'] == 'runtime-replacement' else 'runtime'
    runtime = runtime_plugin(config['variant'], name=runtime_name, replacement=True) if config['variant'] == 'runtime-replacement' else None
    schedule_name = 'replacement.schedule' if runtime else 'schedule'
    def install_schedule(ctx):
        journal = ctx.require('shutdown.events', 'journal')
        social, notices = ctx.require('social', 'mechanism'), ctx.require('notice.data', 'notifications')
        class Schedule:
            async def next_step(self, completed):
                number = completed + 1
                journal.record('schedule.call', completed=completed)
                if number > config['end']: return None
                async def phase(current):
                    if memory_enabled and number == 1:
                        await ctx.require('memory', 'memory').seed('a', 'initial', timestamp=0, entries=[{'content': SEED}])
                    for activation in (1, 2):
                        if number == 1 and activation == 1:
                            social.execute('publish_post', 'b', 'b', {'content': original('social', 1, 1)}, 1)
                            notices.publish('a', original('notice', 1, 1))
                        elif activation == 2:
                            social.execute('publish_post', 'b', 'b', {'content': original('social', number, 2)}, 1)
                        else:
                            notices.publish('a', original('notice', number, 1))
                        current.activate('a', {'activation_in_step': activation})
                        await current.drain()
                    journal.record('phase.done', number=number)
                return StepPlan(1, (Phase('read', phase),))
        if config['variant'] == 'runtime-replacement':
            class ReplacementSchedule:
                def __init__(self): self.inner = Schedule()
                async def next_step(self, completed):
                    journal.record('replacement.schedule.called', completed=completed)
                    return await self.inner.next_step(completed)
            ctx.provide('schedule', ReplacementSchedule())
        else:
            ctx.provide('schedule', Schedule())
    schedule = Plugin(schedule_name, ('social', 'notice.data', 'shutdown.events', *(('memory',) if memory_enabled else ())), install_schedule)
    policy = LLMPolicy(mode=config['mode'], result_schema={'type': 'object', 'properties': {'measurement': {'type': 'string'}},
        'required': ['measurement'], 'additionalProperties': False} if config['mode'] == 'interview' else None)
    roots = llm_preset(records=(ActorRecord(actor, 'llm', persona='完整主体背景🙂'+actor, state={'主观状态': actor}) for actor in 'ab'),
        provider=('capture', 'provider'), input_builder=('observed.input', 'input_builder'), allows=lambda *args: True,
        schedule=schedule, plugins=roots, extensions=extensions, policy=policy, runtime=runtime)
    return roots, (schedule_name, 'schedule'), (runtime_name, 'runtime')


def build(config):
    if not config['release'].get('commit'): raise ValueError('release.commit is required')
    if config.get('provider') != 'scripted': raise ValueError('provider must be scripted')
    case = config['case']
    variant = config.get('variant', 'default')
    if case not in ('shutdown', 'extensions') or variant not in (SHUTDOWN if case == 'shutdown' else EXTENSIONS):
        raise ValueError('unsupported shutdown/extensions case or variant')
    if config.get('start', 1) != 1 or type(config.get('end', 2)) is not int or config.get('end', 2) < 1:
        raise ValueError('start=1 and positive end are required')
    if case == 'shutdown' and config.get('end', 2) != 2: raise ValueError('shutdown requires end=2')
    mode = config.get('mode', 'decision')
    if mode not in ('decision', 'interview'): raise ValueError('unsupported mode')
    config = {**config, 'variant': variant, 'start': 1, 'end': config.get('end', 2), 'mode': mode}
    plugins, schedule, runtime = shutdown_plan(config) if case == 'shutdown' else extensions_plan(config)
    dependencies = {'python': sys.version, 'apsw': version('apsw'), 'backports-zstd': version('backports-zstd')}
    if case == 'extensions':
        dependencies['pydantic-ai-slim'] = version('pydantic-ai-slim')
        if variant.startswith('memory-'): dependencies['chromadb'] = version('chromadb')
    return RunPlan(plugins, RunContract(release=config['release'], dependencies=dependencies, configuration=config,
        time={'start': 1, 'end': config['end']}, budgets={'capacity': 3 if variant == 'runtime-replacement' else 1,
        'max_activations': DEFAULT_MAX_ACTIVATIONS, 'max_turns': None, 'max_action_calls': None}), schedule=schedule, runtime=runtime)


def _check(identifier, expected, actual, location):
    return {'id': identifier, 'passed': expected == actual, 'expected': expected, 'actual': actual, 'location': location}


def _contains(value, text):
    if isinstance(value, str):
        if text in value: return True
        payload = value if value.startswith(('{', '[')) else value.partition('\n')[2]
        if payload.startswith(('{', '[')):
            try:
                return _contains(json.loads(payload), text)
            except json.JSONDecodeError:
                return False
        return False
    if isinstance(value, dict): return any(_contains(item, text) for item in value.values())
    if isinstance(value, (list, tuple)): return any(_contains(item, text) for item in value)
    return False


def _events(reader):
    return reader.read(lambda view: [{'event': event, 'detail': json.loads(detail)} for event, detail in view.iter_query(
        'SELECT event,detail FROM shutdown_extension_events ORDER BY id')])


def validate(path):
    path = Path(path)
    manifest = json.loads((path / 'runner.json').read_text())
    status = json.loads((path / 'runner-status.json').read_text())
    config = manifest['contract']['configuration']
    checks = []
    with StageReader(path) as reader:
        events = _events(reader)
        names = [row['event'] for row in events]
        complete = reader.read(lambda view: view.complete_step)
        checks.append(_check('contract.identity', [config['case'], config['variant'], 'scripted', 1, config['end']],
            [config['case'], config['variant'], config['provider'], manifest['contract']['time']['start'], manifest['contract']['time']['end']], 'runner.json/contract'))
        checks.append(_check('contract.commit', True, bool(manifest['contract']['release']['commit']), 'runner.json/contract/release/commit'))
        checks.append(_check('runtime.budgets', manifest['contract']['budgets']['capacity'], manifest['effective_runtime']['capacity'], 'runner.json/effective_runtime'))
        checks.append(_check('runtime.activation-budget', manifest['contract']['budgets']['max_activations'], manifest['effective_runtime']['max_activations'], 'runner.json/effective_runtime'))
        if config['case'] == 'shutdown':
            descriptor = json.loads((path / 'steps' / f'{1:020d}.json').read_text())
            checks.append(_check('shutdown.complete-descriptor', 1, descriptor['step'], 'steps/00000000000000000001.json/step'))
            checks.append(_check('shutdown.step2-descriptor-absent', False, (path / 'steps' / f'{2:020d}.json').exists(), 'steps/00000000000000000002.json'))
            checks += [_check('shutdown.failed', 'failed', status['status'], 'runner-status.json/status'),
                _check('shutdown.complete', 1, complete, '_stage_runtime/complete_step'),
                _check('shutdown.status-complete', 1, status['complete_step'], 'runner-status.json/complete_step')]
            external = json.loads((path / 'shutdown-exit.json').read_text())
            # 独立进程资源日志与真实 SQL 一致；恢复继承的事件不属于本次关闭文件。
            checks.append(_check('shutdown.external-sql', events, external[:len(events)], 'shutdown-exit.json + shutdown_extension_events'))
            events = external
            names = [row['event'] for row in events]
            chain = ['worker.pending', 'control.cancel', 'runtime.finally', 'runtime.stop', 'runtime.rejected', 'runtime.drained', 'worker.stop', 'worker.finally', 'worker.drained', 'sibling.use']
            positions = [next((index for index, row in enumerate(events) if row['event'] == name
                and (name != 'runtime.finally' or row['detail'].get('number') == 2)), -1) for name in chain]
            checks.append(_check('shutdown.stages', chain, [events[index]['event'] for index in sorted(positions) if index >= 0], 'shutdown-exit.json/event'))
            for event in ('runtime.resource.close', 'worker.resource.close', 'control.resource.close', 'sibling.resource.close', 'journal.close'):
                checks.append(_check('shutdown.once.'+event, 1, names.count(event), 'shutdown_extension_events/'+event))
            first_release = min((index for index, name in enumerate(names) if name.endswith('resource.close')), default=-1)
            checks.append(_check('shutdown.release-after-drain', True, 'worker.drained' in names and first_release > names.index('worker.drained'), 'shutdown_extension_events/event'))
            checks.append(_check('shutdown.no-step2-publish', False, any(row == {'event': 'phase.done', 'detail': {'number': 2}} for row in events), 'shutdown_extension_events/phase.done'))
            installs = [name for name in names if name.startswith('install.')]
            checks.append(_check('shutdown.install-order', ['install.worker', 'install.runtime'] if config['variant'] == 'worker-first' else ['install.runtime', 'install.worker'], installs, 'shutdown_extension_events/install'))
            error = config['variant'] if config['variant'].endswith('-error') else None
            checks.append(_check('shutdown.expected-error', 'ShutdownFault' if error else 'CancelledError', status.get('error'), 'runner-status.json/error'))
            checks.append(_check('shutdown.cleanup-error', [error.split('-')[0]+'.error'] if error else [], [name for name in names if name.endswith('.error')], 'shutdown-exit.json/error'))
            checks.append(_check('shutdown.cleanup-error-detail', [{'error': 'ShutdownFault', 'message': error}] if error else [],
                [row['detail'] for row in events if row['event'].endswith('.error')], 'shutdown-exit.json/error/detail'))
            checks.append(_check('shutdown.sibling', ['独立资源仍可用🙂'], [row['detail']['value'] for row in events if row['event'] == 'sibling.use'], 'shutdown_extension_events/sibling.use'))
            return checks
        checks += [_check('extensions.completed', 'completed', status['status'], 'runner-status.json/status'),
            _check('extensions.complete', config['end'], complete, '_stage_runtime/complete_step'),
            _check('extensions.diagnostics', 0, status['diagnostic_errors'], 'runner-status.json/diagnostic_errors')]
        requests = reader.read(lambda view: [(step, json.loads(typed), json.loads(options)) for step, typed, options in view.iter_query(
            'SELECT step,typed,options FROM composition_requests ORDER BY id')])
        from society0.kernel.threads import ThreadStore
        threads = ThreadStore(reader)
        request_refs = reader.read(lambda view: list(view.iter_query('SELECT thread_id,request_seq,through FROM composition_requests ORDER BY id')))
        for index, ((tid, sequence, through), (_, _, options)) in enumerate(zip(request_refs, requests)):
            saved = threads.read_request(tid, sequence)
            checks.append(_check(f'extensions.request-reference.{index}', [through, options, f'{tid}:{through}'],
                [saved['through'], saved['provider_options'], saved['physical_request_id']], f'Thread/{tid}/request/{sequence}'))
        provider_events = [row['detail'] for row in events if row['event'] == 'provider.request']
        checks.append(_check('extensions.request-count', len(provider_events), len(requests), 'composition_requests/count'))
        sdk_groups = {}
        for identity, (_, typed, _) in zip(provider_events, requests):
            sdk_ids = {message['run_id'] for message in typed if message.get('run_id') is not None}
            group = (identity['step'], identity['activation'])
            sdk_groups.setdefault(group, set()).update(sdk_ids)
        checks.append(_check('extensions.sdk-reference', True, all(len(ids) <= 1 for ids in sdk_groups.values()), 'composition_requests/typed/run_id by activation'))
        expected_tools = {'submit_result'} if config['mode'] == 'interview' else {'ls', 'read', 'find', 'grep', 'action_find', 'action_describe', 'action_invoke'}
        checks.append(_check('extensions.standard-tools', True,
            all({tool['function']['name'] for tool in options['tools']} == expected_tools and options['parallel_tool_calls'] is False for _, _, options in requests), 'composition_requests/options/tools'))
        for step in range(1, config['end'] + 1):
            for activation in (1, 2):
                selected = [row['event']+(':'+row['detail']['name'] if 'name' in row['detail'] else '') for row in events
                    if row['detail'].get('step') == step and row['detail'].get('activation') == activation
                    and row['event'] in ('extension.enter', 'extension.prepare', 'source.effects', 'extension.result', 'extension.exit')]
                expected = ['extension.enter:A', 'extension.enter:B', 'extension.prepare:A', 'extension.prepare:B', 'source.effects',
                    'extension.result:B', 'extension.exit:B', 'extension.result:A', 'extension.exit:A']
                checks.append(_check(f'extensions.order.{step}.{activation}', expected, selected, 'shutdown_extension_events/activation'))
                effect = next(row['detail'] for row in events if row['event'] == 'source.effects'
                    and row['detail']['step'] == step and row['detail']['activation'] == activation)
                checks.append(_check(f'extensions.saved-cursor.{step}.{activation}', ['notice.main.v1', 'social.main.feed.v1'],
                    sorted(effect['cursor']['position']), 'shutdown_extension_events/source.effects/cursor/position'))
                checks.append(_check(f'extensions.receipts.{step}.{activation}', list(range(1, step + 1)),
                    [notice_id for _, notice_id in effect['receipts']], 'shutdown_extension_events/source.effects/receipts'))
                effect_index = next(index for index, row in enumerate(events) if row['event'] == 'source.effects' and row['detail'] == effect)
                provider_index = next(index for index, row in enumerate(events) if row['event'] == 'provider.request'
                    and row['detail']['step'] == step and row['detail']['activation'] == activation)
                checks.append(_check(f'extensions.effects-before-provider.{step}.{activation}', True, effect_index < provider_index,
                    'shutdown_extension_events/source.effects->provider.request'))
                for name in ('A', 'B'):
                    checks.append(_check(f'extensions.provider-original.{step}.{activation}.{name}', True,
                        any(_contains(typed, original('扩展'+name, step, activation)) for number, typed, _ in requests if number == step), 'composition_requests/typed'))
        for step in range(1, config['end'] + 1):
            for domain, activations in (('social', (1, 2) if step == 1 else (2,)), ('notice', (1,))):
                for activation in activations:
                    body = original(domain, step, activation)
                    checks.append(_check(f'extensions.source-original.{domain}.{step}.{activation}', True,
                        any(_contains(typed, body) for number, typed, _ in requests if number == config['end']), 'composition_requests/typed/final-step'))
        checks.append(_check('extensions.actual-actions', 0 if config['mode'] == 'interview' else config['end'] * 2, names.count('action.actual'), 'shutdown_extension_events/action.actual'))
        expected_actions = [] if config['mode'] == 'interview' else [original('实际工具动作', step, activation)
            for step in range(1, config['end'] + 1) for activation in (1, 2)]
        checks.append(_check('extensions.action-originals', expected_actions,
            [row['detail']['body'] for row in events if row['event'] == 'action.actual'], 'shutdown_extension_events/action.actual/body'))
        outcomes = reader.read(lambda view: [json.loads(result)['status'] for (result,) in view.iter_query('SELECT result FROM composition_outcomes ORDER BY id')])
        checks.append(_check('extensions.outcomes', ['completed'] * (config['end'] * 2), outcomes, 'composition_outcomes/result/status'))
        if config['variant'].startswith('memory-'):
            write = config['variant'] in ('memory-write', 'memory-all') and config['mode'] != 'interview'
            recall = config['variant'] in ('memory-recall', 'memory-all')
            tools = config['variant'] in ('memory-tools', 'memory-all') and config['mode'] != 'interview'
            from society0.kernel.threads import ThreadStore
            threads = ThreadStore(reader)
            tid = threads.list_threads(actor='a')['items'][0]['id']
            thread_events = []
            after = 0
            while True:
                page = threads.tail(tid, after_seq=after)
                thread_events.extend(page['items'])
                if page['next_seq'] >= page['total']: break
                after = page['next_seq']
            policies = [item['payload'] for item in thread_events if item['kind'] == 'memory_policy']
            expected_policies = [{'auto_write': config['variant'] in ('memory-write', 'memory-all'),
                'auto_recall': recall, 'active_tools': config['variant'] in ('memory-tools', 'memory-all'),
                'recall_top_k': 10, 'effective_auto_write': write, 'step': step}
                for step in range(1, config['end'] + 1) for _ in (1, 2)]
            checks.append(_check('memory.actual-policies', expected_policies, policies, 'Thread/memory_policy'))
            results = [item['payload'] for item in thread_events if item['kind'] == 'action_result'
                and item['payload']['name'].startswith('memory.')]
            expected_results = ['memory.recall', 'memory.remember'] * (config['end'] * 2) if tools else []
            checks.append(_check('memory.actual-tools', expected_results,
                [item['name'] for item in results if item['result']['status'] == 'completed'], 'Thread/action_result/name,status'))
            checks.append(_check('memory.pending-jobs', 0, reader.read(lambda view: view.query("SELECT count(*) FROM memory_jobs WHERE state!='complete'")[0][0]), 'memory_jobs/state'))
            checks.append(_check('memory.recall-after-saved-input', config['end'] * 2 if recall else 0,
                names.count('memory.recall.inputs'), 'shutdown_extension_events/memory.recall.inputs'))
            for row in events:
                if row['event'] == 'memory.recall.inputs':
                    checks.append(_check('memory.recall.full-dual-source', True,
                        _contains(row['detail']['messages'], original('social', 1, 1))
                        and _contains(row['detail']['messages'], original('notice', 1, 1)), 'shutdown_extension_events/memory.recall.inputs/messages'))
            rows = reader.read(lambda view: view.query('SELECT count(*) FROM memory_rows')[0][0])
            checks.append(_check('memory.rows', 1 + config['end'] * 2 * (int(write) + int(tools)), rows, 'memory_rows/count'))
            checks.append(_check('memory.extraction', config['end'] * 2 if write else 0, names.count('memory.extract'), 'shutdown_extension_events/memory.extract'))
            first = requests[0][1]
            checks.append(_check('memory.auto-recall-consumed', recall, _contains(first, SEED), 'composition_requests/id=1/typed'))
            all_requests = [typed for _, typed, _ in requests]
            checks.append(_check('memory.active-recall-consumed', tools, _contains(all_requests, 'memory.recall') and _contains(all_requests, SEED), 'composition_requests/typed/tool-results'))
            for event in events:
                if event['event'] == 'memory.extract':
                    text = event['detail']['messages']
                    checks.append(_check('memory.extract.full-dual-source', True,
                        _contains(text, original('social', 1, 1)) and _contains(text, original('notice', 1, 1)),  'shutdown_extension_events/memory.extract/messages'))
        if config['variant'] == 'runtime-replacement':
            checks.append(_check('replacement.default-absent', False, any(item['name'] == 'runtime' for item in manifest['plugins']), 'runner.json/plugins'))
            checks.append(_check('replacement.calls', list(range(1, config['end'] + 1)), [row['detail']['number'] for row in events if row['event'] == 'runtime.call'], 'shutdown_extension_events/runtime.call'))
            checks.append(_check('replacement.schedule', list(range(config['end'] + 1)), [row['detail']['completed'] for row in events if row['event'] == 'replacement.schedule.called'], 'shutdown_extension_events/replacement.schedule.called'))
            checks.append(_check('replacement.services', [['replacement.runtime', 'runtime'], ['replacement.schedule', 'schedule']],
                [manifest['runtime'], manifest['schedule']], 'runner.json/runtime,schedule'))
            checks.append(_check('replacement.default-schedule-absent', False,
                any(item['name'] == 'schedule' for item in manifest['plugins']), 'runner.json/plugins'))
    return checks


def _comparable(path):
    from society0.kernel.threads import ThreadStore
    manifest = json.loads((Path(path) / 'runner.json').read_text())
    config = manifest['contract']['configuration']
    with StageReader(path) as reader:
        threads = ThreadStore(reader)
        heads = reader.read(lambda view: view.query('SELECT id FROM thread_heads ORDER BY id'))
        messages = [threads.snapshot_messages(tid)['messages'] for (tid,) in heads]
        requests = reader.read(lambda view: [(step, json.loads(typed), json.loads(options)) for step, typed, options in view.iter_query('SELECT step,typed,options FROM composition_requests ORDER BY id')])
        bodies = []
        replacements = {}
        if config['variant'].startswith('memory-'):
            from society0.kernel.memory import Memory
            memory = Memory(reader, threads, embed=None, client=None)
            ids = reader.read(lambda view: view.query('SELECT id FROM memory_rows ORDER BY actor,timestamp,id'))
            bodies = [memory.get(identifier, actor='a') for (identifier,) in ids]
            for index, item in enumerate(sorted(bodies, key=lambda item: (item['timestamp'], item['content']))):
                replacements[item['id']] = 'memory:'+str(index)
            bodies = sorted(({key: value for key, value in item.items() if key != 'id'} for item in bodies), key=lambda item: (item['timestamp'], item['content']))
        # 规范化生成字段：memory UUID（全文对应）、Page.revision.run_id、typed请求/part时间与SDK run_id。
        # Thread 与 tool_call ID、正文、schema、结果、分页水位和其余字段逐值保留。
        run_ids = {manifest['run_id']}
        if manifest['source'] is not None:
            source_manifest = json.loads((Path(manifest['source']['path']) / 'runner.json').read_text())
            run_ids.add(source_manifest['run_id'])
        def normalize(value):
            if isinstance(value, str):
                if value.startswith(('{', '[')):
                    try:
                        return json.dumps(normalize(json.loads(value)), ensure_ascii=False)
                    except json.JSONDecodeError:
                        pass
                return replacements.get(value, value)
            if isinstance(value, (list, tuple)): return [normalize(item) for item in value]
            if isinstance(value, dict):
                result = {key: normalize(item) for key, item in value.items()}
                if (value.get('kind') == 'request' or value.get('part_kind') in ('system-prompt', 'user-prompt', 'tool-return', 'retry-prompt')) and 'timestamp' in value:
                    result['timestamp'] = 'activation-generated-timestamp'
                if value.get('kind') in ('request', 'response') and value.get('run_id') is not None:
                    result['run_id'] = 'sdk-activation-id'
                revision = value.get('revision')
                if isinstance(revision, list) and revision and revision[0] in run_ids:
                    result['revision'] = ['run-identity', *revision[1:]]
                return result
            return value
        actions = [row for row in _events(reader) if row['event'] == 'action.actual']
        receipts = reader.read(lambda view: view.query('SELECT actor,thread_id,notice_id FROM "notice.data_receipts" ORDER BY actor,thread_id,notice_id'))
        return {'messages': normalize(messages), 'requests': normalize(requests), 'memory': bodies, 'actions': actions, 'receipts': receipts}


def compare(continuous, restored):
    left, right = _comparable(continuous), _comparable(restored)
    manifests = [json.loads((Path(path) / 'runner.json').read_text()) for path in (continuous, restored)]
    checks = [_check('restore.new-run-id', True, manifests[0]['run_id'] != manifests[1]['run_id'], 'runner.json/run_id'),
        _check('restore.source', {'path': str(Path(continuous).resolve()), 'step': 1},
            None if manifests[1]['source'] is None else {'path': str(Path(manifests[1]['source']['path']).resolve()), 'step': manifests[1]['source']['step']}, 'runner.json/source')]
    checks.extend(_check('restore.'+key, left[key], right[key], key) for key in left)
    return checks
