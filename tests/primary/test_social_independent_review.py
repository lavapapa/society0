"""非作者反例：只经公开装配/信息接口验证推荐合同，不修改实现。"""
from types import SimpleNamespace

import pytest

from society0.kernel.actors import ActorRecord, actor_data_plugin
from society0.kernel.composition import compose
from society0.kernel.interaction import InteractionScope, Moment, Query, interaction_plugin
from society0.kernel.plugins import Plugin
from society0.plugins.social import social_plugin
from society0.plugins.social_ranking import (
    ChronologicalRanker, RankedPost, RankingOutput, chronological_ranking_plugin,
)
from tests.primary.test_kernel_memory import Client, Embed


PLAIN = {'social_media': {'recommendation': {'use_embedding_similarity': False}}}


def assembly(*plugins):
    return [actor_data_plugin(records=[ActorRecord(a, 'unused', persona=a) for a in 'abc']),
            interaction_plugin(lambda *args: True), *plugins]


def seed(social):
    for tick in range(3):
        social.execute('publish_post', 'b', 'b', {'content': str(tick)}, tick)


@pytest.mark.asyncio
async def test_external_chronological_strategy_needs_no_hidden_embedding_resource(tmp_path):
    """显式选择时间排序且没有语义资源，首次读应可用。"""
    plugins = assembly(chronological_ranking_plugin(name='external'),
                       social_plugin('abc', edges=[], ranking=('external', 'rank')))
    async with compose(tmp_path / 'run', plugins) as host:
        seed(host.service('social', 'mechanism'))
        page = await host.service('interaction', 'information').query(
            InteractionScope('a', Moment(3, 'read')), '/social/feed', Query())
        assert page.total == 3
        assert 'social.semantic' not in {p.name for p in plugins[-1].includes}


@pytest.mark.asyncio
async def test_external_score_only_ranker_preserves_score_in_information(tmp_path):
    """RankedPost.components 可为空，独立 score 字段仍必须呈现。"""
    class ScoreOnly:
        def rank(self, source):
            items = tuple(RankedPost(p.post_id, 42.5) for p in source.candidates
                          if p.author_id != source.actor)
            return RankingOutput(items, len(items), source.revision)

    plugins = assembly(Plugin('external', install=lambda ctx: ctx.provide('rank', ScoreOnly())),
                       social_plugin('abc', edges=[], config=PLAIN, ranking=('external', 'rank')))
    async with compose(tmp_path / 'run', plugins) as host:
        seed(host.service('social', 'mechanism'))
        page = await host.service('interaction', 'information').query(
            InteractionScope('a', Moment(3, 'read')), '/social/feed', Query())
        assert page.items[0]['_recommendation_score'].get('total_score') == 42.5


@pytest.mark.asyncio
@pytest.mark.parametrize('broken', ['revision', 'total', 'duplicate'])
async def test_external_ranking_output_contract_is_checked_before_publication(tmp_path, broken):
    """策略边界拒绝过期版本、无法继续取得的总数及重复身份。"""
    class InvalidOutput(ChronologicalRanker):
        def rank(self, source):
            output = super().rank(source)
            return RankingOutput(
                output.items + output.items[:1] if broken == 'duplicate' else output.items,
                output.total + (broken in ('total', 'duplicate')),
                output.revision - (broken == 'revision'))

    plugins = assembly(Plugin('external', install=lambda ctx: ctx.provide('rank', InvalidOutput())),
                       social_plugin('abc', edges=[], config=PLAIN, ranking=('external', 'rank')))
    async with compose(tmp_path / 'run', plugins) as host:
        seed(host.service('social', 'mechanism'))
        with pytest.raises(ValueError):
            await host.service('interaction', 'information').query(
                InteractionScope('a', Moment(3, 'read')), '/social/feed', Query())


@pytest.mark.asyncio
async def test_interleaved_feed_pages_reuse_ranked_snapshot(tmp_path):
    """同一时点/业务版本的 actor A、C 交错续页不重新排序全候选。"""
    class CountingRanker(ChronologicalRanker):
        def __init__(self):
            self.calls = []

        def rank(self, source):
            self.calls.append((source.actor, source.revision))
            return super().rank(source)

    ranker = CountingRanker()
    plugins = assembly(Plugin('external', install=lambda ctx: ctx.provide('rank', ranker)),
                       social_plugin('abc', edges=[], config=PLAIN, ranking=('external', 'rank')))
    async with compose(tmp_path / 'run', plugins) as host:
        seed(host.service('social', 'mechanism'))
        info = host.service('interaction', 'information')
        scopes = [InteractionScope(a, Moment(3, 'read')) for a in 'ac']
        pages = [await info.query(scope, '/social/feed', Query(limit=1)) for scope in scopes]
        for scope, page in zip(scopes, pages):
            following = await info.query(scope, '/social/feed', Query(limit=1, cursor=page.next_cursor))
            assert following.items[0]['post_id'] != page.items[0]['post_id']
        assert len(ranker.calls) == 2, ranker.calls


@pytest.mark.asyncio
async def test_interleaved_feed_pages_reuse_actor_semantic_input(tmp_path):
    """A/C 偏好不同；另一 actor 的读取不能驱逐尚在分页的输入。"""
    embed, client = Embed(), Client()

    def resources(ctx):
        ctx.provide('embeddings', {'default': SimpleNamespace(embed=embed)})
        ctx.provide('client', client)

    plugins = assembly(Plugin('vectors', install=resources),
                       social_plugin('abc', edges=[], embedding=('vectors', 'default'),
                                     config={'social_media': {'recommendation': {'use_embedding_similarity': True}}},
                                     vector_client=('vectors', 'client')))
    async with compose(tmp_path / 'run', plugins) as host:
        seed(host.service('social', 'mechanism'))
        info = host.service('interaction', 'information')
        scopes = [InteractionScope(a, Moment(3, 'read')) for a in 'ac']
        pages = [await info.query(scope, '/social/feed', Query(limit=1)) for scope in scopes]
        for scope, page in zip(scopes, pages):
            await info.query(scope, '/social/feed', Query(limit=1, cursor=page.next_cursor))
        assert len(embed.calls) == 3, embed.calls  # 一批正文 + 两个完整主体偏好。


@pytest.mark.asyncio
async def test_recommendation_leaf_runs_and_restores_without_presentation_or_content(tmp_path):
    """纯数据初始化后，独立输入/双策略完全不装动作、正文写服务和曝光。"""
    from society0.plugins.social import (
        social_data_plugin, social_notifications_plugin, social_relations_plugin,
        social_candidates_plugin, social_recommendation_plugin, weighted_ranking_plugin,
    )

    def initialize(w):
        w.executemany('INSERT INTO social_posts VALUES(?,?,?,?,NULL,0,0,0,0,?)',
                      [('old', 1, 'b', 1, 5.0), ('new', 2, 'c', 2, 0.0)])
        w.executemany('INSERT INTO social_bodies VALUES(?,?,?)',
                      [('old', b'old body', '[]'), ('new', b'new body', '[]')])
        w.execute('UPDATE social_head SET post_count=2 WHERE id=1')
        w.execute("UPDATE social_members SET post_count=1 WHERE id IN ('b','c')")

    def plugins(strategy):
        return [actor_data_plugin(records=[ActorRecord(a, 'unused') for a in 'abc']),
                social_data_plugin('abc', edges=[], config=PLAIN),
                Plugin('fixture', schema_requires=('social.data',), initialize=initialize),
                social_notifications_plugin(), social_relations_plugin(), social_candidates_plugin(),
                chronological_ranking_plugin(name='recent'),
                weighted_ranking_plugin(name='weighted', config={
                    'engagement_weight': 1, 'chronological_weight': 0, 'network_weight': 0}),
                social_recommendation_plugin(ranking=(strategy, 'rank'))]

    async with compose(tmp_path / 'run', plugins('recent')) as host:
        source = host.service('social.recommendation', 'recommendation')
        value, ranked = await source.recommendation('a', 3)
        assert [p.post_id for p in ranked.items] == ['new', 'old']
        assert [p.post_id for p in host.service('weighted', 'rank').rank(value).items] == ['old', 'new']
        for absent in ('interaction', 'social', 'social.presentation', 'social.content', 'social.exposure'):
            with pytest.raises(KeyError):
                host.service(absent, 'anything')
        host.service('storage', 'store').complete(1)
    async with compose(tmp_path / 'restored', plugins('weighted'), source=tmp_path / 'run') as host:
        value, ranked = await host.service('social.recommendation', 'recommendation').recommendation('a', 3)
        assert [p.post_id for p in ranked.items] == ['old', 'new']
        assert ranked.total == len(value.candidates) == 2
        assert host.service('storage', 'store').read(
            lambda r: r.query('SELECT sum(view_count) FROM social_posts')) == [(0,)]


@pytest.mark.asyncio
async def test_named_instances_share_external_ranker_without_mixing_state(tmp_path):
    """正对照：同类双实例共享纯策略，正文/曝光/恢复仍分别归属。"""
    def plugins():
        return assembly(chronological_ranking_plugin(name='external'), *[
            social_plugin('abc', name=name, edges=[], config=PLAIN, ranking=('external', 'rank'))
            for name in ('one', 'two')])

    async with compose(tmp_path / 'run', plugins()) as host:
        one, two = [host.service(name, 'mechanism') for name in ('one', 'two')]
        one.execute('publish_post', 'b', 'b', {'content': 'one'}, 1)
        two.execute('publish_post', 'c', 'c', {'content': 'two'}, 2)
        one.present('a', ['post_1'])
        await one.after_tick()
        assert one.post_details('post_1')['view_count'] == 1
        assert two.post_details('post_1')['view_count'] == 0
        host.service('storage', 'store').complete(1)
    async with compose(tmp_path / 'restored', plugins(), source=tmp_path / 'run') as host:
        for name in ('one', 'two'):
            social = host.service(name, 'mechanism')
            assert social.post_details('post_1')['content'] == name
            assert social.post_details('post_1')['view_count'] == (name == 'one')


@pytest.mark.asyncio
async def test_duplicate_composite_leaf_names_fail_before_run_creation(tmp_path):
    """正对照：复合产品同名叶子仍参与全局预检。"""
    with pytest.raises(ValueError, match='duplicate plugin'):
        async with compose(tmp_path / 'run', assembly(
                social_plugin('abc', config=PLAIN),
                Plugin('other', includes=(Plugin('social.content'),)))):
            pytest.fail('duplicate child was installed')
    assert not (tmp_path / 'run').exists()
