import importlib.util
from pathlib import Path

ROOT=Path(__file__).parent

def load(name):
    spec=importlib.util.spec_from_file_location(name,ROOT/(name+'.py'))
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module

def test_plan_is_explicit_and_credential_free():
    module=load('real_acceptance')
    plan=module.command('chain',Path('/tmp/new-output'))
    assert len([x for x in plan if '::test_real_' in x])==3
    assert all('KEY' not in x and 'api_key' not in x for x in plan)

def test_rule_runtime_and_restore(tmp_path):
    module=load('rule_scale')
    result=module.run_case(tmp_path/'run',history=17,actors=3,steps=2)
    assert result['balances']==[[f'a-{i}',2] for i in range(3)]
    assert result['trade_count']==23
    assert result['complete_step']==2
    restored=module.restore_case(tmp_path/'run')
    assert restored['balances']==result['balances']
    assert restored['trade_count']==23 and restored['thread_count']==6

def test_endpoint_probe_preserves_tools_and_cache_usage(tmp_path):
    from types import SimpleNamespace
    module=load('endpoint_probe')
    calls=[]
    class Response:
        def __init__(self,value):self.value=value
        def model_dump(self,**kwargs):return self.value
    def create(**kwargs):
        calls.append(kwargs)
        if len(calls)==1:
            message={'role':'assistant','content':None,'tool_calls':[{'id':'call1','type':'function','function':{'name':'read_probe','arguments':'{}'}}]}
        else:message={'role':'assistant','content':'已确认 PROBE-37'}
        return Response({'choices':[{'message':message,'finish_reason':'tool_calls' if len(calls)==1 else 'stop'}],
            'usage':{'prompt_tokens':2100,'completion_tokens':8,'prompt_tokens_details':{'cached_tokens':2048}}})
    client=SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)),
        embeddings=SimpleNamespace(create=lambda **kw:Response({'data':[{'index':1,'embedding':[0.,1.]},{'index':0,'embedding':[1.,0.]}],'usage':{'prompt_tokens':12,'total_tokens':12}})))
    result=module.probe(client,llm='test',embedding='embed',output=tmp_path)
    assert len(calls)==5
    assert calls[1]['messages'][-1]=={'role':'tool','tool_call_id':'call1','content':'{"value":"PROBE-37"}'}
    assert calls[2]['messages']==calls[3]['messages']
    assert calls[4]['messages'][:-1]==calls[3]['messages']
    assert all(c['extra_body']=={'enable_thinking':False} for c in calls)
    assert result['requests'][2]['usage']['prompt_tokens_details']['cached_tokens']==2048
    assert result['embedding']['indices']==[1,0]

def test_cache_only_uses_two_identical_requests_without_tools(tmp_path):
    from types import SimpleNamespace
    module=load('endpoint_probe');calls=[]
    class Response:
        def model_dump(self,**kwargs):return {'usage':{'prompt_tokens':8000,'prompt_tokens_details':{'cached_tokens':0}},'choices':[{'message':{'role':'assistant','content':'收到'}}]}
    def create(**kwargs):calls.append(kwargs);return Response()
    client=SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    result=module.cache_only(client,llm='test',output=tmp_path)
    assert len(calls)==2 and calls[0]==calls[1]
    assert 'tools' not in calls[0] and 'prompt_cache_key' not in calls[0]
    assert len(calls[0]['messages'][0]['content'])>10000
    assert len(result['requests'])==2

def test_all_fifteen_cases_are_partitioned_and_keep_existing_budgets():
    module=load('real_acceptance')
    remaining=module.command('remaining',Path('/tmp/remaining'))
    nodes=[item for item in remaining if '::test_real_' in item]
    assert len(nodes)==12 and len(module.BUDGETS)==15
    assert set(module.CASES).isdisjoint(item.split('::')[1] for item in nodes)
    assert '-x' in remaining
    assert module.BUDGETS['test_real_vfs_discovery_pagination_original_and_action']['turns']==20
    assert sum(x['activations']*x['turns']+2*x['auto_write_activations'] for x in module.BUDGETS.values())==256
