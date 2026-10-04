"""最短规则运行：两个主体、完整结果、公开冻结合同。"""
import sys
from society0.kernel.interaction import interaction_plugin
from society0.kernel.results import StepResult, results_plugin
from society0.kernel.runner import RunContract, RunPlan
from society0.kernel.runtime import Actor, DriverResult, Phase, runtime_plugin
from society0.kernel.schedule import RuleDriver, activate, schedule_plugin


def build(config):
    start, end = config['start'], config['end']
    actors = [Actor(name, RuleDriver(lambda session: DriverResult(
        'completed', {'actor':session.actor.id,'time':session.moment.time}, 'rule')))
        for name in ('alice', 'bob')]
    async def decide(context):
        results = await activate(context, (actor.id for actor in actors))
        return StepResult(metrics={'decisions':len(results)},
            tables={'decisions':(item.result.value for item in results)})
    plugins = [interaction_plugin(lambda *args:True), results_plugin(),
        runtime_plugin(actors, information=('interaction','information'),actions=('interaction','actions'),
            store=('storage','store'),results=('results','results'),max_activations=2),
        schedule_plugin([Phase('decide',decide)])]
    return RunPlan(plugins, range(start,end+1), RunContract(
        release=config['release'], dependencies={'python':sys.version},
        configuration={'plugins':{},'actors':[actor.id for actor in actors],'models':{}},
        time={'start':start,'end':end}, budgets={'max_activations':2}))
