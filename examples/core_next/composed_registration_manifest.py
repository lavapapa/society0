"""公开规则消费者：独立登记、资源所有权与实际组合清单。"""
from dataclasses import asdict, replace
import json
from pathlib import Path
import sys

from society0.kernel.actors import ActorRecord
from society0.kernel.interaction import (
    Action, ActionResult, Actions, DocumentChunk, Information, InteractionScope,
    Moment, Page, Ref, ScopeClosed, Unavailable,
)
from society0.kernel.plugins import Plugin, PluginHost
from society0.kernel.results import StepResult
from society0.kernel.runner import RunContract, RunPlan
from society0.kernel.runtime import DriverResult, Phase
from society0.kernel.schedule import SequenceSchedule, activate, schedule_plugin
from society0.kernel.storage import StageReader
from society0.presets import rule_preset

BODY = '完整登记材料：能力、原文、预算与所有权均由公开消费者读取。中文🙂\n' * 12
PARAMETERS = {'type': 'object', 'properties': {'step': {'type': 'integer'}},
              'required': ['step'], 'additionalProperties': False}
SCHEMA = (
    'CREATE TABLE composition_observations(step INTEGER PRIMARY KEY,body TEXT NOT NULL)',
    'CREATE TABLE composition_actions(step INTEGER NOT NULL,actor TEXT NOT NULL,name TEXT NOT NULL,body TEXT NOT NULL,PRIMARY KEY(step,actor,name))',
)
VARIANTS = {'registration': ('default', 're-register', 'owned', 'install-failure'),
            'manifest': ('default', 'alternate-tree', 'write-failure')}


class Materials:
    def __init__(self, name, record):
        self.name, self.record, self.closed = name, record, 0

    def ref(self, path):
        return Ref('composition', 'document', path)

    def list(self, scope, path, *, limit, cursor):
        return Page([self.ref(path + '/body')], 1, None, 1)

    def read(self, scope, path, *, offset, size):
        if self.closed:
            raise RuntimeError('material provider is closed')
        body = (self.name + '\n' + BODY).encode()
        end = min(offset + size, len(body))
        return DocumentChunk(body[offset:end], len(body), end if end < len(body) else None,
                             1, self.ref(path))

    def close(self):
        self.closed += 1
        self.record('provider-close', name=self.name, count=self.closed)


async def _read_all(information, path):
    blocks, offset = [], 0
    while True:
        chunk = await information.read(path, offset=offset, size=37)
        blocks.append(chunk.data)
        if chunk.next_offset is None:
            return b''.join(blocks).decode()
        offset = chunk.next_offset


def build(config):
    case, variant = config['case'], config.get('variant', 'default')
    if case not in VARIANTS or variant not in VARIANTS[case]:
        raise ValueError('unknown registration/manifest case or variant')
    if config['provider'] != 'scripted' or config.get('start',1) != 1 or config.get('end',2) != 2:
        raise ValueError('these rule cases require scripted provider, start=1, end=2')
    if not config['release']['commit']:
        raise ValueError('release.commit is required')
    frozen = {**config, 'variant': variant, 'start':1, 'end':2}
    shared, controls, old_handles, scopes = {}, {}, [], []

    async def evidence_install(ctx):
        store = ctx.require('storage', 'store')
        shared['store'] = store
        def record(event, **values):
            with (store.path/'composition-evidence.jsonl').open('a', encoding='utf-8') as stream:
                stream.write(json.dumps({'event': event, **values}, ensure_ascii=False) + '\n')
        shared['record'] = record
        record('configuration', config=frozen)
        # 独立 Host 由本次运行拥有；失败 Host 清理后仍真实读取其资源。
        independent_contexts = []
        def check_closed_context():
            try:
                independent_contexts[0].require('independent', 'information')
            except RuntimeError as error:
                record('closed-context', error=type(error).__name__, message=str(error))
        ctx.on_close(check_closed_context)
        def independent_install(other):
            independent_contexts.append(other)
            info = Information(lambda *args: True)
            provider = Materials('independent', record)
            info.mount('/independent', provider, owned=True)
            other.provide('information', info)
            other.on_close(info.close)
        independent = await ctx.enter_context(PluginHost([Plugin('independent', install=independent_install)]))
        async def after_registries():
            for handle in old_handles:
                handle()  # 注册表已关闭，旧 handle 不触及已关闭索引。
            with InteractionScope('a', Moment(0, 'cleanup')) as scope:
                text = await _read_all(independent.service('independent', 'information').bound(scope), '/independent/body')
            record('post-registry-close', old_handles=len(old_handles),
                   independent_text=text)
        ctx.on_close(after_registries)
    evidence = Plugin('composition.evidence', ('storage',), evidence_install, schema=SCHEMA)

    async def audit_install(ctx):
        info, actions = ctx.require('interaction', 'information'), ctx.require('interaction', 'actions')
        shared.update(information=info, actions=actions)
        async def audit():
            with InteractionScope('a', Moment(0, 'cleanup')) as scope:
                paths = [item['path'] for item in (await info.list(scope, '/')).items]
                names = [item.name for item in (await actions.find(scope, Ref('composition', 'item', 'one'))).items]
            expired = 0
            for scope in scopes:
                try:
                    scope.check_active()
                except ScopeClosed:
                    expired += 1
            shared['record']('contributions-cleaned', paths=paths, actions=names,
                             expired_scopes=expired, borrowed_closed=shared.get('borrowed').closed if 'borrowed' in shared else 0)
        ctx.on_close(audit)
    audit = Plugin('composition.audit', ('interaction', 'composition.evidence'), audit_install)

    def contribution(name, *, owned=False, borrowed=False, duplicate=False):
        def install(ctx):
            info, actions = ctx.require('interaction', 'information'), ctx.require('interaction', 'actions')
            provider = shared.get('owned') if owned else None
            if provider is None:
                provider = Materials('owned' if owned else name, shared['record'])
                if owned:
                    shared['owned'] = provider
                elif borrowed:
                    shared['borrowed'] = provider
                else:
                    ctx.on_close(provider.close)
            action_name = 'composition.A' if duplicate else 'composition.' + name
            def register(text='needle ' + name, tag=name, on_close=None):
                remove_path = info.mount('/' + name, provider, owned=owned)
                if on_close is not None:
                    on_close(remove_path)
                def handler(scope, target, arguments):
                    value = {'name': name, 'text': text, 'step': arguments['step']}
                    shared['store'].transaction(lambda w: w.execute(
                        'INSERT INTO composition_actions VALUES(?,?,?,?)',
                        (arguments['step'], scope.actor, action_name, json.dumps(value, ensure_ascii=False))))
                    return ActionResult('completed', value)
                remove_action = actions.register(Action(action_name, ('composition', 'item'), text,
                    PARAMETERS, handler, tags=(tag, 'shared')), dependencies=('composition_actions',))
                handles = (remove_path, remove_action)
                old_handles.extend(handles)
                return handles
            handles = register(on_close=ctx.on_close)
            for handle in handles[1:]:
                ctx.on_close(handle)
            controls[name] = {'handles': handles, 'provider': provider, 'register': register}
            # 后续同名重登记由安装时注册的清理器释放当前贡献。
            def dispose_current():
                for handle in controls[name]['handles']:
                    handle()
            ctx.on_close(dispose_current)
            shared['record']('installed', name=name)
        return Plugin('contribution.' + name, ('interaction', 'composition.audit', 'composition.evidence'), install)

    async def registration_rule(session):
        info, actions, target = session.information, session.actions, Ref('composition', 'item', 'one')
        scopes.append(session.scope)
        control = controls['A']
        # 每个步骤以同一公开贡献开始，恢复后的新 registry 采用相同规则。
        if session.step == 2:
            for handle in control['handles']:
                handle()
            control['handles'] = control['register']()
        before_paths = await info.list('/', limit=1)
        before_actions = await actions.find(target, query='needle', limit=1)
        before_text = await _read_all(info, '/A/body')
        old = control['handles']
        for handle in old:
            handle(); handle()
        observed = {'before_text': before_text, 'before_paths_total': before_paths.total,
                    'before_actions_total': before_actions.total}
        for key, operation in (
            ('path_cursor', lambda: info.list('/', limit=1, cursor=before_paths.next_cursor)),
            ('action_cursor', lambda: actions.find(target, query='needle', limit=1, cursor=before_actions.next_cursor)),
            ('removed_read', lambda: info.read('/A/body')),
            ('removed_schema', lambda: actions.describe('composition.A', target)),
        ):
            try:
                await operation()
            except (ValueError, Unavailable) as error:
                observed[key] = type(error).__name__
        observed['removed_invoke'] = asdict(await actions.invoke('composition.A', target, {'step': session.step}))
        observed['removed_fts'] = (await actions.find(target, query='needle A')).total
        observed['removed_tags'] = (await actions.find(target, tags=('A',))).total
        if variant == 're-register':
            control['handles'] = control['register']('replacement token', 'replacement')
            for handle in old:
                handle()
            observed['replacement_text'] = await _read_all(info, '/A/body')
            observed['replacement_schema'] = asdict(await actions.describe('composition.A', target))
            observed['replacement_find'] = [item.name for item in (await actions.find(target, query='token', tags=('replacement',))).items]
            observed['replacement_result'] = asdict(await actions.invoke('composition.A', target, {'step': session.step}))
        observed['paths'] = [item['path'] for item in (await info.list('/')).items]
        observed['B_text'] = await _read_all(info, '/B/body')
        observed['B_find'] = [item.name for item in (await actions.find(target, query='needle B', tags=('B',))).items]
        observed['B_schema'] = asdict(await actions.describe('composition.B', target))
        observed['B_result'] = asdict(await actions.invoke('composition.B', target, {'step': session.step}))
        observed['provider_closed'] = control['provider'].closed
        if variant == 'owned':
            observed['borrowed_text'] = await _read_all(info, '/borrowed/body')
            observed['borrowed_closed'] = shared['borrowed'].closed
        observed = json.loads(json.dumps(observed, ensure_ascii=False))
        shared['store'].transaction(lambda w: w.execute('INSERT INTO composition_observations VALUES(?,?)',
            (session.step, json.dumps(observed, ensure_ascii=False))))
        return DriverResult('completed', observed, 'registration')

    selected = 'external.policy.alternate' if variant == 'alternate-tree' else 'external.policy'
    def policy_install(ctx):
        policy = lambda text: {'policy': selected, 'full_text': text, 'characters': len(text)}
        shared['policy'] = policy
        ctx.provide('policy', policy)
        shared['record']('policy-installed', name=selected)
    policy = Plugin(selected, ('composition.evidence',), policy_install)
    def consumer_install(ctx):
        shared['policy'] = ctx.require(selected, 'policy')
        if variant == 'write-failure':
            path = ctx.require('storage', 'store').path/'runner.json'
            path.mkdir()
            shared['record']('manifest-blocker', name=path.name, kind='directory')
    consumer = Plugin('composition.consumer', (selected, 'storage', 'composition.evidence'), consumer_install,
                      schema_requires=('composition.evidence',))
    async def manifest_rule(session):
        value = shared['policy'](BODY)
        shared['store'].transaction(lambda w: w.execute('INSERT INTO composition_observations VALUES(?,?)',
            (session.step, json.dumps(value, ensure_ascii=False))))
        return DriverResult('completed', value, 'manifest')

    async def decide(context):
        results = await activate(context, ('a',))
        return StepResult(metrics={'decisions': len(results)}, tables={'decisions': [item.result.value for item in results]})
    extensions = (evidence, audit, contribution('A', owned=variant == 'owned'),
                  contribution('B', owned=variant == 'owned')) if case == 'registration' else (evidence, policy, consumer)
    if case == 'registration' and variant == 'owned':
        extensions += (contribution('borrowed', borrowed=True),)
    if case == 'registration' and variant == 'install-failure':
        extensions += (contribution('C', duplicate=True),)
    plugins = rule_preset(records=[ActorRecord('a', 'rule')], run=registration_rule if case == 'registration' else manifest_rule,
        allows=lambda *args: True, schedule=schedule_plugin(SequenceSchedule((1, 2), (Phase('consume', decide, incomplete='fail_step'),))),
        plugins=extensions)
    # evidence 拥有独立 Host，并在主登记表关闭后消费旧 handle 和 context。
    plugins = tuple(replace(item, requires=(*item.requires, 'composition.evidence')) if item.name == 'interaction' else item for item in plugins)
    if case == 'manifest':
        nested = Plugin('composition.branch.alternate' if variant == 'alternate-tree' else 'composition.branch', includes=plugins)
        plugins = (Plugin('composition.recipe', includes=(Plugin('composition.layer', includes=(nested,)),)),)
    return RunPlan(plugins, RunContract(release=config['release'], dependencies={'python': sys.version},
        configuration=frozen, time={'start': 1, 'end': 2}, budgets={'capacity': 1, 'max_activations': 4096}))


def _check(identifier, expected, actual, location):
    return {'id': identifier, 'passed': actual == expected, 'expected': expected, 'actual': actual, 'location': location}


def _artifacts(path):
    path = Path(path)
    events = [json.loads(line) for line in (path/'composition-evidence.jsonl').read_text().splitlines()]
    with StageReader(path) as reader:
        metadata = reader.read(lambda r: {'run_id': r.run_id, 'complete_step': r.complete_step})
        observations = reader.read(lambda r: r.query('SELECT step,body FROM composition_observations ORDER BY step'))
        actions = reader.read(lambda r: r.query('SELECT step,actor,name,body FROM composition_actions ORDER BY step,actor,name'))
    return events, metadata, [(step, json.loads(body)) for step, body in observations], actions


def validate(path):
    """只读实际工件；启动故障需消费者保存标准 CLI 的 cli-result.json。"""
    path = Path(path)
    events, metadata, observations, actions = _artifacts(path)
    config = events[0]['config']
    case, variant = config['case'], config['variant']
    failed = variant in ('install-failure', 'write-failure')
    checks = [_check('complete-step', 0 if failed else 2, metadata['complete_step'], 'StageReader.complete_step'),
              _check('observation-steps', [] if failed else [1, 2], [row[0] for row in observations], 'composition_observations.step')]
    closes = {name: [row['count'] for row in events if row['event'] == 'provider-close' and row['name'] == name]
              for name in ('A', 'B', 'C', 'owned', 'borrowed', 'independent')}
    expected_closes = {'A': [], 'B': [], 'C': [], 'owned': [], 'borrowed': [], 'independent': [1]}
    if case == 'registration':
        if variant == 'owned':
            expected_closes['owned'] = [1]
        else:
            expected_closes.update(A=[1], B=[1])
            if failed:
                expected_closes['C'] = [1]
        audits = [row for row in events if row['event'] == 'contributions-cleaned']
        checks.append(_check('cleanup-count', 1, len(audits), 'composition-evidence.contributions-cleaned'))
        for key, expected in [('paths', []), ('actions', []), ('borrowed_closed', 0), ('expired_scopes', 0 if failed else (1 if json.loads((path/'runner.json').read_text())['source'] else 2))]:
            checks.append(_check('cleanup-' + key, expected, audits[0][key] if audits else None, 'composition-evidence.contributions-cleaned.' + key))
    checks.append(_check('exactly-once-owned-and-borrowed', expected_closes, closes, 'composition-evidence.provider-close'))
    tails = [row for row in events if row['event'] == 'post-registry-close']
    checks.append(_check('post-registry-close-count', 1, len(tails), 'composition-evidence.post-registry-close'))
    checks.append(_check('independent-host-still-readable', 'independent\n' + BODY,
                         tails[0]['independent_text'] if tails else None, 'composition-evidence.independent_text'))
    checks.append(_check('closed-plugin-context', [{'event': 'closed-context', 'error': 'RuntimeError', 'message': 'plugin context is closed'}],
                         [row for row in events if row['event'] == 'closed-context'], 'composition-evidence.closed-context'))
    if failed:
        result_file = path/'cli-result.json'
        result = json.loads(result_file.read_text()) if result_file.is_file() else {}
        stdout = json.loads(result['stdout']) if 'stdout' in result else {}
        checks += [_check('runner-exit', 1, result.get('returncode'), 'cli-result.returncode'),
                   _check('runner-error', 'ValueError' if case == 'registration' else 'FileExistsError', stdout.get('error', {}).get('code'), 'cli-result.stdout.error.code'),
                   _check('no-runner-status', False, (path/'runner-status.json').exists(), 'runner-status.json'),
                   _check('no-business-actions', [], actions, 'composition_actions')]
        if case == 'registration':
            checks.append(_check('installed-before-third-failure', ['A', 'B'], [row['name'] for row in events if row['event'] == 'installed'], 'composition-evidence.installed'))
        else:
            checks.append(_check('real-manifest-directory', True, (path/'runner.json').is_dir(), 'runner.json'))
            checks.append(_check('blocker-created', [{'event': 'manifest-blocker', 'name': 'runner.json', 'kind': 'directory'}], [row for row in events if row['event'] == 'manifest-blocker'], 'composition-evidence.manifest-blocker'))
        return checks
    manifest = json.loads((path/'runner.json').read_text())
    status = json.loads((path/'runner-status.json').read_text())
    checks += [_check('runner-status', 'completed', status['status'], 'runner-status.status'),
               _check('diagnostic-errors', 0, status['diagnostic_errors'], 'runner-status.diagnostic_errors'),
               _check('frozen-config', config, manifest['contract']['configuration'], 'runner.contract.configuration'),
               _check('release', config['release'], manifest['contract']['release'], 'runner.contract.release'),
               _check('run-identity', metadata['run_id'], manifest['run_id'], 'runner.run_id'),
               _check('runtime-budgets', {'capacity': 1, 'max_activations': 4096}, manifest['effective_runtime'], 'runner.effective_runtime')]
    expected_actions = []
    for step, observed in observations:
        if case == 'manifest':
            # 历史 step 1 可来自另一种子树，本次策略必须真实用于 step 2。
            policy = 'external.policy.alternate' if variant == 'alternate-tree' else 'external.policy'
            if step == 1 and manifest['source']:
                source_manifest = json.loads((Path(manifest['source']['path'])/'runner.json').read_text())
                source_variant = source_manifest['contract']['configuration']['variant']
                policy = 'external.policy.alternate' if source_variant == 'alternate-tree' else 'external.policy'
            checks.append(_check('policy-step-' + str(step), {'policy': policy, 'full_text': BODY, 'characters': len(BODY)}, observed, 'composition_observations[' + str(step) + ']'))
            continue
        expected = {'before_text': ('owned' if variant == 'owned' else 'A') + '\n' + BODY,
            'before_paths_total': 3 if variant == 'owned' else 2, 'before_actions_total': 3 if variant == 'owned' else 2,
            'path_cursor': 'ValueError', 'action_cursor': 'ValueError', 'removed_read': 'Unavailable', 'removed_schema': 'Unavailable',
            'removed_invoke': asdict(ActionResult('rejected', {'reason': 'unavailable'})), 'removed_fts': 0, 'removed_tags': 0,
            'paths': ['/B', '/A'] if variant == 're-register' else (['/B', '/borrowed'] if variant == 'owned' else ['/B']),
            'B_text': ('owned' if variant == 'owned' else 'B') + '\n' + BODY, 'B_find': ['composition.B'],
            'B_schema': asdict(Action('composition.B', ('composition', 'item'), 'needle B', PARAMETERS, None, tags=('B', 'shared'))),
            'B_result': asdict(ActionResult('completed', {'name': 'B', 'text': 'needle B', 'step': step})), 'provider_closed': 0}
        expected['B_schema'].pop('handler'); expected['B_schema'].pop('available')
        # dataclass 的 tuple 经公开 JSON 变为数组。
        expected = json.loads(json.dumps(expected))
        expected_actions.append((step, 'a', 'composition.B', json.dumps(expected['B_result']['value'], ensure_ascii=False)))
        if variant == 'owned':
            expected.update(borrowed_text='borrowed\n' + BODY, borrowed_closed=0)
        if variant == 're-register':
            description = {**expected['B_schema'], 'name': 'composition.A', 'description': 'replacement token', 'tags': ['replacement', 'shared']}
            result = asdict(ActionResult('completed', {'name': 'A', 'text': 'replacement token', 'step': step}))
            expected.update(replacement_text='A\n' + BODY, replacement_schema=description,
                            replacement_find=['composition.A'], replacement_result=result)
            expected_actions.append((step, 'a', 'composition.A', json.dumps(result['value'], ensure_ascii=False)))
        checks.append(_check('registration-step-' + str(step), expected, observed, 'composition_observations[' + str(step) + ']'))
    checks.append(_check('actual-action-facts', sorted(expected_actions), actions, 'composition_actions(step,actor,name)'))
    if case == 'manifest':
        descriptions = manifest['plugins']
        names = [row['name'] for row in descriptions]
        policy = 'external.policy.alternate' if variant == 'alternate-tree' else 'external.policy'
        branch = 'composition.branch.alternate' if variant == 'alternate-tree' else 'composition.branch'
        plan = build(config)
        declared = PluginHost((Plugin('storage'), *plan.plugins)).describe_plugins()
        checks += [_check('actual-expanded-graph', json.loads(json.dumps([asdict(row) for row in declared])), descriptions, 'runner.plugins'),
                   _check('root-identity', ['composition.recipe'], manifest['plugin_roots'], 'runner.plugin_roots'),
                   _check('external-policy-used', 1, names.count(policy), 'runner.plugins.name'),
                   _check('no-default-policy', False, 'default.policy' in names, 'runner.plugins.name'),
                   _check('branch-present', True, branch in names, 'runner.plugins.name')]
    return checks


def compare(continuous, restored):
    """逐值比较业务事实，并确认新身份确实从 step 1 继续执行 step 2。"""
    left_events, left_meta, left_rows, left_actions = _artifacts(continuous)
    right_events, right_meta, right_rows, right_actions = _artifacts(restored)
    manifest = json.loads((Path(restored)/'runner.json').read_text())
    timings = [json.loads(line)['step'] for line in (Path(restored)/'timings.jsonl').read_text().splitlines()]
    return [_check('same-frozen-config', left_events[0]['config'], right_events[0]['config'], 'composition-evidence.configuration'),
            _check('new-run-identity', True, left_meta['run_id'] != right_meta['run_id'], 'StageReader.run_id'),
            _check('restore-source-step', {'path': str(Path(continuous).absolute()), 'step': 1}, manifest['source'], 'runner.source'),
            _check('only-step-two-executed', [2], timings, 'timings.step'),
            _check('same-observations', left_rows, right_rows, 'composition_observations(step,body)'),
            _check('same-action-facts', left_actions, right_actions, 'composition_actions(step,actor,name)')]
