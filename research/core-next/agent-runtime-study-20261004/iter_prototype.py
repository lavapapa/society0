"""公开 Agent.iter + 现有 Ledger 原型。使用既有离线消费者，无产品源码修改。"""
import asyncio
import json
import tempfile
from pathlib import Path
from pydantic_ai import Agent, Tool
from pydantic_ai.messages import ModelRequest, UserPromptPart
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.usage import UsageLimits
from pydantic_ai import ModelRequestNode
from jsonschema.validators import validator_for
from society0.kernel.llm import _Ledger, _json, _ToolInputError, _BudgetReached
from society0.kernel.model_messages import history
from tests.primary.test_kernel_llm import setup, reply, invoke
from society0.kernel.llm import LLMPolicy


async def run(driver, session):
    threads, policy = driver.threads, driver.policy
    tid=threads.open(session.actor.id,session.moment,policy.mode)
    session.cursors['thread_id']=tid
    for item in driver.input_builder(session): threads.append_message(tid,item)
    ledger=_Ledger(driver,session,tid)
    declarations=driver._tools()
    schemas={item['function']['name']:validator_for(item['function']['parameters'])(item['function']['parameters']) for item in declarations}
    options={**policy.request_options,'tools':declarations,'parallel_tool_calls':policy.parallel_tool_calls}
    state={'calls':{},'structured':None,'incomplete':None}

    async def request(messages, info):
        # 原型仅使用项目 FakeProvider。产品桥应直接返回 ModelProvider 的原始 SDK ModelResponse。
        # 这里沿用已有 Thread 权威输入，不通过 all_messages() 再物化一份全历史。
        response=await driver.provider.request(tid,options)
        body={key:value for key,value in response.items() if key in ('role','content','tool_calls','reasoning_content')}
        threads.append_message(tid,body)
        if response.get('incomplete_reason') or response.get('finish_reason')=='length':
            state['incomplete']=response.get('incomplete_reason','output_token_limit')
        state['calls']={call['id']:call for call in response.get('tool_calls',())}
        return history([body])[0]

    def wrap(name):
        async def execute(ctx, **arguments):
            identifier=ctx.tool_call_id
            call=state['calls'][identifier]
            previous=threads.get_tool_result(tid,identifier)
            if previous is not None:
                if previous['call']!=call: raise ValueError('tool call identity reused with different content')
                ledger.replay(identifier,previous['metadata'])
                threads.append_message(tid,{'role':'tool','tool_call_id':identifier,'content':previous['content']})
                return previous['content']
            ledger.effects=[]
            if ledger.terminal and ledger.requirements_met():
                feedback=_json({'status':'not_executed','reason':'activation_completed'})
            elif not schemas[name].is_valid(arguments):
                feedback=_json({'error':'invalid_tool_arguments'})
            elif name=='submit_result':
                state['structured']=arguments
                feedback=_json({'status':'completed','result':arguments})
            else:
                ledger.call_id=identifier
                ledger.shell_call=False
                try: feedback=_json(await driver._dispatch(name,arguments,session,ledger,None))
                except _ToolInputError as error: feedback=_json({'error':'invalid_tool_input','message':str(error)})
            threads.save_tool_result(tid,call,feedback,metadata={'actions':ledger.effects,'structured':state['structured'] if name=='submit_result' else None})
            ledger.applied_receipts.add(identifier)
            return feedback
        return execute

    tools=[Tool.from_schema(wrap(item['function']['name']),item['function']['name'],item['function']['description'],
                            item['function']['parameters'],takes_ctx=True,sequential=True) for item in declarations]
    agent=Agent(FunctionModel(request),tools=tools,retries=0)
    result,reason='incomplete','agent_error'
    try:
        async with agent.iter(message_history=history(threads.read_messages(tid)),
                              usage_limits=UsageLimits(request_limit=policy.max_turns)) as run:
            node=run.next_node
            while not Agent.is_end_node(node):
                if Agent.is_call_tools_node(node):
                    if state['incomplete']:
                        reason=state['incomplete'];break
                    if not state['calls'] and not ledger.requirements_met():
                        feedback={'role':'user','content':_json({'required_names':policy.required_names,'completed':sorted(ledger.completed)})}
                        threads.append_message(tid,feedback)
                        node=ModelRequestNode(ModelRequest(parts=[UserPromptPart(feedback['content'])]));continue
                    pending=[]
                    for call in state['calls'].values():
                        fn=call['function']
                        if fn['name']=='action_invoke' and threads.get_tool_result(tid,call['id']) is None:
                            args=json.loads(fn['arguments'])
                            if schemas['action_invoke'].is_valid(args):pending.append(args['name'])
                    ledger.preflight(pending)
                node=await run.next(node)
                if Agent.is_model_request_node(node):
                    if (ledger.terminal or state['structured'] is not None) and ledger.requirements_met():
                        result,reason='completed','terminal_action';break
                    if policy.max_action_calls is not None and ledger.used>=policy.max_action_calls and ledger.used:
                        reason='action_budget_exhausted';break
            else: result,reason='completed','natural_completion'
    except _BudgetReached as error:reason=str(error)
    finally:threads.close(tid,result,reason=reason)
    return {'status':result,'reason':reason,'thread_id':tid}


async def main():
    results=[]
    cases=[('terminal',[reply(invoke())],LLMPolicy(),True,1,'completed'),
           ('required',[reply(text='early1'),reply(text='early2'),reply(invoke())],LLMPolicy(required_names=('work',)),True,1,'completed'),
           ('duplicate_across_turns',[reply(invoke('same')),reply(invoke('same')),reply(text='done')],LLMPolicy(max_action_calls=2),False,1,'completed'),
           ('batch_budget',[reply(invoke('a'),invoke('b'))],LLMPolicy(max_action_calls=1,parallel_tool_calls=True),False,0,'incomplete'),
           ('length',[reply(invoke(),finish='length')],LLMPolicy(),False,0,'incomplete'),
           ('sequential_terminal',[reply(invoke('a'),invoke('b'))],LLMPolicy(parallel_tool_calls=True),True,1,'completed')]
    for name,replies,policy,terminal,effects,status in cases:
        with tempfile.TemporaryDirectory() as folder:
            store,threads,provider,driver,session,calls=setup(Path(folder),replies,policy=policy,terminal=terminal)
            try:
                result=await run(driver,session)
                assert len(calls)==effects and result['status']==status
                if effects:
                    assert any(m['role']=='tool' for m in threads.read_messages(result['thread_id']))
                results.append({'case':name,'effects':len(calls),'requests':len(provider.requests),'status':result['status']})
            finally:store.close()
    print(json.dumps(results,ensure_ascii=False,indent=2))

asyncio.run(main())
