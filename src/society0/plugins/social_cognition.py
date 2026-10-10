"""推荐认知输入：完整新帖、增量资料与呈现后的曝光效果。"""
import json
from dataclasses import asdict, replace

from ..kernel.cognition import CognitiveInput
from ..kernel.plugins import Plugin


class SocialPerception:
    def __init__(self, recommendations, content, exposure, *, post_count, domain):
        self.recommendations, self.content, self.exposure = recommendations, content, exposure
        self.post_count, self.domain = post_count, domain

    async def __call__(self, session, position):
        source, ranked = await self.recommendations.recommendation(session.actor.id, session.moment.time)
        selected = ranked.items[:self.post_count]
        ids = [item.post_id for item in selected]
        candidates = {item.post_id: item for item in source.candidates}
        previous = position or {'seen': [], 'visible': {}, 'recommended': []}
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

        return messages, next_position


def social_cognition_plugin(*, name='social.cognition', recommendations=('social.recommendation', 'recommendation'),
                            content=('social.content', 'content'), exposure=('social.exposure', 'exposure'),
                            data=('social.data', 'data'), threads=('threads', 'threads'), environment='共享社交环境'):
    def install(ctx):
        state = ctx.require(*data)
        perception = SocialPerception(ctx.require(*recommendations), ctx.require(*content), ctx.require(*exposure),
            post_count=state.config.social_media.recommendation.post_count, domain=state.name)
        thread_store = ctx.require(*threads)

        async def build(session):
            effects = []

            async def perceive(current, position):
                messages, following = await perception(current, position)
                previous = position or {'seen': [], 'recommended': []}
                fresh = following['seen'][len(previous['seen']):]
                ids = following['recommended']
                if fresh or ids != previous['recommended']:
                    def presented(context):
                        context.session.scope.check_active()
                        perception.exposure.record(current.actor.id, fresh, recommended=False)
                        perception.exposure.set_recommended(current.actor.id, ids)
                    effects.append(presented)
                return messages, following

            batch = await CognitiveInput(thread_store, perceive, consumer=name, environment=environment)(session)
            return replace(batch, effects=tuple(effects))

        ctx.provide('perception', perception)
        ctx.provide('input_builder', build)

    requires = tuple(dict.fromkeys(ref[0] for ref in (recommendations, content, exposure, data, threads)))
    return Plugin(name, requires=requires, install=install)
