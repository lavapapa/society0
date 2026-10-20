"""社交认知来源：默认完整新帖、显式正文预览与保存后的曝光效果。"""
import json
from dataclasses import asdict

from ..kernel.plugins import Plugin
from .cognition import SourceBatch, SourcePosition


class SocialPerception:
    def __init__(self, recommendations, content, exposure, *, post_count, domain,
                 feed_content_preview_chars=None, feed_max_chars=None):
        self.recommendations, self.content, self.exposure = recommendations, content, exposure
        self.post_count, self.domain = post_count, domain
        self.preview_chars, self.body_budget = feed_content_preview_chars, feed_max_chars

    def _new_posts(self,ids):
        posts=[];remaining=self.body_budget
        for identifier in ids:
            post=self.content.post_details(identifier)
            body=post['content'];limit=len(body)
            if self.preview_chars is not None:limit=min(limit,self.preview_chars)
            if remaining is not None:limit=min(limit,remaining);remaining-=limit
            post.update(content=body[:limit],content_length=len(body),content_truncated=limit<len(body),
                content_path='/world/'+self.domain+'/content/'+identifier)
            posts.append(post)
        return posts

    async def __call__(self, session, previous: SourcePosition | None) -> SourceBatch:
        source, ranked = await self.recommendations.recommendation(session.actor.id, session.moment.time)
        ids = self.content.feed_ids(session.actor.id,session.moment.time,[item.post_id for item in ranked.items],self.post_count)
        ranks = {item.post_id: item for item in ranked.items}
        candidates = {item.post_id: item for item in source.candidates}
        previous = {'seen': [], 'visible': {}, 'recommended': []} if previous is None else previous.position
        seen = set(previous['seen'])
        fresh = [identifier for identifier in ids if identifier not in seen]
        visible = {}
        for identifier in ids:
            item=ranks.get(identifier)
            if identifier in candidates:metadata=asdict(candidates[identifier])
            else:
                details=self.content.post_details(identifier)
                metadata={key:details[key] for key in ('post_id','author_id','created_tick','like_count','reply_count','repost_count','view_count')}
            visible[identifier] = {**metadata, 'score': item.score if item else None,
                'components': dict(item.components) if item else {'trending':True},
                'details_path': '/world/' + self.domain + '/post_details/' + identifier}
        changed = [value for key, value in visible.items()
                   if key in seen and previous['visible'].get(key) != value]
        messages = []
        if fresh or changed or ids != previous['recommended']:
            payload = {'recommended_ids': ids, 'new_posts': self._new_posts(fresh),
                       'updated_posts': changed}
            messages.append({'role': 'user', 'content': '推荐动态\n' + json.dumps(payload, ensure_ascii=False, allow_nan=False)})
        next_position = {'seen': [*previous['seen'], *fresh], 'visible': visible, 'recommended': ids}
        effects = ()
        if fresh or ids != previous['recommended']:
            def presented(context):
                context.session.scope.check_active()
                self.exposure.record(session.actor.id, fresh, recommended=False)
                self.exposure.set_recommended(session.actor.id, ids)
            effects = (presented,)
        return SourceBatch(messages, next_position, effects=effects)


def social_source_plugin(*, name='social.source', recommendations=('social.recommendation', 'recommendation'),
                         content=('social.content', 'content'), exposure=('social.exposure', 'exposure'),
                         data=('social.data', 'data')):
    def install(ctx):
        state = ctx.require(*data)
        ctx.provide('source', SocialPerception(ctx.require(*recommendations), ctx.require(*content), ctx.require(*exposure),
            post_count=state._recommendation_config.post_count, domain=state.name,
            feed_content_preview_chars=state._recommendation_config.feed_content_preview_chars,
            feed_max_chars=state._recommendation_config.feed_max_chars))
    requires = tuple(dict.fromkeys(ref[0] for ref in (recommendations, content, exposure, data)))
    return Plugin(name, requires=requires, install=install)
