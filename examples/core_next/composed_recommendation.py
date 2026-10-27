"""公开规则计划：SQL 来源、完整排名、离线语义等待与正常分页。"""
import asyncio
from dataclasses import asdict, replace
import json
import struct
from pathlib import Path
import sys

from society0.kernel.actors import ActorRecord
from society0.kernel.interaction import Query
from society0.kernel.plugins import Plugin
from society0.kernel.runner import RunContract, RunPlan
from society0.kernel.runtime import DriverResult, Phase
from society0.kernel.schedule import StepPlan
from society0.kernel.storage import StageReader
from society0.plugins.social import social_plugin, social_recommendation_plugin
from society0.plugins.social_ranking import Candidate, WeightedRanker
from society0.presets import rule_preset

VARIANTS = ('default', 'external-selection', 'external-relations', 'native', 'semantic-change', 'paging')
WEIGHTS = dict(chronological_weight=.1, engagement_weight=.2, network_weight=.4,
               similarity_weight=.3, follow_bonus=1, time_decay_hours=24)


def body(author, number):
    return (f'帖子{author} 序号{number}：完整社区讨论、证据与不同观点🙂🚀\n' * 6000)


def output_values(output):
    return [dict(post_id=item.post_id, score=item.score, components=dict(item.components))
            for item in output.items]


class SelectedPosts:
    @property
    def dependencies(self):
        return ('recommendation_selected', 'social_posts')

    def __init__(self, store):
        self.store = store
        self.reads = 0

    def active_pool_in(self, view, tick):
        self.reads += 1
        pool = tuple(Candidate(*row) for row in view.iter_query(
            'SELECT p.id,p.author,p.created_tick,p.like_count,p.reply_count,p.repost_count,p.view_count,p.engagement '
            'FROM recommendation_selected s CROSS JOIN social_posts p ON p.id=s.id ORDER BY p.ordinal'))
        self.last_read = dict(revision=view.revision_for(self.dependencies), tick=tick,
                              candidates=[asdict(item) for item in pool])
        return pool


class ExternalRelations:
    @property
    def dependencies(self):
        return ('recommendation_following',)

    def __init__(self, store):
        self.store = store

    def following_in(self, view, actor):
        following = frozenset(row[0] for row in view.iter_query(
            'SELECT followee FROM recommendation_following WHERE follower=?', (actor,)))
        self.last_read = dict(actor=actor, following=sorted(following), revision=view.revision_for(self.dependencies))
        return following


class CountingRanker(WeightedRanker):
    def __init__(self):
        super().__init__(WEIGHTS)
        self.calls = 0

    def rank(self, source):
        self.calls += 1
        return super().rank(source)


class OfflineEmbed:
    def __init__(self, store):
        self.store = store
        self.calls = []
        self.ready = asyncio.Event()
        self.release = asyncio.Event()
        self.pause = False

    async def embed(self, texts, *, metadata):
        self.calls.append(dict(purpose=metadata['purpose'], texts=list(texts)))
        if self.pause and metadata['purpose'] == 'social_recommendation':
            self.store.transaction(lambda w: w.execute(
                'INSERT INTO recommendation_events(body) VALUES(?)',
                (json.dumps(dict(event='embedding-ready', metadata=metadata), ensure_ascii=False),)))
            self.ready.set()
            await self.release.wait()
        return [[0., 1.] if text.startswith('帖子d') or '改变偏好' in text else [1., 0.]
                for text in texts]


class Scenario:
    def __init__(self, store, social, recommendations, ranker, embed, selected, relations, directory, variant):
        self.store, self.social, self.recs = store, social, recommendations
        self.ranker, self.embed, self.selected = ranker, embed, selected
        self.relations, self.directory, self.variant = relations, directory, variant

    def record(self, value, *, event=False):
        table = 'recommendation_events' if event else 'recommendation_observations'
        self.store.transaction(lambda w: w.execute(f'INSERT INTO {table}(body) VALUES(?)',
            (json.dumps(value, ensure_ascii=False, allow_nan=False, default=asdict),)))

    def revisions(self):
        return self.store.read(lambda r: {table: r.revision_for((table,)) for table in
            ('social_posts', 'social_edges', 'social_vectors', 'recommendation_selected', 'recommendation_following')})

    def execute(self, operation, actor, target, arguments=None, tick=2):
        result = self.social.execute(operation, actor, target, arguments or {}, tick)
        if result.status != 'completed':
            raise ValueError(f'{operation} rejected: {result.value}')
        return result

    async def seed(self):
        if self.store.read(lambda r: r.query('SELECT count(*) FROM social_posts')[0][0]):
            return
        count = 4 if self.variant == 'default' else 2
        for number in range(count):
            author = 'b' if number % 2 == 0 else 'd'
            self.execute('publish_post', author, author, {'content': body(author, number + 1)}, tick=number)
        self.store.transaction(lambda w: w.execute("INSERT INTO recommendation_selected VALUES('post_1')"))
        if self.variant != 'external-selection':
            self.store.transaction(lambda w: w.execute("INSERT INTO recommendation_selected VALUES('post_2')"))
        await self.social.flush_embeddings()

    async def observe(self, session, label, *, tick=2, query=None, cursor=None, limit=1, actor=None):
        actor = session.actor.id if actor is None else actor
        before = self.ranker.calls
        embed_before = len(self.embed.calls)
        source, output = await self.recs.recommendation(actor, tick, query=query)
        # 独立新快照经公开输入接口取值，再用无计数的纯值策略作为对照。
        direct = await self.recs.recommendation_input(actor, tick, query=query)
        oracle = WeightedRanker(WEIGHTS).rank(direct)
        page = None
        if actor == session.actor.id and tick == session.moment.time and query is None:
            page = await session.information.query('/social/feed', Query(limit=limit, cursor=cursor))
        record = dict(label=label, step=session.step, actor=actor, tick=tick, query=query,
            source_revision=source.revision, following=sorted(source.following),
            similarities=dict(source.similarities), candidates=[asdict(c) for c in source.candidates],
            ranked=output_values(output), total=output.total,
            direct=dict(revision=direct.revision, following=sorted(direct.following),
                        similarities=dict(direct.similarities), candidates=[asdict(c) for c in direct.candidates],
                        ranked=output_values(oracle), total=oracle.total),
            page=None if page is None else dict(total=page.total, cursor=page.next_cursor,
                revision=page.revision, items=list(page.items)),
            rank_calls=self.ranker.calls, rank_delta=self.ranker.calls-before,
            embed_calls=len(self.embed.calls), embed_delta=len(self.embed.calls)-embed_before,
            pool_reads=self.selected.reads, table_revisions=self.revisions(), weights=WEIGHTS)
        self.record(record)
        return page

    async def reject_cursor(self, session, cursor, label):
        try:
            await session.information.query('/social/feed', Query(limit=1, cursor=cursor))
        except ValueError as error:
            self.record(dict(event='cursor-rejected', label=label, error=type(error).__name__, message=str(error)), event=True)
        else:
            raise AssertionError('旧分页游标被接受')

    async def read_all(self, session, page):
        items = []
        while True:
            for item in page.items:
                pieces, offset = [], 0
                while True:
                    chunk = await session.information.read(item['content_path'], offset=offset, size=4096)
                    pieces.append(chunk.data)
                    if chunk.next_offset is None:
                        break
                    offset = chunk.next_offset
                text = b''.join(pieces).decode()
                self.record(dict(event='original-read', actor=session.actor.id, post_id=item['post_id'],
                                 content=text, expected=self.social.post_details(item['post_id'])['content']), event=True)
                items.append(item['post_id'])
            if page.next_cursor is None:
                break
            page = await self.observe(session, 'full-page', cursor=page.next_cursor)
        return items

    async def wait_change(self, session, change, index):
        query = f'等待变更{index} 完整偏好🙂'
        old = await self.recs.recommendation_input('a', 2, query=query)
        self.embed.pause = True
        self.embed.ready.clear()
        self.embed.release.clear()
        before_rank = self.ranker.calls
        before_embed = sum(call['purpose'] == 'social_recommendation' for call in self.embed.calls)
        before_tables = self.revisions()
        before_pool_reads = self.selected.reads
        task = asyncio.create_task(self.recs.recommendation('a', 2, query=query + ' 新请求'))
        try:
            await asyncio.wait_for(self.embed.ready.wait(), timeout=10)
            request_candidates = self.selected.last_read
            request_following = self.relations.last_read
            pool_reads_delta = self.selected.reads - before_pool_reads
            if change == 'selected':
                self.store.transaction(lambda w: w.execute("DELETE FROM recommendation_selected WHERE id='post_2'"))
            elif change == 'following':
                self.store.transaction(lambda w: w.execute("INSERT INTO recommendation_following VALUES('a','b')"))
            elif change == 'posts':
                self.execute('like_post', 'a', 'post_1')
            elif change == 'vectors':
                self.execute('publish_post', 'd', 'd', {'content': body('d', 3)}, tick=3)
                self.store.transaction(lambda w: w.execute("INSERT INTO recommendation_selected VALUES('post_3')"))
                await self.social.flush_embeddings()
            elif change == 'persona':
                self.directory.update('a', persona='改变偏好 完整人格🙂')
            self.embed.pause = False
            self.embed.release.set()
            try:
                await task
            except ValueError as error:
                self.record(dict(event='await-rejected', change=change, error=type(error).__name__, message=str(error),
                    old_revision=old.revision, old_candidates=[asdict(c) for c in old.candidates],
                    old_following=sorted(old.following), old_similarities=dict(old.similarities),
                    before_tables=before_tables, after_tables=self.revisions(),
                    request_candidates=request_candidates, request_following=request_following,
                    pool_reads_delta=pool_reads_delta,
                    rank_delta=self.ranker.calls-before_rank,
                    embed_purpose='social_recommendation',
                    embed_delta=sum(call['purpose'] == 'social_recommendation' for call in self.embed.calls)-before_embed), event=True)
            else:
                raise AssertionError('过期语义结果被接受')
        finally:
            self.embed.pause = False
            self.embed.release.set()
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await self.observe(session, 'await-new-' + change, query=query + ' 新请求')
        await self.observe(session, 'await-hit-' + change, query=query + ' 新请求')

    async def run(self, session):
        variant = self.variant
        if session.step > 1:
            await self.observe(session, 'continued')
        elif variant == 'default':
            page = await self.observe(session, 'default-first', limit=2)
            await self.observe(session, 'default-second', limit=2, cursor=page.next_cursor)
        elif variant == 'external-selection':
            await self.observe(session, 'selection-one')
            self.store.transaction(lambda w: w.execute("INSERT INTO recommendation_selected VALUES('post_2')"))
            await self.observe(session, 'selection-two')
            self.store.transaction(lambda w: w.execute("INSERT INTO recommendation_notes(body) VALUES('无关原文')"))
            await self.observe(session, 'selection-unrelated')
            self.store.transaction(lambda w: w.execute("DELETE FROM recommendation_selected WHERE id='post_1'"))
            await self.observe(session, 'selection-removed')
        elif variant == 'external-relations':
            await self.observe(session, 'relations-before')
            self.store.transaction(lambda w: w.execute("INSERT INTO recommendation_following VALUES('a','d')"))
            await self.observe(session, 'relations-after')
        elif variant == 'native':
            await self.observe(session, 'native-before')
            self.execute('publish_post', 'd', 'd', {'content': body('d', 3)}, tick=3)
            await self.observe(session, 'native-post')
            self.execute('follow', 'a', 'd')
            await self.observe(session, 'native-follow')
            self.execute('like_post', 'a', 'post_2')
            await self.observe(session, 'native-like')
            self.execute('comment', 'a', 'post_1', {'content': '完整评论及理由🙂'})
            await self.observe(session, 'native-comment')
            self.directory.update('a', persona='改变偏好 完整人格🙂')
            await self.observe(session, 'native-persona')
            self.store.transaction(lambda w: w.execute("INSERT INTO recommendation_notes(body) VALUES('无关原文')"))
            page = await self.observe(session, 'native-unrelated')
            await self.read_all(session, page)
        elif variant == 'semantic-change':
            for index, change in enumerate(('selected', 'following', 'posts', 'vectors', 'persona')):
                await self.wait_change(session, change, index)
        elif variant == 'paging':
            first = await self.observe(session, 'paging-first')
            await self.observe(session, 'paging-c', actor='c')
            await self.observe(session, 'paging-a-again')
            await self.read_all(session, first)
            self.execute('like_post', 'a', 'post_1')
            await self.reject_cursor(session, first.next_cursor, 'ranking-change')
            page = await self.observe(session, 'paging-restart')
            ids = await self.read_all(session, page)
            await self.observe(session, 'paging-query', query='查询改变 完整原文🙂')
            await self.observe(session, 'paging-tick', tick=3)
            preview = await self.social.recommended_feed('a', 2, record_impressions=False)
            before = [self.social.post_details(key)['view_count'] for key in ids]
            self.social.present('a', [ids[-1]])
            await self.social.after_tick()
            after = [self.social.post_details(key)['view_count'] for key in ids]
            self.record(dict(event='exposure', ids=ids, before=before, after=after,
                preview=[item['post_id'] for item in preview], presented=ids[-1],
                recommended=self.social.recommended_ids('a')), event=True)
        return DriverResult('completed', {'variant': variant}, 'rule')

def build(config):
    if config.get('case') != 'recommendation':
        raise ValueError('case 必须为 recommendation')
    variant = config.get('variant', 'default')
    if variant not in VARIANTS:
        raise ValueError(f'未知 recommendation variant: {variant}; 可用值: {VARIANTS}')
    if config.get('provider') != 'scripted':
        raise ValueError('规则推荐验收的 provider 必须为 scripted')
    start, end = config.get('start', 1), config.get('end', 2)
    if type(start) is not int or start != 1 or type(end) is not int or end < 1:
        raise ValueError('start 必须为 1；end 必须为正整数')

    def resources(ctx):
        import chromadb
        from chromadb.config import Settings
        store = ctx.require('storage', 'store')
        ctx.provide('embeddings', {'offline': OfflineEmbed(store)})
        ctx.provide('client', chromadb.EphemeralClient(Settings(anonymized_telemetry=False)))
        ctx.provide('rank', CountingRanker())
        ctx.provide('candidates', SelectedPosts(store))
        ctx.provide('relations', ExternalRelations(store))

    external = Plugin('recommendation.external', requires=('storage',), schema_requires=('social.data',), install=resources, schema=(
        'CREATE TABLE recommendation_selected(id TEXT PRIMARY KEY NOT NULL,FOREIGN KEY(id) REFERENCES social_posts(id))',
        'CREATE TABLE recommendation_following(follower TEXT NOT NULL,followee TEXT NOT NULL,PRIMARY KEY(follower,followee),FOREIGN KEY(follower) REFERENCES actors(id),FOREIGN KEY(followee) REFERENCES actors(id))',
        'CREATE TABLE recommendation_notes(id INTEGER PRIMARY KEY,body TEXT NOT NULL)',
        'CREATE TABLE recommendation_observations(id INTEGER PRIMARY KEY,body TEXT NOT NULL)',
        'CREATE TABLE recommendation_events(id INTEGER PRIMARY KEY,body TEXT NOT NULL)',
    ))
    cfg = {'social_media': {'content_length_limit': -1, 'recommendation':
        dict(WEIGHTS, use_embedding_similarity=True, post_count=1)}}
    candidates = 'recommendation.external' if variant in ('external-selection', 'semantic-change') else 'social.candidates'
    relations = 'recommendation.external' if variant in ('external-relations', 'semantic-change') else 'social.relations'
    capacity = 1 if variant == 'paging' else 128
    social = social_plugin('abcd', edges=[], config=cfg, ranking=('recommendation.external', 'rank'),
        embedding=('recommendation.external', 'offline'), vector_client=('recommendation.external', 'client'),
        cache_capacity=capacity)
    recommendation = social_recommendation_plugin(candidates=candidates, relations=relations,
        ranking=('recommendation.external', 'rank'), semantic='social.semantic', cache_capacity=capacity)
    social = replace(social, includes=tuple(recommendation if p.name == recommendation.name else p for p in social.includes))

    def schedule_install(ctx):
        scenario = Scenario(ctx.require('storage', 'store'), ctx.require('social', 'mechanism'),
            ctx.require('social.recommendation', 'recommendation'), ctx.require('recommendation.external', 'rank'),
            ctx.require('recommendation.external', 'embeddings')['offline'], ctx.require('recommendation.external', 'candidates'),
            ctx.require('recommendation.external', 'relations'), ctx.require('actors.data', 'directory'), variant)

        async def prepare(phase):
            await scenario.seed()
            return scenario

        async def consume(phase):
            for actor in ('ac' if variant == 'default' else 'a'):
                phase.activate(actor)
                await phase.drain()

        class Schedule:
            async def next_step(self, completed_step):
                return None if completed_step >= end else StepPlan(2, (Phase('read', consume, prepare=prepare),))
        ctx.provide('schedule', Schedule())

    async def run(session):
        return await session.prepared.run(session)

    schedule = Plugin('schedule', requires=('runtime', 'storage', 'social', 'social.recommendation',
        'recommendation.external', 'actors.data'), install=schedule_install)
    plugins = rule_preset(records=[ActorRecord(actor, 'rule', persona=f'完整人格原文🙂{actor}') for actor in 'abcd'],
        run=run, allows=lambda *args: True, schedule=schedule, plugins=(external, social))
    return RunPlan(plugins, RunContract(release=config['release'], dependencies={'python': sys.version},
        configuration=dict(config, case='recommendation', variant=variant, weights=WEIGHTS, models={},
            recommendation=dict(candidates=[candidates, 'candidates'], relations=[relations, 'relations'],
                ranking=['recommendation.external', 'rank'], semantic=['social.semantic', 'semantic'],
                cache_capacity=capacity, social_config=cfg)),
        time={'start': start, 'end': end}, budgets={}))


def validate(path):
    """只读验收；返回 id/passed/expected/actual/location 的检查列表。"""
    path = Path(path)
    manifest = json.loads((path / 'runner.json').read_text())
    variant = manifest['contract']['configuration']['variant']
    with StageReader(path) as reader:
        records = reader.read(lambda r: [json.loads(row[0]) for row in r.iter_query(
            'SELECT body FROM recommendation_observations ORDER BY id')])
        events = reader.read(lambda r: [json.loads(row[0]) for row in r.iter_query(
            'SELECT body FROM recommendation_events ORDER BY id')])
        views = reader.read(lambda r: dict(r.iter_query('SELECT id,view_count FROM social_posts ORDER BY ordinal')))
    checks = []
    def check(identifier, expected, actual, location):
        checks.append(dict(id=identifier, passed=actual == expected, expected=expected, actual=actual, location=location))
    status = json.loads((path / 'runner-status.json').read_text())
    check('recommendation.completed', 'completed', status['status'], 'runner-status.status')
    check('recommendation.diagnostics', 0, status['diagnostic_errors'], 'runner-status.diagnostic_errors')
    check('recommendation.complete-step', manifest['contract']['time']['end'], status['complete_step'], 'runner-status.complete_step')
    if manifest['source'] is not None:
        check('recommendation.restore-step', 1, manifest['source']['step'], 'runner.source.step')
        source_manifest = json.loads((Path(manifest['source']['path']) / 'runner.json').read_text())
        check('recommendation.restore-run-id', True, manifest['run_id'] != source_manifest['run_id'], 'runner.run_id')
        check('recommendation.restore-complete', 2, status['complete_step'], 'runner-status.complete_step')
        resumed = [r for r in records if r['step'] == 2]
        expected_actors = ['a', 'c'] if variant == 'default' else ['a']
        check('recommendation.restore-consumers', expected_actors, [r['actor'] for r in resumed], 'recommendation_observations.step=2.actor')
        check('recommendation.restore-cold-ranks', [1]*len(expected_actors), [r['rank_delta'] for r in resumed], 'recommendation_observations.step=2.rank_delta')
    check('recommendation.records', True, bool(records), 'recommendation_observations')
    presented = [e['presented'] for e in events if e['event'] == 'exposure']
    check('recommendation.actual-exposure', {key: presented.count(key) for key in views}, views, 'social_posts.view_count')
    for index, record in enumerate(records, 1):
        direct = record['direct']
        actual = {key: record[key] for key in ('following', 'similarities', 'candidates', 'ranked', 'total')}
        expected = {key: direct[key] for key in actual}
        check(f'recommendation.snapshot.{index}', expected, actual, f'recommendation_observations:{index}')
        check(f'recommendation.revision.{index}', direct['revision'], record['source_revision'], f'recommendation_observations:{index}.source_revision')
        check(f'recommendation.components.{index}', [9]*record['total'],
              [len(item['components']) for item in record['ranked']], f'recommendation_observations:{index}.ranked')
        if record['page'] is not None:
            page = record['page']
            location = f'recommendation_observations:{index}.page'
            check(f'recommendation.page-total.{index}', record['total'], page['total'], location + '.total')
            check(f'recommendation.page-version.{index}', record['source_revision'], page['revision'][0], location + '.revision')
            if page['cursor'] is not None:
                identity = json.loads(page['cursor']['identity'])
                check(f'recommendation.cursor-binding.{index}',
                    [record['actor'], {'time': record['tick'], 'phase': 'read'}, '/social/feed', *page['revision']],
                    identity[1:], location + '.cursor.identity')
                inherited = manifest['source'] is not None and record['step'] <= manifest['source']['step']
                expected_run = source_manifest['run_id'] if inherited else manifest['run_id']
                check(f'recommendation.cursor-run.{index}', expected_run, identity[0], location + '.cursor.identity.run_id')
    rows = [r for r in records if r['step'] == 1]
    indexed = {r['label']: r for r in rows}
    if variant == 'default':
        check('recommendation.default-actors', ['a', 'a', 'c', 'c'], [r['actor'] for r in rows], 'recommendation_observations.actor')
        check('recommendation.default-ranks', [1, 0, 1, 0], [r['rank_delta'] for r in rows], 'recommendation_observations.rank_delta')
        check('recommendation.default-embeds', 3, rows[-1]['embed_calls'], 'recommendation_observations:4.embed_calls')
        for actor in ('a', 'c'):
            pages = [r for r in rows if r['actor'] == actor]
            check('recommendation.default-pages-' + actor, [i['post_id'] for i in pages[0]['ranked']],
                [i['post_id'] for r in pages for i in r['page']['items']], 'recommendation_observations.page.items')
            check('recommendation.default-continuation-' + actor, [True, False],
                [r['page']['cursor'] is not None for r in pages], 'recommendation_observations.page.cursor')
    if variant == 'external-selection':
        check('recommendation.selection', [1, 2, 2, 1], [r['total'] for r in rows], 'recommendation_observations.total')
        check('recommendation.unrelated-hit', 0, rows[2]['rank_delta'], 'recommendation_observations:3.rank_delta')
        check('recommendation.native-unchanged', 1, len({r['table_revisions']['social_posts'] for r in rows}), 'recommendation_observations.table_revisions.social_posts')
    if variant == 'external-relations':
        check('recommendation.external-following', [[], ['d']], [r['following'] for r in rows], 'recommendation_observations.following')
        check('recommendation.external-network', [1., 0.], [i['components']['network_score'] for i in rows[-1]['ranked']], 'recommendation_observations:2.ranked')
        check('recommendation.edges-unchanged', 1, len({r['table_revisions']['social_edges'] for r in rows}), 'recommendation_observations.table_revisions.social_edges')
        check('recommendation.relation-rerank', [1, 1], [r['rank_delta'] for r in rows], 'recommendation_observations.rank_delta')
    if variant == 'native':
        changes = [r for r in rows if r['label'].startswith('native-')]
        check('recommendation.native-reranks', [1, 1, 1, 1, 1, 1, 0], [r['rank_delta'] for r in changes], 'recommendation_observations.rank_delta')
        check('recommendation.native-post', 3, indexed['native-post']['total'], 'recommendation_observations.native-post.total')
        check('recommendation.native-follow', ['d'], indexed['native-follow']['following'], 'recommendation_observations.native-follow.following')
        for label, post, field in (('native-like', 'post_2', 'like_count'), ('native-comment', 'post_1', 'reply_count')):
            value = next(c[field] for c in indexed[label]['candidates'] if c['post_id'] == post)
            check('recommendation.' + label, 1, value, f'recommendation_observations.{label}.candidates.{post}.{field}')
        check('recommendation.native-semantic-update', True,
            indexed['native-before']['similarities'] != indexed['native-persona']['similarities'], 'recommendation_observations.native-persona.similarities')
        check('recommendation.native-originals', ['post_1', 'post_2', 'post_3'],
            sorted(e['post_id'] for e in events if e['event'] == 'original-read'), 'recommendation_events.original-read')
    if variant == 'paging':
        check('recommendation.lru-reranks', [1, 1, 1], [indexed[k]['rank_delta'] for k in ('paging-first', 'paging-c', 'paging-a-again')], 'recommendation_observations.rank_delta')
        check('recommendation.lru-equal', indexed['paging-first']['ranked'], indexed['paging-a-again']['ranked'], 'recommendation_observations.paging-a-again.ranked')
        check('recommendation.cursor-rejected', 1, sum(e['event'] == 'cursor-rejected' for e in events), 'recommendation_events.cursor-rejected')
        check('recommendation.paging-originals', ['post_1', 'post_1', 'post_2', 'post_2'],
            sorted(e['post_id'] for e in events if e['event'] == 'original-read'), 'recommendation_events.original-read')
        check('recommendation.query-tick-reranks', [1, 1], [indexed[k]['rank_delta'] for k in ('paging-query', 'paging-tick')], 'recommendation_observations.rank_delta')
        check('recommendation.exposure-record', 1, sum(e['event'] == 'exposure' for e in events), 'recommendation_events.exposure')
    if variant == 'semantic-change':
        rejected = [e for e in events if e['event'] == 'await-rejected']
        check('recommendation.await-changes', ['selected', 'following', 'posts', 'vectors', 'persona'], [e['change'] for e in rejected], 'recommendation_events.await-rejected')
        check('recommendation.await-no-rank', [0]*5, [e['rank_delta'] for e in rejected], 'recommendation_events.await-rejected.rank_delta')
        check('recommendation.await-single-embed', [1]*5, [e['embed_delta'] for e in rejected], 'recommendation_events.await-rejected.embed_delta')
        check('recommendation.await-source-read', [1]*5, [e['pool_reads_delta'] for e in rejected], 'recommendation_events.await-rejected.pool_reads_delta')
        for event in rejected:
            change = event['change']
            location = 'recommendation_events.await-rejected.' + change
            check('recommendation.await-read-candidates-' + change, event['old_candidates'], event['request_candidates']['candidates'], location)
            check('recommendation.await-read-following-' + change, event['old_following'], event['request_following']['following'], location)
            newer = indexed['await-new-' + change]
            hit = indexed['await-hit-' + change]
            check('recommendation.await-version-' + change, True, newer['source_revision'] > event['old_revision'], location)
            check('recommendation.await-recompute-' + change, [1, 0], [newer['rank_delta'], hit['rank_delta']], location)
    for index, event in enumerate(events, 1):
        if event['event'] == 'original-read':
            check(f'recommendation.original.{index}', event['expected'], event['content'], f'recommendation_events:{index}.content')
        if event['event'] == 'exposure':
            check('recommendation.preview-no-exposure', [0]*len(event['ids']), event['before'], f'recommendation_events:{index}.before')
            check('recommendation.presented-only', [int(key == event['presented']) for key in event['ids']], event['after'], f'recommendation_events:{index}.after')
    return checks


def compare(continuous, restored):
    """比较业务事实。run_id、source_revision/direct.revision、page.revision、
    cursor.identity 的 run_id/rank_revision/access_revision、table_revisions 属于运行身份；
    rank_calls/rank_delta/embed_calls/embed_delta/pool_reads 属于进程缓存计数。
    validate 分别验证这些本地字段；游标保留 actor/Moment/path/offset 进行比较。
    source.path 解析符号链接后比较同一实际目录。
    """
    def read(path):
        with StageReader(path) as reader:
            observations = reader.read(lambda r: [json.loads(row[0]) for row in r.iter_query(
                'SELECT body FROM recommendation_observations ORDER BY id')])
            events = reader.read(lambda r: [json.loads(row[0]) for row in r.iter_query(
                'SELECT body FROM recommendation_events ORDER BY id')])
            tables = reader.read(lambda r: {
                'social_posts': r.query('SELECT id,ordinal,author,created_tick,reply_to,like_count,reply_count,repost_count,view_count,engagement FROM social_posts ORDER BY ordinal'),
                'social_bodies': [(key, raw.decode(), tags) for key, raw, tags in r.iter_query('SELECT id,body,tags FROM social_bodies ORDER BY id')],
                'social_replies': [(key, post, author, tick, raw.decode()) for key, post, author, tick, raw in r.iter_query('SELECT id,post,author,tick,body FROM social_replies ORDER BY id')],
                'social_edges': r.query('SELECT id,follower,followee FROM social_edges ORDER BY id'),
                'actor_personas': r.query('SELECT actor,body FROM actor_personas ORDER BY actor'),
                'social_vectors': [(key, ordinal, dimension, list(struct.unpack('<' + str(dimension) + 'd', raw)))
                    for key, ordinal, dimension, raw in r.iter_query('SELECT id,ordinal,dimension,vector FROM social_vectors ORDER BY ordinal')],
                'recommendation_selected': r.query('SELECT id FROM recommendation_selected ORDER BY id'),
                'recommendation_following': r.query('SELECT follower,followee FROM recommendation_following ORDER BY follower,followee'),
            })
        projected = []
        for row in observations:
            value = {key: row[key] for key in ('label', 'step', 'actor', 'tick', 'query', 'following',
                'similarities', 'candidates', 'ranked', 'total', 'weights')}
            value['direct'] = {key: row['direct'][key] for key in ('following', 'similarities', 'candidates', 'ranked', 'total')}
            page = row['page']
            value['page'] = None if page is None else dict(total=page['total'], items=page['items'], cursor=None)
            if page is not None and page['cursor'] is not None:
                identity = json.loads(page['cursor']['identity'])
                value['page']['cursor'] = dict(actor=identity[1], moment=identity[2], path=identity[3], offset=page['cursor']['offset'])
            projected.append(value)
        return dict(observations=projected, events=events, **tables)

    expected, actual = read(continuous), read(restored)
    checks = []
    for key in expected:
        checks.append(dict(id='recommendation.compare.' + key, passed=expected[key] == actual[key],
                           expected=expected[key], actual=actual[key], location=key))
    manifest = json.loads((Path(restored) / 'runner.json').read_text())
    status = json.loads((Path(restored) / 'runner-status.json').read_text())
    source = manifest['source']
    for key, wanted, received in (
        ('source-path', str(Path(continuous).resolve()), None if source is None else str(Path(source['path']).resolve())),
        ('source-step', 1, None if source is None else source['step']),
        ('complete-step', 2, status['complete_step']),
    ):
        checks.append(dict(id='recommendation.compare.' + key, passed=wanted == received,
                           expected=wanted, actual=received, location='runner.' + key))
    return checks
