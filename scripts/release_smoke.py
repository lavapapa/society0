"""在独立安装环境运行规则、Social 和完整点恢复，无源码路径注入。"""
import asyncio
import importlib.metadata
from pathlib import Path
from tempfile import TemporaryDirectory

from society0.kernel.actors import ActorRecord, actor_plugin
from society0.kernel.composition import compose
from society0.kernel.interaction import interaction_plugin, InteractionScope, Moment, Ref
from society0.kernel.results import Results, StepResult
from society0.kernel.runner import RunContract, RunPlan, run_plan
from society0.kernel.runtime import DriverResult, Phase
from society0.kernel.schedule import SequenceSchedule, activate, schedule_plugin
from society0.kernel.storage import StageReader
from society0.presets import rule_preset
from society0.plugins import social_plugin


def plan(end):
    def decide(session):
        return DriverResult('completed', {'actor': session.actor.id, 'time': session.moment.time}, 'rule')

    async def phase(context):
        outcomes = await activate(context, ('a', 'b'))
        return StepResult(tables={'decisions': [item.result.value for item in outcomes]})

    plugins = rule_preset(records=[ActorRecord(actor, 'rule') for actor in ('a', 'b')],
                          run=decide, allows=lambda *args: True,
                          schedule=schedule_plugin(SequenceSchedule(range(1, end + 1), [Phase('decide', phase)])))
    contract = RunContract(release={'version': importlib.metadata.version('society0')}, dependencies={},
                           configuration={'actors': ['a', 'b']}, time={'start': 1, 'end': end}, budgets={})
    return RunPlan(plugins, contract)


def rows(path):
    with StageReader(path) as reader:
        results = Results(reader)
        return [[item['value'] for item in results.page(results.phase(step, 0)['tables']['decisions'])['items']]
                for step in (1, 2)]


async def main(root):
    import society0
    assert society0.__version__ == importlib.metadata.version('society0') == '2.0.0a1'
    import society0_filesystem  # 必须来自已安装的原生发行物。
    import society0.kernel.llm
    import society0.kernel.memory
    await run_plan(root / 'continuous', plan(2))
    await run_plan(root / 'first', plan(1))
    resumed = await run_plan(root / 'resumed', plan(2), source=root / 'first', step=1)
    assert resumed['complete_step'] == 2
    assert rows(root / 'resumed') == rows(root / 'continuous') == [
        [{'actor': 'a', 'time': 1}, {'actor': 'b', 'time': 1}],
        [{'actor': 'a', 'time': 2}, {'actor': 'b', 'time': 2}],
    ]
    plugins = [actor_plugin({'rule': lambda record: None}, records=[ActorRecord(i, 'rule') for i in 'ab']),
               interaction_plugin(lambda *args: True), social_plugin('ab', name='social', content_length_limit=-1)]
    async with compose(root / 'social', plugins) as host:
        actions = host.service('interaction', 'actions')
        posted = await actions.invoke(InteractionScope('a', Moment(1, 'act')), 'social.publish_post',
                                      Ref('social', 'participants', 'a'), {'content': '公开发行完整帖子🙂'})
        assert posted.status == 'completed'
        key = posted.value['post_id']
        liked = await actions.invoke(InteractionScope('b', Moment(1, 'act')), 'social.like_post',
                                     Ref('social', 'posts', key), {})
        assert liked.status == 'completed'
        expected = host.service('social', 'mechanism').post_details(key)
        assert expected['likes'] == ['b']
        host.service('storage', 'store').complete(1)
    async with compose(root / 'social-restored', plugins, source=root / 'social') as host:
        assert host.service('social', 'mechanism').post_details(key) == expected
    print(f'Society0 {importlib.metadata.version("society0")}: installed rule, Social, results and recovery passed')


if __name__ == '__main__':
    with TemporaryDirectory(prefix='society0-release-') as directory:
        asyncio.run(main(Path(directory)))
