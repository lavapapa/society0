"""保留规范池和 strict schema 算法，隔离退役 World 装配。"""
import asyncio
import copy
import pytest


@pytest.mark.asyncio
async def test_pool_has_no_world_dependency_and_refills_free_slot():
    from society0.activation_pool import ActivationPool
    pool=ActivationPool(capacity=2,concurrency_source='test')
    slow=asyncio.Event();quick=asyncio.Event();refilled=asyncio.Event()
    async def a():await slow.wait()
    async def b():await quick.wait()
    async def c():refilled.set()
    await pool.start()
    try:
        pool.submit('a',a);pool.submit('b',b);pool.submit('c',c)
        quick.set()
        await asyncio.wait_for(refilled.wait(),1)
        assert not slow.is_set()
        slow.set()
        assert len(await pool.drain())==3
    finally:
        slow.set();await pool.close()


def test_strict_schema_keeps_original_and_retired_registry_is_absent():
    from society0 import function_registry as module
    schema={'type':'object','properties':{'text':{'type':'string'},'values':{'type':'array','items':{'type':'integer'}}},'required':['text']}
    original=copy.deepcopy(schema)
    normalized=module.normalize_strict_function_parameters(schema)
    module.validate_strict_function_parameters(normalized)
    assert schema==original
    assert normalized['required']==['text','values']
    assert normalized['properties']['values']['type']==['array','null']
    assert not hasattr(module,'FunctionRegistry')


def test_shared_logging_does_not_construct_retired_agent_thread_store(tmp_path):
    import subprocess,sys,os
    script="""
import sys
from pathlib import Path
from society0.logging import ExperimentLogContext
context=ExperimentLogContext(Path(sys.argv[1]))
assert not hasattr(context,'agent_thread_store')
assert not any(name.startswith('society0.agent') for name in sys.modules)
context.close()
"""
    result=subprocess.run([sys.executable,'-c',script,str(tmp_path/'logs')],capture_output=True,text=True,env={**os.environ,'PYTHONPATH':'src'})
    assert result.returncode==0,result.stderr
