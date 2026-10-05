"""最短规则运行：两个主体、完整结果、公开冻结合同。"""
import sys
from society0.kernel.interaction import interaction_plugin
from society0.kernel.results import StepResult, results_plugin
from society0.kernel.runner import RunContract, RunPlan
from society0.kernel.runtime import DriverResult, Phase, runtime_plugin
from society0.kernel.drivers import rule_driver_plugin
from society0.kernel.actors import ActorRecord,actor_plugin
from society0.kernel.schedule import SequenceSchedule, activate, schedule_plugin


def build(config):
    start, end = config['start'], config['end']
    actors = ('alice','bob')
    def rule(session):
        return DriverResult('completed',{'actor':session.actor.id,'time':session.moment.time},'rule')
    async def decide(context):
        results = await activate(context, actors)
        return StepResult(metrics={'decisions':len(results)},
            tables={'decisions':(item.result.value for item in results)})
    plugins = [interaction_plugin(lambda *args:True), results_plugin(),
        rule_driver_plugin(rule),actor_plugin({'rule':('rule_driver','factory')},records=[ActorRecord(name,'rule') for name in actors]),
        runtime_plugin(actor_service=('actors','actors'), information=('interaction','information'),actions=('interaction','actions'),
            store=('storage','store'),results=('results','results'),max_activations=2),
        schedule_plugin(SequenceSchedule(range(start,end+1),[Phase('decide',decide)]))]
    return RunPlan(plugins, RunContract(
        release=config['release'], dependencies={'python':sys.version},
        configuration={'plugins':{},'actors':list(actors),'models':{}},
        time={'start':start,'end':end}, budgets={'max_activations':2}))
