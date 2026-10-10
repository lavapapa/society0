"""独立推荐实验插件：复用公开输入和多个策略，保存自己的观察记录。"""
import json
from dataclasses import asdict

from society0 import Plugin
from society0.kernel.information_sql import _quote


class RecommendationObserver:
    def __init__(self, store, table, source, strategies):
        self.store, self.table = store, table
        self.source, self.strategies = source, strategies

    async def observe(self, actor, tick):
        source = await self.source.recommendation_input(actor, tick)
        rows = {}
        for name, strategy in self.strategies.items():
            output = strategy.rank(source)
            rows[name] = [{'post_id': item.post_id, 'score': item.score,
                           'components': dict(item.components)} for item in output.items]
        record = {'actor': actor, 'tick': tick, 'source_revision': source.revision,
                  'candidate_ids': [item.post_id for item in source.candidates],
                  'candidates': [asdict(item) for item in source.candidates], 'strategies': rows}
        self.store.transaction(lambda writer: writer.execute(
            f'INSERT INTO {self.table}(body) VALUES(?)',
            (json.dumps(record, ensure_ascii=False, allow_nan=False),)))
        return record

    def records(self):
        return self.store.read(lambda view: [json.loads(body) for body, in view.iter_query(
            f'SELECT body FROM {self.table} ORDER BY id')])


def recommendation_observer_plugin(*, source, strategies, name='recommendation.observer', storage='storage'):
    """source 为推荐输入服务，strategies 为实验名称到具名 rank 服务的映射。"""
    strategies = dict(strategies)
    table = _quote(name + '_observations')
    data = Plugin(name + '.data', schema=(f'CREATE TABLE {table}(id INTEGER PRIMARY KEY,body TEXT NOT NULL)',))

    def install(ctx):
        ctx.provide('observer', RecommendationObserver(ctx.require(storage, 'store'), table,
            ctx.require(*source), {label: ctx.require(*ref) for label, ref in strategies.items()}))

    requires = tuple(dict.fromkeys((storage, source[0], *(ref[0] for ref in strategies.values()))))
    return Plugin(name, requires=requires, includes=(data,), install=install)
