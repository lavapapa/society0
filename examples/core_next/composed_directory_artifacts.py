"""公共规则计划：独立目录、共享 persona 快照与主体授权原文工件。"""
import json
from dataclasses import replace
from functools import partial
from pathlib import Path
import sys
import tempfile

from society0.kernel.actor_files import ActorFiles
from society0.kernel.actors import ActorDirectory, ActorRecord, ActorStore, actor_data_plugin, actor_plugin
from society0.kernel.drivers import rule_driver_plugin
from society0.kernel.interaction import ScopeClosed, interaction_plugin
from society0.kernel.plugins import Plugin
from society0.kernel.runner import RunContract, RunPlan
from society0.kernel.runtime import DriverResult, Phase, runtime_plugin
from society0.kernel.schedule import StepPlan, activate
from society0.kernel.storage import StageReader, StageStore
from society0.kernel.threads import THREAD_SCHEMA, ThreadStore


OBSERVATIONS = ('CREATE TABLE directory_artifact_observations(id INTEGER PRIMARY KEY,step INTEGER NOT NULL,actor TEXT NOT NULL,kind TEXT NOT NULL,body TEXT NOT NULL)',)
OVERRIDES = ('CREATE TABLE directory_overrides(actor TEXT PRIMARY KEY NOT NULL,driver TEXT NOT NULL,persona TEXT NOT NULL,config TEXT NOT NULL,FOREIGN KEY(actor) REFERENCES actors(id))',)


def save(store, step, actor, kind, value):
    body = json.dumps(value, ensure_ascii=False, allow_nan=False)
    store.transaction(lambda w: w.execute(
        'INSERT INTO directory_artifact_observations(step,actor,kind,body) VALUES(?,?,?,?)',
        (step, actor, kind, body)))


class Directory:
    """原生身份保留，驱动/persona/config 与委托角色来自本插件。"""
    def __init__(self, reader):
        self.reader, self.native = reader, ActorDirectory(reader)

    def driver_names(self):
        return self.reader.read(lambda r: tuple(name for name, in r.iter_query(
            'SELECT DISTINCT driver FROM directory_overrides ORDER BY driver')))

    @property
    def persona_dependencies(self):
        return ('directory_overrides',)

    def persona_in(self, view, actor):
        rows = view.query('SELECT persona FROM directory_overrides WHERE actor=?', (actor,))
        if not rows:
            raise KeyError(actor)
        return rows[0][0]

    def view(self, actor):
        original = self.native.get_record(actor)
        driver, persona, config = self.reader.read(lambda r: r.query(
            'SELECT driver,persona,config FROM directory_overrides WHERE actor=?', (actor,))[0])
        return replace(original, driver=driver, persona=persona, config=json.loads(config), roles=('delegated',))

    def get_record(self, actor):
        return self.view(actor)

    def __len__(self):
        return len(self.native)

    def __iter__(self):
        # ponytail: 固定两主体反向物化；大人口目录改用反向分页。
        return iter(reversed(tuple(self.native)))

    def select(self, *, role=None, active=True, limit=100, cursor=None):
        return self.native.select(role='base' if role == 'delegated' else role,
                                  active=active, limit=limit, cursor=cursor)

    def update(self, actor, **changes):
        own = {key: changes.pop(key) for key in ('driver', 'persona', 'config') if key in changes}
        if changes:
            self.native.update(actor, **changes)
        if own:
            columns = ','.join(key + '=?' for key in own)
            values = tuple(json.dumps(value, ensure_ascii=False) if key == 'config' else value
                           for key, value in own.items())
            self.reader.transaction(lambda w: w.execute(
                'UPDATE directory_overrides SET ' + columns + ' WHERE actor=?', (*values, actor)))

    def set_state(self, actor, key, value):
        self.native.set_state(actor, key, value)

    def deactivate(self, actor):
        self.native.deactivate(actor)


def directory_plugin():
    def initialize(w):
        w.executemany('INSERT INTO directory_overrides VALUES(?,?,?,?)',
            [(actor, driver, 'override-' + actor, json.dumps({'origin': 'override', 'actor': actor}))
             for actor, driver in [('a', 'alpha'), ('b', 'beta')]])
    return Plugin('directory', requires=('storage', 'actors.data'), schema_requires=('actors.data',),
                  schema=OVERRIDES, initialize=initialize,
                  install=lambda ctx: ctx.provide('directory', Directory(ctx.require('storage', 'store'))))


class PersonaQueries:
    """确定性 SQL 语义来源；真实推荐服务负责快照及缓存，无网络资源。"""
    def __init__(self, data):
        self.data, self.store, self.snapshots = data, data.store, []

    @property
    def dependencies(self):
        return self.data.preference_dependencies

    async def flush_embeddings(self):
        pass

    def query_text_in(self, view, actor, *, query=None):
        text = self.data.preference_text_in(view, actor) if query is None else query
        self.snapshots.append({'actor': actor, 'text': text,
            'persona': self.data.actors.persona_in(view, actor),
            'native': json.loads(view.query('SELECT body FROM actor_personas WHERE actor=?', (actor,))[0][0]),
            'revision': view.revision_for(self.dependencies),
            'posts': [key for key, in view.query('SELECT id FROM social_posts ORDER BY ordinal')]})
        return text

    async def similarities(self, source, *, text, dependencies):
        score = 0.75 if '-v2' in text else 0.25 if '-v1' in text else 0.0
        return {item.post_id: score for item in source.candidates}


def pages(directory):
    result, cursor = [], None
    while True:
        page = directory.select(role='delegated', limit=1, cursor=cursor)
        result.append({'items': page.items, 'total': page.total, 'cursor': page.next_cursor})
        cursor = page.next_cursor
        if cursor is None:
            return result


async def directory_rule(session, *, label):
    state = session.prepared
    actors, store = state['actors'], state['store']
    actor, step = session.actor.id, session.step
    record = session.actor.config
    save(store, step, actor, 'session', {'factory': label, 'driver': record.driver,
         'persona': record.persona, 'config': record.config, 'roles': list(record.roles),
         'state': record.state, 'iteration': list(actors), 'pages': pages(actors),
         'run_id': store.read(lambda view: view.run_id)})
    if state['variant'] == 'persona':
        recs, semantic = state['recommendations'], state['semantic']
        start = len(semantic.snapshots)
        before = await recs.recommendation(actor, 1)
        cached = await recs.recommendation(actor, 1)
        actors.update(actor, persona=f'override-{actor}-v{step}')
        after = await recs.recommendation(actor, 1)
        cached_after = await recs.recommendation(actor, 1)
        save(store, step, actor, 'persona', {'dependencies': list(actors.persona_dependencies),
             'snapshots': semantic.snapshots[start:],
             'cache_before': cached[0] is before[0] and cached[1] is before[1],
             'cache_after': cached_after[0] is after[0] and cached_after[1] is after[1],
             'before': {'revision': before[0].revision, 'scores': [item.score for item in before[1].items]},
             'after': {'revision': after[0].revision, 'scores': [item.score for item in after[1].items]}})
    return DriverResult('completed', {'actor': actor, 'driver': label})


class ThreadFacade:
    """显式公共方法转发；服务没有 .store 或动态属性穿透。"""
    def __init__(self, threads):
        self._threads = threads

    def open(self, actor, moment, kind):
        return self._threads.open(actor, moment, kind)

    def find(self, actor, moment, kind='decision'):
        return self._threads.find(actor, moment, kind)

    def reopen(self, thread):
        return self._threads.reopen(thread)

    def append_message(self, thread, message):
        return self._threads.append_message(thread, message)

    def save_tool_result(self, thread, call, content):
        return self._threads.save_tool_result(thread, call, content)

    def close(self, thread, outcome):
        return self._threads.close(thread, outcome)

    def register_artifact(self, thread, reference, artifact, *, actor):
        return self._threads.register_artifact(thread, reference, artifact, actor=actor)

    def list_artifacts(self, *, actor, limit=100, cursor=None):
        return self._threads.list_artifacts(actor=actor, limit=limit, cursor=cursor)

    def read_actor_artifact(self, reference, *, actor, offset=0, size=65536):
        return self._threads.read_actor_artifact(reference, actor=actor, offset=offset, size=size)

    def read_artifact(self, thread, reference, *, actor, offset=0, size=65536):
        return self._threads.read_artifact(thread, reference, actor=actor, offset=offset, size=size)


def original(actor, step, kind):
    return f'主体{actor} 第{step}步 {kind}\n' + ('完整中文🙂🚀逐字原文\n' * 4500) + f'末尾:{actor}:{step}:{kind}\n'


async def artifact_rule(session):
    state, actor, step = session.prepared, session.actor.id, session.step
    threads, store = state['threads'], state['store']
    thread = threads.find(actor, session.moment)
    session.cursors['thread_id'] = thread
    files = ActorFiles(session, threads)
    state['expired'].append((session, files))
    cursor, found, found_pages = None, [], []
    while True:
        page = await files.find('*.txt', '/results', limit=1, cursor=cursor)
        found.extend(item['path'] for item in page.items)
        found_pages.append({'paths': [item['path'] for item in page.items],
                            'total': page.total, 'continued': page.next_cursor is not None})
        cursor = page.next_cursor
        if cursor is None:
            break
    save(store, step, actor, 'find', {'pages': found_pages, 'facade_has_store': hasattr(threads, 'store')})
    for path in found:
        offset, chunks = 0, []
        while True:
            chunk = await files.read(path, offset=offset, size=8191)
            chunks.append({'offset': offset, 'data': chunk['data'], 'total_bytes': chunk['total_bytes'],
                           'next_offset': chunk['next_offset']})
            offset = chunk['next_offset']
            if offset is None:
                break
        save(store, step, actor, 'read', {'path': path, 'chunks': chunks})
    other = 'b' if actor == 'a' else 'a'
    foreign = files.result_path(f'{other}-body-1.txt')
    try:
        await files.read(foreign)
    except KeyError as error:
        save(store, step, actor, 'denied', {'error': type(error).__name__, 'reference': f'{other}-body-1.txt'})
    else:
        raise AssertionError('foreign artifact was readable')
    try:
        threads.read_artifact(threads.find(other, session.moment), f'{other}-body-1.txt', actor=actor)
    except PermissionError as error:
        save(store, step, actor, 'owner', {'error': type(error).__name__, 'owner': other})
    else:
        raise AssertionError('foreign Thread was readable')
    threads.close(thread, 'completed')
    return DriverResult('completed', {'actor': actor, 'files': len(found)})


def build(config):
    case, variant = config['case'], config.get('variant', 'default')
    variants = {'actor-directory': ('default', 'readonly', 'persona'), 'artifact-read': ('default',)}
    if case not in variants or variant not in variants[case]:
        raise ValueError('unknown directory/artifact case or variant')
    if config.get('provider') != 'scripted' or config.get('start', 1) != 1:
        raise ValueError('requires provider=scripted and start=1')
    end = config.get('end', 2)
    if type(end) is not int or end not in (1, 2):
        raise ValueError('directory/artifact scenario end must be 1 or 2')
    if config.get('fault', 'none') != 'none':
        raise ValueError('directory/artifact cases have no fault injection')
    if config.get('activations_per_step', 2) != 2:
        raise ValueError('directory/artifact cases require two activations per step')
    moment = config.get('moment', {'time': 1, 'phase': 'read'})
    plugins = [interaction_plugin(lambda *args: True), Plugin('observations', schema=OBSERVATIONS),
        actor_data_plugin(records=[ActorRecord(actor, 'native', persona='native-' + actor,
                         config={'origin': 'native'}, roles=('base',)) for actor in ('a', 'b')])]
    references = ['actors', 'storage']
    if case == 'actor-directory':
        plugins.extend([directory_plugin(),
            rule_driver_plugin(partial(directory_rule, label='alpha'), name='driver.alpha'),
            rule_driver_plugin(partial(directory_rule, label='beta'), name='driver.beta'),
            actor_plugin({'alpha': ('driver.alpha', 'factory'), 'beta': ('driver.beta', 'factory')},
                         data=('directory', 'directory'))])
        references.append('directory')
        if variant == 'persona':
            from society0.plugins.social import (social_data_plugin, social_notifications_plugin,
                social_relations_plugin, social_candidates_plugin, social_recommendation_plugin,
                weighted_ranking_plugin)
            def semantic(ctx):
                ctx.provide('semantic', PersonaQueries(ctx.require('social.data', 'data')))
            def posts(w):
                w.executemany('INSERT INTO social_posts VALUES(?,?,?,1,NULL,0,0,0,0,0)',
                              [('p-a', 1, 'a'), ('p-b', 2, 'b')])
                w.executemany('INSERT INTO social_bodies VALUES(?,?,?)',
                              [('p-a', '公开完整帖子a🙂'.encode(), '[]'), ('p-b', '公开完整帖子b🙂'.encode(), '[]')])
                w.execute('UPDATE social_head SET post_count=2')
            plugins.extend([
                social_data_plugin(('a', 'b'), actors='directory', edges=[], config={
                    'social_media': {'recommendation': {'use_embedding_similarity': False,
                        'chronological_weight': 0, 'engagement_weight': 0, 'network_weight': 0,
                        'similarity_weight': 1, 'include_recent_posts_in_query': False,
                        'include_following_in_query': False}}}),
                Plugin('persona.posts', schema_requires=('social.data',), initialize=posts),
                social_notifications_plugin(), social_relations_plugin(), social_candidates_plugin(),
                weighted_ranking_plugin(data='social.data'), Plugin('persona.semantic', requires=('social.data',), install=semantic),
                social_recommendation_plugin(semantic='persona.semantic')])
            references.extend(['social.recommendation', 'persona.semantic'])
    else:
        # 工件场景沿原生目录运行，主体标签明确匹配规则工厂。
        plugins[2] = actor_data_plugin(records=[ActorRecord(actor, 'rule') for actor in ('a', 'b')])
        plugins.extend([Plugin('threads', requires=('storage',), schema=THREAD_SCHEMA,
            install=lambda ctx: ctx.provide('threads', ThreadFacade(ThreadStore(ctx.require('storage', 'store'))))),
            rule_driver_plugin(artifact_rule), actor_plugin({'rule': ('rule_driver', 'factory')},
                                                          data=('actors.data', 'directory'))])
        references.append('threads')

    def schedule(ctx):
        state = {'actors': ctx.require('actors', 'actors'), 'store': ctx.require('storage', 'store'),
                 'variant': variant, 'expired': []}
        if case == 'artifact-read':
            state['threads'] = ctx.require('threads', 'threads')
        if variant == 'persona':
            state.update(recommendations=ctx.require('social.recommendation', 'recommendation'),
                         semantic=ctx.require('persona.semantic', 'semantic'))
        class ConsumerSchedule:
            async def next_step(self, completed_step):
                number = completed_step + 1
                if number > end:
                    return None
                async def run(context):
                    store, actors = state['store'], state['actors']
                    if case == 'actor-directory':
                        prior = actors.view('a').driver
                        errors = []
                        try:
                            actors.update('a', driver='unresolved')
                        except KeyError as error:
                            errors.append(type(error).__name__)
                        directory = ctx.require('directory', 'directory')
                        directory.update('a', driver='unresolved')
                        try:
                            ActorStore(directory, {'alpha': None, 'beta': None})
                        except ValueError as error:
                            errors.append(type(error).__name__)
                        finally:
                            directory.update('a', driver=prior)
                        save(store, number, '*', 'driver-checks', errors)
                        if variant == 'readonly':
                            with tempfile.TemporaryDirectory(prefix='society0-directory-view-') as temp:
                                view_path = Path(temp) / 'complete'
                                StageStore.prepare_readonly(store.path, view_path, step=completed_step)
                                with StageReader(view_path) as reader:
                                    save(store, number, '*', 'readonly', {
                                        'complete_step': reader.read(lambda r: r.complete_step),
                                        'run_id': reader.read(lambda r: r.run_id), 'pages': pages(Directory(reader))})
                    else:
                        threads = state['threads']
                        for actor in ('a', 'b'):
                            thread = threads.find(actor, context.moment)
                            if thread is None:
                                thread = threads.open(actor, context.moment, 'decision')
                            threads.reopen(thread)
                            body, tool = original(actor, number, 'body'), original(actor, number, 'tool')
                            threads.append_message(thread, {'role': 'user', 'content': body})
                            threads.save_tool_result(thread, {'id': f'{actor}-tool-{number}',
                                'name': 'rule-material', 'arguments': {'actor': actor, 'step': number}}, tool)
                            for kind, text in [('body', body), ('tool', tool)]:
                                artifact = context.prepare_artifact((text.encode(),))
                                threads.register_artifact(thread, f'{actor}-{kind}-{number}.txt', artifact, actor=actor)
                    await activate(context, tuple(actors))
                    if case == 'actor-directory':
                        # 完整点 1 保存切换后的标签，恢复后的实际激活必须使用它们。
                        actors.update('a', driver='beta' if number % 2 else 'alpha')
                        actors.update('b', driver='alpha' if number % 2 else 'beta')
                    else:
                        for session, files in state['expired']:
                            try:
                                await files.read(files.result_path(f'{session.actor.id}-body-1.txt'))
                            except ScopeClosed as error:
                                save(store, number, session.actor.id, 'expired', type(error).__name__)
                            else:
                                raise AssertionError('expired actor scope was readable')
                            await files.close()
                        state['expired'].clear()
                return StepPlan(moment['time'], (Phase(moment['phase'], run, prepare=lambda context: state),))
        ctx.provide('schedule', ConsumerSchedule())
    plugins.extend([Plugin('schedule', requires=tuple(references), install=schedule),
        runtime_plugin(actor_service=('actors', 'actors'), information=('interaction', 'information'),
                       actions=('interaction', 'actions'), store=('storage', 'store'))])
    return RunPlan(plugins, RunContract(config['release'], {'python': sys.version},
        {'case': case, 'variant': variant, 'provider': 'scripted', 'models': {},
         'moment': moment, 'activations_per_step': 2},
        {'start': 1, 'end': end}, {}))


def observations(reader):
    return reader.read(lambda r: [{'step': step, 'actor': actor, 'kind': kind, 'body': json.loads(body)}
        for step, actor, kind, body in r.iter_query(
            'SELECT step,actor,kind,body FROM directory_artifact_observations ORDER BY id')])


def validate(path):
    """只读最终事实；每项返回 id/passed/expected/actual/location。"""
    path = Path(path)
    manifest = json.loads((path / 'runner.json').read_text(encoding='utf-8'))
    status = json.loads((path / 'runner-status.json').read_text(encoding='utf-8'))
    config, end = manifest['contract']['configuration'], manifest['contract']['time']['end']
    checks = []
    def check(key, expected, actual, location):
        checks.append({'id': key, 'passed': expected == actual, 'expected': expected,
                       'actual': actual, 'location': location})
    check('run.completed', ['completed', 0], [status['status'], status['diagnostic_errors']], 'runner-status.json')
    check('run.complete-step', end, status['complete_step'], 'runner-status.json.complete_step')
    with StageReader(path) as reader:
        rows = observations(reader)
        check('run.sql-complete-step', end, reader.read(lambda r: r.complete_step), 'StageReader.complete_step')
        tables = reader.read(lambda r: [name for name, in r.query("SELECT name FROM sqlite_master WHERE type='table'")])
        requests = reader.read(lambda r: r.query("SELECT count(*) FROM thread_events WHERE kind='request'")[0][0]) if 'thread_events' in tables else 0
        check('run.no-model-requests', 0, requests, 'thread_events.kind=request')
        def records(kind, step=None, actor=None):
            return [row['body'] for row in rows if row['kind'] == kind and
                    (step is None or row['step'] == step) and (actor is None or row['actor'] == actor)]
        if config['case'] == 'actor-directory':
            check('directory.native-difference', [['a', 'native', 'native-a'], ['b', 'native', 'native-b']],
                  reader.read(lambda r: [[actor, driver, json.loads(body)] for actor, driver, body in r.query(
                      'SELECT id,driver,body FROM actors JOIN actor_personas ON actor=id ORDER BY id')]), 'actors/actor_personas')
            for step in range(1, end + 1):
                check(f'directory.driver-checks.{step}', [['KeyError', 'ValueError']], records('driver-checks', step),
                      f'directory_artifact_observations(step={step},kind=driver-checks)')
                for actor in ('a', 'b'):
                    label = ('alpha' if actor == 'a' else 'beta') if step % 2 else ('beta' if actor == 'a' else 'alpha')
                    persona = f'override-{actor}' + (f'-v{step - 1}' if config['variant'] == 'persona' and step > 1 else '')
                    sessions = records('session', step, actor)
                    expected = {'factory': label, 'driver': label, 'persona': persona,
                        'config': {'origin': 'override', 'actor': actor}, 'roles': ['delegated'], 'state': {},
                        'iteration': ['b', 'a']}
                    check(f'directory.session.{step}.{actor}', [expected],
                          [{key: value for key, value in item.items() if key not in ('pages', 'run_id')} for item in sessions],
                          f'directory_artifact_observations(step={step},actor={actor},kind=session)')
                    check(f'directory.pages.{step}.{actor}', [[['a'], 2, True], [['b'], 2, False]],
                          [[item['items'], item['total'], item['cursor'] is not None] for item in sessions[0]['pages']],
                          f'directory_artifact_observations(step={step},actor={actor},kind=session).pages')
                    check(f'directory.page-identity.{step}.{actor}', [sessions[0]['run_id'], 'base', True],
                          sessions[0]['pages'][0]['cursor']['identity'], 'ActorDirectory.select cursor.identity')
                    if manifest['source'] is None or step > manifest['source']['step']:
                        check(f'directory.session-run.{step}.{actor}', manifest['run_id'], sessions[0]['run_id'],
                              'directory_artifact_observations.session.run_id')
                    if config['variant'] == 'persona':
                        record = records('persona', step, actor)[0]
                        snapshots = record['snapshots']
                        check(f'persona.shared-snapshot.{step}.{actor}',
                            [[actor, 'Persona:\n' + persona, persona, 'native-' + actor, ['p-a', 'p-b']],
                             [actor, f'Persona:\noverride-{actor}-v{step}', f'override-{actor}-v{step}', 'native-' + actor, ['p-a', 'p-b']]],
                            [[item[key] for key in ('actor', 'text', 'persona', 'native', 'posts')] for item in snapshots],
                            f'directory_artifact_observations(step={step},actor={actor},kind=persona).snapshots')
                        check(f'persona.snapshot-versions.{step}.{actor}',
                            [record['before']['revision'], record['after']['revision']],
                            [item['revision'] for item in snapshots], 'shared ReadView/recommendation revision')
                        check(f'persona.scores.{step}.{actor}', [[0.0 if step == 1 else 0.25], [0.25 if step == 1 else 0.75]],
                            [record['before']['scores'], record['after']['scores']], 'RankingOutput semantic score')
                        check(f'persona.dependencies.{step}.{actor}', ['directory_overrides'], record['dependencies'], 'ActorStore.persona_dependencies')
                        check(f'persona.cache.{step}.{actor}', [True, True, True, True],
                            [record['cache_before'], record['cache_after'], record['after']['revision'] > record['before']['revision'],
                             record['before']['scores'] != record['after']['scores']], 'Recommendations.recommendation')
            if config['variant'] == 'readonly':
                for step in range(1, end + 1):
                    record = records('readonly', step)[0]
                    check(f'directory.readonly.{step}', [step - 1, [['a'], ['b']], [2, 2], True],
                        [record['complete_step'], [page['items'] for page in record['pages']],
                         [page['total'] for page in record['pages']],
                         record['pages'][0]['cursor']['identity'][0] == record['run_id']], 'StageReader complete point/ActorDirectory.select')
            check('directory.readonly-final', [[['a'], 2, True], [['b'], 2, False]],
                [[item['items'], item['total'], item['cursor'] is not None] for item in pages(Directory(reader))], 'StageReader final/ActorDirectory.select')
        else:
            threads = ThreadStore(reader)
            for actor in ('a', 'b'):
                heads = threads.list_threads(actor=actor)['items']
                check(f'artifact.thread-count.{actor}', 1, len(heads), f'thread_heads(actor={actor})')
                thread = heads[0]['id']
                check(f'artifact.thread.{actor}', [{'role': role, 'content': original(actor, step, kind),
                      **({'tool_call_id': f'{actor}-tool-{step}'} if role == 'tool' else {})}
                      for step in range(1, end + 1) for kind, role in [('body', 'user'), ('tool', 'tool')]],
                      threads.read_messages(thread), f'Thread({thread}).messages')
                check(f'artifact.thread-status.{actor}', 'completed', heads[0]['status'], f'Thread({thread}).status')
                for step in range(1, end + 1):
                    call = threads.get_tool_result(thread, f'{actor}-tool-{step}')
                    check(f'artifact.tool-receipt.{step}.{actor}',
                        {'call': {'id': f'{actor}-tool-{step}', 'name': 'rule-material',
                                  'arguments': {'actor': actor, 'step': step}},
                         'metadata': None, 'content': original(actor, step, 'tool')},
                        call, f'Thread({thread}).tool_receipts[{actor}-tool-{step}]')
                    for kind in ('body', 'tool'):
                        reference = f'{actor}-{kind}-{step}.txt'
                        offset, parts, ranges = 0, [], []
                        while True:
                            chunk = threads.read_actor_artifact(reference, actor=actor, offset=offset, size=8191)
                            parts.append(chunk['data'])
                            ranges.append([offset, chunk['total_bytes'], chunk['next_offset']])
                            offset = chunk['next_offset']
                            if offset is None:
                                break
                        wanted = original(actor, step, kind)
                        check(f'artifact.authority-bytes.{step}.{actor}.{kind}', wanted,
                              b''.join(parts).decode('utf-8'), f'ThreadStore.read_actor_artifact({reference})')
                        check(f'artifact.authority-range.{step}.{actor}.{kind}',
                              [len(wanted.encode()), None, True],
                              [ranges[-1][1], ranges[-1][2], len(ranges) > 1], reference + '.ranges')
                    found = records('find', step, actor)[0]
                    expected_refs = sorted(f'{actor}-{kind}-{number}.txt' for number in range(1, step + 1) for kind in ('body', 'tool'))
                    check(f'artifact.find.{step}.{actor}',
                          {'paths': [ActorFiles.result_path(ref) for ref in expected_refs],
                           'totals': [2 * step] * (2 * step), 'continued': [True] * (2 * step - 1) + [False], 'store': False},
                          {'paths': [path for page in found['pages'] for path in page['paths']],
                           'totals': [page['total'] for page in found['pages']],
                           'continued': [page['continued'] for page in found['pages']], 'store': found['facade_has_store']},
                          f'directory_artifact_observations(step={step},actor={actor},kind=find)')
                    reads = records('read', step, actor)
                    check(f'artifact.read-count.{step}.{actor}', 2 * step, len(reads), 'directory_artifact_observations.kind=read')
                    for item in reads:
                        from urllib.parse import unquote
                        ref = unquote(item['path'][9:])
                        owner, kind, number = ref[:-4].split('-')
                        wanted = original(owner, int(number), kind)
                        chunks = item['chunks']
                        offsets, offset = [], 0
                        for chunk in chunks:
                            offsets.append(offset)
                            offset += len(chunk['data'].encode())
                        check(f'artifact.bytes.{step}.{actor}.{ref}', wanted, ''.join(chunk['data'] for chunk in chunks), item['path'])
                        check(f'artifact.ranges.{step}.{actor}.{ref}',
                            [offsets, [len(wanted.encode())] * len(chunks), offsets[1:] + [None], True],
                            [[chunk['offset'] for chunk in chunks], [chunk['total_bytes'] for chunk in chunks],
                             [chunk['next_offset'] for chunk in chunks], len(chunks) > 1], item['path'] + '.ranges')
                    other = 'b' if actor == 'a' else 'a'
                    check(f'artifact.denied.{step}.{actor}', [{'error': 'KeyError', 'reference': f'{other}-body-1.txt'}], records('denied', step, actor), 'ActorFiles.read foreign reference')
                    check(f'artifact.owner.{step}.{actor}', [{'error': 'PermissionError', 'owner': other}], records('owner', step, actor), 'ThreadStore.read_artifact foreign Thread')
                    check(f'artifact.expired.{step}.{actor}', ['ScopeClosed'], records('expired', step, actor), 'ActorFiles.read expired scope')
    return checks


def compare(continuous, restored):
    """完整正文逐值比较；独立只读点 run_id/选择游标身份逐字段规范化。"""
    def facts(path):
        with StageReader(path) as reader:
            rows = observations(reader)
            for row in rows:
                body = row['body']
                if row['kind'] == 'persona':
                    # 恢复建立新运行的 SQL revision；保留快照关联及本次更新的相对推进量。
                    origin = body['before']['revision']
                    for snapshot in body['snapshots']:
                        snapshot['revision'] -= origin
                    for key in ('before', 'after'):
                        body[key]['revision'] -= origin
                    continue
                if row['kind'] == 'readonly':
                    body.pop('run_id')
                    selected = body['pages']
                elif row['kind'] == 'session':
                    body['run_id'] = '<run-id>'
                    selected = body['pages']
                else:
                    continue
                for page in selected:
                    if page['cursor'] is not None:
                        page['cursor']['identity'][0] = '<run-id>'
            return rows
    expected, actual = facts(continuous), facts(restored)
    # 首个差异带完整事实；成功时返回实际记录数，避免再复制全部测试正文。
    difference = next(((left, right) for left, right in zip(expected, actual) if left != right), None)
    checks = [{'id': 'restore.facts', 'passed': expected == actual,
               'expected': len(expected) if difference is None else difference[0],
               'actual': len(actual) if difference is None else difference[1],
               'location': 'directory_artifact_observations ORDER BY id'}]
    configuration = json.loads((Path(restored) / 'runner.json').read_text(encoding='utf-8'))['contract']['configuration']
    if configuration['case'] == 'artifact-read':
        with StageReader(continuous) as first, StageReader(restored) as second:
            original_heads = ThreadStore(first).list_threads()['items']
            restored_heads = ThreadStore(second).list_threads()['items']
            checks.append({'id': 'restore.thread-identity', 'passed': original_heads == restored_heads,
                           'expected': original_heads, 'actual': restored_heads, 'location': 'ThreadStore.list_threads'})
    return checks
