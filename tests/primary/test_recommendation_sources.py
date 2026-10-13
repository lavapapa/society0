"""外部 SQL 来源以自己的依赖和同一个 ReadView 参与推荐。"""
import asyncio
from dataclasses import asdict, replace
import struct
from types import SimpleNamespace

import pytest

from society0.kernel.actors import ActorRecord, actor_data_plugin
from society0.kernel.composition import compose
from society0.kernel.interaction import InteractionScope, Moment, Query, Unavailable, interaction_plugin
from society0.kernel.plugins import Plugin
from society0.plugins.social import social_plugin, social_recommendation_plugin
from society0.plugins.social_ranking import Candidate, ChronologicalRanker, WeightedRanker


class SelectedPosts:
    def __init__(self, store):
        self.store = store
        self.views = []

    @property
    def dependencies(self):
        return ('external_selected_post_ids', 'social_posts')

    def active_pool_in(self, view, tick):
        self.views.append(view)
        return tuple(Candidate(*row) for row in view.iter_query(
            'SELECT p.id,p.author,p.created_tick,p.like_count,p.reply_count,p.repost_count,p.view_count,p.engagement '
            'FROM external_selected_post_ids s CROSS JOIN social_posts p ON p.id=s.id ORDER BY p.ordinal'))

    def active_pool(self, tick):
        return self.store.read(lambda view: [asdict(value) for value in self.active_pool_in(view, tick)])


class ExternalRelations:
    def __init__(self, store):
        self.store = store
        self.views = []

    @property
    def dependencies(self):
        return ('external_follows',)

    def following_in(self, view, actor):
        self.views.append(view)
        return frozenset(row[0] for row in view.iter_query('SELECT followee FROM external_follows WHERE follower=?', (actor,)))

    def following(self, actor):
        return self.store.read(lambda view: self.following_in(view, actor))


class CountingRanker(ChronologicalRanker):
    def __init__(self):
        self.calls = 0
    def rank(self, source):
        self.calls += 1
        return super().rank(source)


def assembly(*, ranker=None, semantic=False, source_factory=None, access_dependencies=(), allows=None, resources=None):
    ranker = ranker or CountingRanker()
    def install(ctx):
        store = ctx.require('storage', 'store')
        ctx.provide('candidates', source_factory(store) if source_factory else SelectedPosts(store))
        ctx.provide('relations', ExternalRelations(store))
        ctx.provide('rank', ranker)
    external = Plugin('external', requires=('storage',), install=install, schema=(
        'CREATE TABLE external_selected_post_ids(id TEXT PRIMARY KEY NOT NULL)',
        'CREATE TABLE external_follows(follower TEXT NOT NULL,followee TEXT NOT NULL,PRIMARY KEY(follower,followee))',
        'CREATE TABLE external_notes(id INTEGER PRIMARY KEY,body TEXT NOT NULL)',
        'CREATE TABLE observer_data(id INTEGER PRIMARY KEY,body TEXT NOT NULL)',
        'CREATE TABLE access_rules(id INTEGER PRIMARY KEY,allowed INTEGER NOT NULL)',
    ), initialize=lambda w: w.execute('INSERT INTO access_rules VALUES(1,1)'))
    cfg = {'social_media': {'content_length_limit': -1, 'recommendation': {'use_embedding_similarity': semantic, 'post_count': 1}}}
    social = social_plugin('abc', edges=[], config=cfg,
                           embedding=('resources', 'main') if semantic else None,
                           vector_client=('resources', 'client') if semantic else None,
                           ranking=('external', 'rank'))
    recommendation = social_recommendation_plugin(candidates='external', relations='external', ranking=('external', 'rank'),
                                                   semantic='social.semantic' if semantic else None)
    social = replace(social, includes=tuple(recommendation if p.name == recommendation.name else p for p in social.includes))
    return [actor_data_plugin(records=[ActorRecord(actor, 'unused', persona='人格原文🙂' + actor) for actor in 'abc']),
            interaction_plugin(allows or (lambda *args: True), access_dependencies=access_dependencies), external,
            *([resources] if resources else []), social]


def seed(host, *, both=False):
    social = host.service('social', 'mechanism')
    for actor, tick in [('b', 0), ('c', 1)]:
        social.execute('publish_post', actor, actor, {'content': ('完整中文正文🙂' + actor) * 10000}, tick)
    store = host.service('storage', 'store')
    store.transaction(lambda w: w.execute("INSERT INTO external_selected_post_ids VALUES('post_1')"))
    if both:
        store.transaction(lambda w: w.execute("INSERT INTO external_selected_post_ids VALUES('post_2')"))
    return social, store


@pytest.mark.asyncio
async def test_rec_01_external_selection_invalidates_cached_recommendation(tmp_path):
    ranker = CountingRanker()
    async with compose(tmp_path/'run', assembly(ranker=ranker)) as host:
        social, store = seed(host)
        recs = host.service('social.recommendation', 'recommendation')
        first = await recs.recommendation('a', 2)
        original_posts = store.read(lambda view: view.revision_for(('social_posts',)))
        store.transaction(lambda w: w.execute("INSERT INTO external_selected_post_ids VALUES('post_2')"))
        second = await recs.recommendation('a', 2)
        direct = await recs.recommendation_input('a', 2)
        assert [first[1].total, second[1].total, len(direct.candidates)] == [1, 2, 2]
        assert second[0].revision == second[1].revision == direct.revision
        assert second[0].revision > first[0].revision
        assert original_posts == store.read(lambda view: view.revision_for(('social_posts',)))
        assert ranker.calls == 2
        assert await recs.recommendation('a', 2) == second and ranker.calls == 2
        store.transaction(lambda w: w.execute("DELETE FROM external_selected_post_ids WHERE id='post_1'"))
        smaller = await recs.recommendation('a', 2)
        assert smaller[1].total == len((await recs.recommendation_input('a', 2)).candidates) == 1
        assert smaller[0].candidates[0].post_id == 'post_2'
        assert ranker.calls == 3


@pytest.mark.asyncio
async def test_rec_02_external_relations_update_all_network_scores(tmp_path):
    ranker = WeightedRanker(dict(network_weight=1, follow_bonus=1, chronological_weight=0, engagement_weight=0, similarity_weight=0))
    async with compose(tmp_path/'run', assembly(ranker=ranker)) as host:
        _, store = seed(host, both=True)
        recs = host.service('social.recommendation', 'recommendation')
        before = await recs.recommendation('a', 2)
        edges = store.read(lambda view: view.revision_for(('social_edges',)))
        store.transaction(lambda w: w.execute("INSERT INTO external_follows VALUES('a','b')"))
        source, after = await recs.recommendation('a', 2)
        assert source.following == frozenset({'b'})
        assert [item.post_id for item in after.items] == ['post_1', 'post_2']
        assert before[1].total == after.total == 2
        assert edges == store.read(lambda view: view.revision_for(('social_edges',)))
        for item in after.items:
            expected = float(item.post_id == 'post_1')
            assert item.components['network_score'] == item.components['network_contribution'] == item.components['total_score'] == expected
            assert len(item.components) == 9


@pytest.mark.asyncio
async def test_rec_03_unrelated_sql_writes_keep_cached_pool_rank_and_cursor(tmp_path):
    ranker = CountingRanker()
    async with compose(tmp_path/'run', assembly(ranker=ranker)) as host:
        _, store = seed(host, both=True)
        info = host.service('interaction', 'information')
        scope = InteractionScope('a', Moment(2, 'read'))
        first = await info.query(scope, '/social/feed', Query(limit=1))
        candidates = host.service('external', 'candidates')
        count = len(candidates.views)
        def unrelated(w):
            w.execute("INSERT INTO external_notes VALUES(1,'note')")
            w.execute("INSERT INTO observer_data VALUES(1,'observation')")
            w.execute('UPDATE actor_counts SET total=total WHERE active=1')
        store.transaction(unrelated)
        second = await info.query(scope, '/social/feed', Query(limit=1, cursor=first.next_cursor))
        assert first.total == second.total == 2 and first.revision == second.revision
        assert ranker.calls == 1 and len(candidates.views) == count
        assert second.items[0]['post_id'] != first.items[0]['post_id']


@pytest.mark.asyncio
async def test_rec_04_sources_and_member_revision_share_one_expired_readview(tmp_path, monkeypatch):
    async with compose(tmp_path/'run', assembly()) as host:
        _, store = seed(host, both=True)
        data = host.service('social.data', 'data')
        members = []
        original = data.member_in
        def member(view, actor):
            members.append(view)
            return original(view, actor)
        monkeypatch.setattr(data, 'member_in', member)
        source = await host.service('social.recommendation', 'recommendation').recommendation_input('a', 2)
        pools = host.service('external', 'candidates').views
        relations = host.service('external', 'relations').views
        assert pools[-1] is relations[-1] and pools[-1] in members
        assert len(source.candidates) == 2
        with pytest.raises(RuntimeError):
            pools[-1].query('SELECT 1')
        store.transaction(lambda w: w.execute("DELETE FROM external_selected_post_ids WHERE id='post_2'"))
        newer = await host.service('social.recommendation', 'recommendation').recommendation_input('a', 2)
        assert newer.revision > source.revision and len(newer.candidates) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('broken', ['missing_dependencies', 'missing_method', 'unknown_table', 'other_store'])
async def test_rec_08_source_contract_fails_during_install(tmp_path, broken):
    def source_factory(store):
        source = SelectedPosts(store)
        if broken == 'missing_dependencies':
            return SimpleNamespace(store=store, active_pool_in=source.active_pool_in)
        if broken == 'missing_method':
            return SimpleNamespace(store=store, dependencies=source.dependencies)
        if broken == 'unknown_table':
            return SimpleNamespace(store=store, dependencies=('missing_sql_table',), active_pool_in=source.active_pool_in)
        source.store = object()
        return source
    with pytest.raises(ValueError, match='candidates'):
        async with compose(tmp_path/broken, assembly(source_factory=source_factory)):
            pass


@pytest.mark.asyncio
async def test_rec_09_ranker_binding_and_public_config_are_static(tmp_path):
    from pydantic import ValidationError
    declared = {'network_weight': 1}
    ranker = WeightedRanker(declared)
    declared['network_weight'] = 0
    assert ranker.config.network_weight == 1
    with pytest.raises(ValidationError):
        ranker.config.network_weight = 0
    with pytest.raises(AttributeError):
        ranker.config = None
    async with compose(tmp_path/'run', assembly(ranker=ranker)) as host:
        recs = host.service('social.recommendation', 'recommendation')
        assert recs.ranker is ranker
        with pytest.raises(AttributeError):
            recs.ranker = ChronologicalRanker()


@pytest.mark.asyncio
async def test_rec_13_feed_and_root_search_version_include_external_sources(tmp_path):
    async with compose(tmp_path/'run', assembly(access_dependencies=('access_rules',))) as host:
        _, store = seed(host, both=True)
        info = host.service('interaction', 'information')
        scope = InteractionScope('a', Moment(2, 'read'))
        store.transaction(lambda w: w.execute('UPDATE access_rules SET allowed=1'))
        before = [await info.search_revision(scope, path) for path in ('/social/feed', '/social')]
        page = await info.query(scope, '/social/feed', Query(limit=1))
        store.transaction(lambda w: w.execute("DELETE FROM external_selected_post_ids WHERE id='post_2'"))
        after = [await info.search_revision(scope, path) for path in ('/social/feed', '/social')]
        assert before[0] != after[0] and before[1] != after[1]
        with pytest.raises(ValueError, match='cursor'):
            await info.query(scope, '/social/feed', Query(limit=1, cursor=page.next_cursor))


@pytest.mark.asyncio
@pytest.mark.parametrize('bad_pool', ['duplicate', 'iterator'])
async def test_rec_04_source_returns_complete_unique_materialized_values(tmp_path, bad_pool):
    async with compose(tmp_path/'run', assembly()) as host:
        _, store = seed(host, both=True)
        candidates = host.service('external', 'candidates')
        original = candidates.active_pool_in
        def invalid(view, tick):
            rows = original(view, tick)
            return rows + rows[:1] if bad_pool == 'duplicate' else iter(rows)
        candidates.active_pool_in = invalid
        with pytest.raises((TypeError, ValueError), match='candidates'):
            await host.service('social.recommendation', 'recommendation').recommendation_input('a', 2)


@pytest.mark.asyncio
async def test_rec_09_restored_run_rebinds_ranker_and_rejects_old_cursor(tmp_path):
    weighted = WeightedRanker(dict(network_weight=1, follow_bonus=1, chronological_weight=0, engagement_weight=0, similarity_weight=0))
    async with compose(tmp_path/'run', assembly(ranker=weighted)) as host:
        social, store = seed(host, both=True)
        store.transaction(lambda w: w.execute("INSERT INTO external_follows VALUES('a','b')"))
        info = host.service('interaction', 'information')
        page = await info.query(InteractionScope('a', Moment(2, 'read')), '/social/feed', Query(limit=1))
        assert page.items[0]['post_id'] == 'post_1'
        original = [social.post_details(key) for key in ('post_1', 'post_2')]
        store.complete(1)
    async with compose(tmp_path/'restored', assembly(ranker=ChronologicalRanker()), source=tmp_path/'run', step=1) as host:
        social = host.service('social', 'mechanism')
        assert [social.post_details(key) for key in ('post_1', 'post_2')] == original
        info = host.service('interaction', 'information')
        scope = InteractionScope('a', Moment(2, 'read'))
        with pytest.raises(ValueError, match='cursor'):
            await info.query(scope, '/social/feed', Query(limit=1, cursor=page.next_cursor))
        renewed = await info.query(scope, '/social/feed', Query(limit=1))
        assert renewed.items[0]['post_id'] == 'post_2' and renewed.total == 2
        assert renewed.next_cursor != page.next_cursor


@pytest.mark.asyncio
async def test_rec_11_source_pages_keep_all_original_bytes_and_exact_presented_exposure(tmp_path):
    async with compose(tmp_path/'run', assembly()) as host:
        social, store = seed(host, both=True)
        info = host.service('interaction', 'information')
        scope = InteractionScope('a', Moment(2, 'read'))
        first = await info.query(scope, '/social/feed', Query(limit=1))
        second = await info.query(scope, '/social/feed', Query(limit=1, cursor=first.next_cursor))
        assert first.total == second.total == 2 and second.next_cursor is None
        for item in (*first.items, *second.items):
            pieces = []
            offset = 0
            while True:
                chunk = await info.read(scope, item['content_path'], offset=offset, size=4096)
                pieces.append(chunk.data)
                offset = chunk.next_offset
                if offset is None:
                    break
            assert b''.join(pieces) == social.post_details(item['post_id'])['content'].encode()
            assert len(b''.join(pieces)) > 100000
        preview = await social.recommended_feed('a', 2, record_impressions=False)
        assert len(preview) == 1 and preview[0]['content'] == social.post_details(first.items[0]['post_id'])['content']
        await social.after_tick()
        assert [social.post_details(item['post_id'])['view_count'] for item in (*first.items, *second.items)] == [0, 0]
        shown = second.items[0]['post_id']
        social.present('a', [shown])
        await social.after_tick()
        assert social.recommended_ids('a') == [shown]
        assert [social.post_details(item['post_id'])['view_count'] for item in (*first.items, *second.items)] == [0, 1]


@pytest.mark.asyncio
async def test_rec_12_external_selection_and_hot_page_work_ignore_unselected_history(tmp_path, monkeypatch):
    import society0.plugins.social as module
    original_asdict = module.asdict
    materialized = []
    def observed(value):
        if isinstance(value, Candidate):
            materialized.append(value.post_id)
        return original_asdict(value)
    monkeypatch.setattr(module, 'asdict', observed)
    measured = []
    for history in (100, 10000):
        ranker = CountingRanker()
        async with compose(tmp_path/str(history), assembly(ranker=ranker)) as host:
            _, store = seed(host, both=True)
            def historical(w):
                w.executemany('INSERT INTO social_posts VALUES(?,?,?,0,NULL,0,0,0,0,0)',
                              ((f'historical_{i}', i + 3, 'b') for i in range(history - 2)))
                w.executemany('INSERT INTO social_bodies VALUES(?,?,?)',
                              ((f'historical_{i}', '完整历史正文'.encode(), '[]') for i in range(history - 2)))
                w.execute('UPDATE social_head SET post_count=?', (history,))
                w.execute("UPDATE social_members SET post_count=? WHERE id='b'", (history - 1,))
            store.transaction(historical)
            vm = [0]
            original_read = store.read
            def read(callback, **kwargs):
                def run(view):
                    view._connection.set_progress_handler(lambda: vm.__setitem__(0, vm[0]+1) or False, 1)
                    try:
                        return callback(view)
                    finally:
                        view._connection.set_progress_handler(None, 0)
                return original_read(run, **kwargs)
            store.read = read
            recs = host.service('social.recommendation', 'recommendation')
            await recs.recommendation('a', 2)
            cold = vm[0]
            vm[0] = 0
            pool_reads = len(host.service('external', 'candidates').views)
            info = host.service('interaction', 'information')
            scope = InteractionScope('a', Moment(2, 'read'))
            count = len(materialized)
            page = await info.query(scope, '/social/feed', Query(limit=1))
            hot = vm[0]
            assert page.total == 2 and len(materialized) - count == 1
            await info.query(scope, '/social/feed', Query(limit=10))
            assert len(materialized) - count == 3
            assert len(host.service('external', 'candidates').views) == pool_reads and ranker.calls == 1
            measured.append((cold, hot))
    print({'history': [100, 10000], 'cold_hot_sql_vm': measured})
    assert measured[1][0] <= measured[0][0] + 30
    assert measured[1][1] <= measured[0][1] + 10


class ControlledEmbed:
    def __init__(self):
        self.calls = []
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.pause = False

    async def embed(self, texts, *, metadata):
        self.calls.append((metadata['purpose'], list(texts)))
        if self.pause and metadata['purpose'] == 'social_recommendation':
            self.entered.set()
            await self.release.wait()
        return [[1.0, 0.0] for _ in texts]


def semantic_resources(embed):
    from tests.primary.test_kernel_memory import Client
    client = Client()
    def install(ctx):
        ctx.provide('embeddings', {'main': embed})
        ctx.provide('client', client)
    return Plugin('resources', install=install), client


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['selected', 'following', 'persona', 'posts', 'comment', 'reply_body', 'vectors', 'member', 'explicit_selected'])
async def test_rec_05_14_semantic_await_rejects_every_declared_source_change(tmp_path, change):
    embed = ControlledEmbed()
    resources, client = semantic_resources(embed)
    ranker = CountingRanker()
    async with compose(tmp_path/'run', assembly(ranker=ranker, semantic=True, resources=resources)) as host:
        social, store = seed(host, both=True)
        if change == 'reply_body':
            social.execute('comment', 'a', 'post_1', {'content': '旧完整回复🙂'}, 2)
        semantic = host.service('social.semantic', 'semantic')
        await semantic.flush_embeddings()
        recs = host.service('social.recommendation', 'recommendation')
        embed.pause = True
        task = asyncio.create_task(recs.recommendation('a', 2, query='明确完整偏好🙂' if change == 'explicit_selected' else None))
        try:
            await asyncio.wait_for(embed.entered.wait(), timeout=10)
            if change in ('selected', 'explicit_selected'):
                store.transaction(lambda w: w.execute("DELETE FROM external_selected_post_ids WHERE id='post_2'"))
            elif change == 'following':
                store.transaction(lambda w: w.execute("INSERT INTO external_follows VALUES('a','b')"))
            elif change == 'persona':
                host.service('actors.data', 'directory').update('a', persona='改变后完整人格🙂')
            elif change == 'posts':
                store.transaction(lambda w: w.execute("UPDATE social_posts SET like_count=like_count+1 WHERE id='post_1'"))
            elif change == 'comment':
                social.execute('comment', 'a', 'post_1', {'content': '新完整评论🙂'}, 2)
            elif change == 'reply_body':
                store.transaction(lambda w: w.execute('UPDATE social_replies SET body=?', ('改变后的完整回复🙂'.encode(),)))
            elif change == 'vectors':
                store.transaction(lambda w: w.execute("UPDATE social_vectors SET vector=? WHERE id='post_1'", (struct.pack('<2d', 9.0, 1.0),)))
            elif change == 'member':
                store.transaction(lambda w: w.execute("DELETE FROM social_members WHERE id='a'"))
            embed.release.set()
            with pytest.raises((ValueError, Unavailable)):
                await task
            assert ranker.calls == 0 and not recs._snapshots
            assert not semantic._semantic_cache
            assert social.post_details('post_1')['view_count'] == 0
            assert social.post_details('post_2')['view_count'] == 0
        finally:
            embed.release.set()
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_rec_04_semantic_query_and_persona_share_candidate_readview(tmp_path, monkeypatch):
    embed = ControlledEmbed()
    resources, client = semantic_resources(embed)
    async with compose(tmp_path/'run', assembly(semantic=True, resources=resources)) as host:
        social, store = seed(host, both=True)
        directory = host.service('actors.data', 'directory')
        semantic = host.service('social.semantic', 'semantic')
        observed = {'persona': [], 'text': []}
        original_persona, original_text = directory.persona_in, semantic.query_text_in
        def persona(view, actor):
            observed['persona'].append(view)
            return original_persona(view, actor)
        def text(view, actor, *, query=None):
            observed['text'].append(view)
            return original_text(view, actor, query=query)
        monkeypatch.setattr(directory, 'persona_in', persona)
        monkeypatch.setattr(semantic, 'query_text_in', text)
        await host.service('social.recommendation', 'recommendation').recommendation_input('a', 2)
        shared = host.service('external', 'candidates').views[-1]
        assert shared is host.service('external', 'relations').views[-1]
        assert observed['persona'] == observed['text'] == [shared]
        with pytest.raises(RuntimeError):
            shared.query('SELECT 1')
        preference = [texts[0] for purpose, texts in embed.calls if purpose == 'social_recommendation']
        assert preference == ['Persona:\n人格原文🙂a']


@pytest.mark.asyncio
async def test_rec_14_lagging_vector_projection_is_prepared_without_reembedding_bodies(tmp_path):
    embed = ControlledEmbed()
    resources, client = semantic_resources(embed)
    async with compose(tmp_path/'run', assembly(semantic=True, resources=resources)) as host:
        _, store = seed(host, both=True)
        semantic = host.service('social.semantic', 'semantic')
        await semantic.flush_embeddings()
        collection = next(iter(client.collections.values()))
        assert collection.metadata['through'] == 2
        collection.modify(metadata={'through': 0})
        source = await host.service('social.recommendation', 'recommendation').recommendation_input('a', 2)
        assert source.revision > 0 and collection.metadata['through'] == 2
        assert [purpose for purpose, texts in embed.calls].count('social_posts') == 1
        assert [purpose for purpose, texts in embed.calls].count('social_recommendation') == 1
        assert len(collection.rows) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize('actor,moment', [('c', Moment(2, 'read')), ('a', Moment(3, 'read')), ('a', Moment(2, 'different'))])
async def test_rec_13_cursor_is_bound_to_actor_and_complete_moment(tmp_path, actor, moment):
    async with compose(tmp_path/'run', assembly()) as host:
        seed(host, both=True)
        info = host.service('interaction', 'information')
        page = await info.query(InteractionScope('a', Moment(2, 'read')), '/social/feed', Query(limit=1))
        with pytest.raises(ValueError, match='cursor'):
            await info.query(InteractionScope(actor, moment), '/social/feed', Query(limit=1, cursor=page.next_cursor))


@pytest.mark.asyncio
async def test_rec_06_07_first_page_uses_prepared_version_and_interleaved_hot_pages(tmp_path):
    embed = ControlledEmbed()
    resources, client = semantic_resources(embed)
    ranker = CountingRanker()
    async with compose(tmp_path/'run', assembly(ranker=ranker, semantic=True, resources=resources)) as host:
        _, store = seed(host, both=True)
        info = host.service('interaction', 'information')
        scopes = [InteractionScope(actor, Moment(2, 'read')) for actor in 'ac']
        first = await info.query(scopes[0], '/social/feed', Query(limit=1))
        source, output = await host.service('social.recommendation', 'recommendation').recommendation('a', 2)
        assert first.revision[0] == source.revision == output.revision
        assert not store.read(lambda view: view.query('SELECT 1 FROM social_embedding_pending'))
        other = await info.query(scopes[1], '/social/feed', Query(limit=1))
        second = await info.query(scopes[0], '/social/feed', Query(limit=1, cursor=first.next_cursor))
        # c 自己的帖子仍排除，单条结果没有续页。
        assert other.total == 1 and other.next_cursor is None
        assert first.total == second.total == 2 and first.revision == second.revision
        assert ranker.calls == 2
        assert [purpose for purpose, _ in embed.calls].count('social_posts') == 1
        assert [purpose for purpose, _ in embed.calls].count('social_recommendation') == 2
        assert all(len(text) > 50000 for purpose, texts in embed.calls if purpose == 'social_posts' for text in texts)


@pytest.mark.asyncio
async def test_rec_13_permission_change_during_semantic_wait_rejects_first_page(tmp_path):
    embed = ControlledEmbed()
    resources, client = semantic_resources(embed)
    async with compose(tmp_path/'run', assembly(semantic=True, resources=resources, access_dependencies=('access_rules',))) as host:
        social, store = seed(host, both=True)
        await host.service('social.semantic', 'semantic').flush_embeddings()
        embed.pause = True
        task = asyncio.create_task(host.service('interaction', 'information').query(
            InteractionScope('a', Moment(2, 'read')), '/social/feed', Query(limit=1)))
        try:
            await asyncio.wait_for(embed.entered.wait(), timeout=10)
            store.transaction(lambda w: w.execute('UPDATE access_rules SET allowed=0'))
            embed.release.set()
            with pytest.raises(ValueError, match='revision|changed'):
                await task
            assert social.post_details('post_1')['view_count'] == 0
        finally:
            embed.release.set()
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
