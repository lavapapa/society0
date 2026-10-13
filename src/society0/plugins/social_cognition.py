"""社交认知来源：完整新帖、增量资料与保存后的曝光效果。"""
import json
from dataclasses import asdict

from ..kernel.plugins import Plugin
from .cognition import SourceBatch, SourcePosition


class SocialPerception:
    def __init__(self, recommendations, content, exposure, *, post_count, domain):
        self.recommendations, self.content, self.exposure = recommendations, content, exposure
        self.post_count, self.domain = post_count, domain

    async def __call__(self, session, previous: SourcePosition | None) -> SourceBatch:
        source, ranked = await self.recommendations.recommendation(session.actor.id, session.moment.time)
        selected = ranked.items[:self.post_count]
        ids = [item.post_id for item in selected]
        candidates = {item.post_id: item for item in source.candidates}
        previous = {'seen': [], 'visible': {}, 'recommended': []} if previous is None else previous.position
        seen = set(previous['seen'])
        fresh = [identifier for identifier in ids if identifier not in seen]
        visible = {}
        for item in selected:
            visible[item.post_id] = {**asdict(candidates[item.post_id]), 'score': item.score,
                'components': dict(item.components),
                'details_path': '/world/' + self.domain + '/post_details/' + item.post_id}
        changed = [value for key, value in visible.items()
                   if key in seen and previous['visible'].get(key) != value]
        messages = []
        if fresh or changed or ids != previous['recommended']:
            payload = {'recommended_ids': ids, 'new_posts': [self.content.post_details(identifier) for identifier in fresh],
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
            post_count=state._recommendation_config.post_count, domain=state.name))
    requires = tuple(dict.fromkeys(ref[0] for ref in (recommendations, content, exposure, data)))
    return Plugin(name, requires=requires, install=install)
