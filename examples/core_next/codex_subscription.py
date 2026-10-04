"""由官方 SIWC 账户提供模型的完整仿真计划；配置文件明确选择模型与研究预算。"""
from dataclasses import asdict
from importlib.metadata import version
import json
import sys

from society0.activation_pool import DEFAULT_MAX_ACTIVATIONS
from society0.kernel.actors import ActorRecord, actor_plugin
from society0.kernel.cognition import CognitiveInput
from society0.kernel.interaction import Action, ActionResult, interaction_plugin
from society0.kernel.llm import LLMPolicy
from society0.kernel.drivers import llm_driver_plugin
from society0.kernel.models import model_plugin
from society0.kernel.plugins import Plugin
from society0.kernel.results import StepResult, results_plugin
from society0.kernel.runner import RunContract, RunPlan
from society0.kernel.runtime import Phase, runtime_plugin
from society0.kernel.schedule import SequenceSchedule, activate, schedule_plugin
from society0.kernel.services import thread_plugin


def build(config):
    model, account, release, steps = (config[key] for key in ('model', 'account', 'release', 'steps'))
    if type(steps) is not int or steps < 1:
        raise ValueError('steps must be a positive integer')
    policy = LLMPolicy(**config.get('policy', {}))
    endpoint = {'id': 'chatgpt', 'provider_type': 'siwc', 'model': model, 'account': account,
                'concurrency': config.get('concurrency', 1), 'timeout': config.get('timeout', 120)}
    profile = {'endpoints': [endpoint], 'max_attempts': config.get('max_attempts', 2),
               'retry_delay': config.get('retry_delay', .1), 'request_options': config.get('request_options', {})}
    schema = ('CREATE TABLE proposals(ordinal INTEGER PRIMARY KEY, actor TEXT NOT NULL, proposal TEXT NOT NULL)',)

    def proposals(context):
        store = context.require('storage', 'store')
        actions = context.require('interaction', 'actions')
        def publish(scope, target, arguments):
            if target.key != scope.actor:
                return ActionResult('rejected', {'reason': 'proposal owner differs'})
            store.transaction(lambda writer: writer.execute(
                'INSERT INTO proposals(actor,proposal) VALUES(?,?)', (scope.actor, arguments['proposal'])))
            return ActionResult('completed', {'published': True, 'proposal': arguments['proposal']})
        actions.register(Action('proposals.publish', ('proposals', 'actor'), '发布自己对公共议题的完整建议。',
            {'type': 'object', 'properties': {'proposal': {'type': 'string'}}, 'required': ['proposal'],
             'additionalProperties': False}, publish), dependencies=('proposals',))
        context.provide('store', store)

    def cognition(context):
        threads = context.require('threads', 'threads')
        store = context.require('proposals', 'store')
        def perception(session, cursor):
            rows = store.read(lambda view: [dict(ordinal=o, actor=a, proposal=p)
                for o, a, p in view.iter_query('SELECT ordinal,actor,proposal FROM proposals WHERE ordinal>? ORDER BY ordinal', (cursor or 0,))])
            body = {'time': session.moment.time, 'proposals': rows,
                    'action_target': {'namespace': 'proposals', 'kind': 'actor', 'key': session.actor.id}}
            return [{'role': 'user', 'content': json.dumps(body, ensure_ascii=False)}], rows[-1]['ordinal'] if rows else cursor
        builder = CognitiveInput(threads, perception,
            environment=config.get('environment', '讨论一个公共议题。你可以查找并发布建议，也可以直接结束；建议原文会进入共享环境。'))
        context.provide('input',builder)

    async def decide(phase):
        outcomes = await activate(phase, ('participant',))
        return StepResult(metrics={'activations': len(outcomes)},
            tables={'decisions': ({'actor': item.actor_id, 'status': item.result.status,
                                  'reason': item.result.reason, 'value': item.result.value} for item in outcomes)})

    plugins = [interaction_plugin(lambda actor, operation, target: True), thread_plugin(), results_plugin(),
        model_plugin({'codex': profile}), Plugin('proposals', ('storage', 'interaction'), proposals, schema=schema),
        Plugin('cognition',('threads','proposals'),cognition),
        llm_driver_plugin(provider=('models','models','codex'),input_builder=('cognition','input'),policy=policy),
        actor_plugin({'llm':('llm_driver','factory')}, records=(ActorRecord('participant', 'llm', persona=config.get('persona', '公共议题参与者')),),
                     ),
        runtime_plugin(actor_service=('actors', 'actors'), information=('interaction', 'information'),
                       actions=('interaction', 'actions'), store=('storage', 'store'), results=('results', 'results')),
        schedule_plugin(SequenceSchedule(range(1, steps + 1), [Phase('discussion', decide)]))]
    return RunPlan(plugins, RunContract(
        release=release, dependencies={'python': sys.version, **{name: version(name) for name in
            ('society0', 'pydantic-ai-slim', 'openai', 'Authlib', 'apsw')}},
        configuration={'models': {'codex': profile}, 'persona': config.get('persona', '公共议题参与者'),
                       'environment': config.get('environment', '讨论一个公共议题。你可以查找并发布建议，也可以直接结束；建议原文会进入共享环境。')},
        time={'start': 1, 'end': steps}, budgets={'llm': asdict(policy), 'runtime_capacity': 1, 'max_activations': DEFAULT_MAX_ACTIVATIONS}))
