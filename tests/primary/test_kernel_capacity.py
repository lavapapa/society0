import asyncio
import pytest
from society0.kernel.runtime import Actor,Runtime,Phase,DriverResult
from society0.kernel.interaction import Information,Actions
from society0.kernel.storage import StageStore
from society0.kernel.results import Results,RESULTS_SCHEMA


@pytest.mark.parametrize('execution,capacity',[('serial',2),('independent',0),('independent',-1),('independent',True),('independent',1.5)])
def test_invalid_phase_capacity_is_configuration_error(execution,capacity):
    with pytest.raises(ValueError):Phase('bad',lambda ctx:None,execution=execution,capacity=capacity)


@pytest.mark.asyncio
async def test_phase_capacity_override_default_serial_and_recorded_source(tmp_path):
    current=0;peak=0;peaks=[];actor_active=set()
    class Driver:
        async def run(self,session):
            nonlocal current,peak
            assert session.actor.id not in actor_active
            actor_active.add(session.actor.id);current+=1;peak=max(peak,current)
            await asyncio.sleep(0)
            actor_active.remove(session.actor.id);current-=1
            return DriverResult('completed')
    with StageStore.create(tmp_path/'run',RESULTS_SCHEMA) as store:
        results=Results(store)
        runtime=Runtime([Actor(str(i),Driver()) for i in range(6)],information=Information(lambda *a:True),actions=Actions(lambda *a:True),store=store,results=results,capacity=3)
        async def phase(ctx):
            nonlocal peak
            peak=0
            for i in range(6):ctx.activate(str(i))
            ctx.activate('0')
            await ctx.drain();peaks.append(peak)
        await runtime.run_step(1,1,[Phase('serial',phase),Phase('runtime',phase,execution='independent'),Phase('override',phase,execution='independent',capacity=2)])
        assert peaks==[1,3,2]
        assert [(results.phase(1,i)['capacity'],results.phase(1,i)['concurrency_source']) for i in range(3)]==[(1,'serial phase'),(3,'runtime'),(2,'phase')]
        await runtime.close()


@pytest.mark.asyncio
async def test_resource_wait_limit_is_independent_of_activation_capacity(tmp_path):
    gate=asyncio.Semaphore(1);resource_active=0;resource_peak=0;drivers=0;driver_peak=0
    class Driver:
        async def run(self,session):
            nonlocal resource_active,resource_peak,drivers,driver_peak
            drivers+=1;driver_peak=max(driver_peak,drivers)
            async with gate:
                resource_active+=1;resource_peak=max(resource_peak,resource_active)
                await asyncio.sleep(0)
                resource_active-=1
            drivers-=1
            return DriverResult('completed')
    with StageStore.create(tmp_path/'run',()) as store:
        runtime=Runtime([Actor(str(i),Driver()) for i in range(5)],information=Information(lambda *a:True),actions=Actions(lambda *a:True),store=store,capacity=2)
        async def phase(ctx):
            for i in range(5):ctx.activate(str(i))
            await ctx.drain()
        await runtime.run_step(1,1,[Phase('parallel',phase,execution='independent')])
        assert resource_peak==1 and driver_peak==2
        await runtime.close()


@pytest.mark.parametrize('capacity',[0,-1,True,1.5])
def test_invalid_runtime_capacity_rejected_before_activation(tmp_path,capacity):
    with StageStore.create(tmp_path/'run',()) as store:
        with pytest.raises(ValueError):Runtime([],information=None,actions=None,store=store,capacity=capacity)
