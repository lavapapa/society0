"""同一环境、共享四个主体、两个独立轮次对话机制的正式运行计划。"""
import sys
from society0.kernel.actors import ActorRecord, actor_plugin
from society0.kernel.interaction import Ref, interaction_plugin
from society0.kernel.plugins import Plugin
from society0.kernel.results import StepResult, results_plugin
from society0.kernel.runner import RunContract, RunPlan
from society0.kernel.runtime import DriverResult, Phase, runtime_plugin
from society0.kernel.drivers import RuleDriver
from society0.kernel.schedule import SequenceSchedule, activate
from society0.plugins.round_robin import round_robin_plugin


async def talk(session):
    sent=[]
    for name in ('work','commons'):
        target=Ref(name,'participants',session.actor.id)
        result=await session.actions.invoke(name+'.send_message_to_partner',target,
            {'content':f'{session.actor.id} 在 {name} 的第 {session.moment.time} 轮完整消息'})
        if result.status!='completed':
            return DriverResult('incomplete',result.value,'message_rejected')
        sent.append({'mechanism':name,'actor':session.actor.id,**result.value})
    return DriverResult('completed',sent,'rule')


def build(config):
    start,end=config['start'],config['end']
    def schedule(context):
        work=context.require('work','mechanism')
        commons=context.require('commons','mechanism')
        def prepare(phase):
            pairs={name:[list(pair) for pair in mechanism.start_round(phase.moment.time)['pairs']]
                for name,mechanism in (('work',work),('commons',commons))}
            return StepResult(observations={'pairs':pairs})
        async def decide(phase):
            results=await activate(phase,'abcd')
            return StepResult(metrics={'decisions':len(results)},tables={
                'messages':(message for item in results for message in item.result.value)})
        context.provide('schedule',SequenceSchedule(range(start,end+1),
            [Phase('pair',prepare),Phase('talk',decide)]))
    plugins=[actor_plugin({'rule':lambda record:RuleDriver(talk)},records=[ActorRecord(x,'rule') for x in 'abcd']),
        interaction_plugin(lambda *args:True),results_plugin(),
        round_robin_plugin('abcd',group_size=4,name='work'),
        round_robin_plugin('acbd',group_size=4,name='commons'),
        runtime_plugin(actor_service=('actors','actors'),information=('interaction','information'),
            actions=('interaction','actions'),store=('storage','store'),results=('results','results'),max_activations=4),
        Plugin('schedule',('work','commons','runtime'),schedule)]
    return RunPlan(plugins,RunContract(
        release=config['release'],dependencies={'python':sys.version},
        configuration={'plugins':{'work':{'members':'abcd','group_size':4},'commons':{'members':'acbd','group_size':4}},
            'actors':[{'id':x,'driver':'rule'} for x in 'abcd'],'models':{}},
        time={'start':start,'end':end},budgets={'max_activations':4}))
