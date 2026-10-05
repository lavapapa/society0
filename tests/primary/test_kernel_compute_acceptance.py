"""实际 spawn 进程池的额度、退出及图机制消费者。"""
import asyncio
import json
import multiprocessing
import os
import threading

import pytest

from examples.core_next.graph_environment import GraphEnvironment, graph_plugin
from society0.kernel.composition import compose
from society0.kernel.compute import compute_plugin
from society0.kernel.plugins import Plugin, PluginHost


def blocked_compute(started, release, finished):
    started.set()
    try:
        if not release.wait(10): raise TimeoutError('test worker was not released')
        return os.getpid()
    finally:
        finished.set()


def worker_pid():
    return os.getpid()


@pytest.mark.asyncio
async def test_two_workers_reach_finite_barrier_concurrently():
    with multiprocessing.get_context('spawn').Manager() as manager:
        started = [manager.Event(), manager.Event()]
        finished = [manager.Event(), manager.Event()]
        release = manager.Event()
        async with PluginHost([compute_plugin(max_workers=2, max_pending=2)]) as host:
            compute = host.service('compute', 'compute')
            tasks = [asyncio.create_task(compute.run(blocked_compute, started[index], release, finished[index]))
                     for index in range(2)]
            try:
                assert all(await asyncio.gather(*(asyncio.to_thread(event.wait, 10) for event in started)))
                assert not any(event.is_set() for event in finished)
                release.set()
                identifiers = await asyncio.wait_for(asyncio.gather(*tasks), 10)
                assert len(set(identifiers)) == 2 and os.getpid() not in identifiers
            finally:
                release.set()
                await asyncio.gather(*tasks, return_exceptions=True)


@pytest.mark.asyncio
async def test_cancelled_native_work_holds_admission_until_completion(monkeypatch):
    with multiprocessing.get_context('spawn').Manager() as manager:
        started, release, finished = (manager.Event() for _ in range(3))
        async with PluginHost([compute_plugin(max_workers=2, max_pending=1)]) as host:
            compute = host.service('compute', 'compute')
            submit = compute._pool.submit
            submitted = []
            def record(*args, **kwargs):
                future = submit(*args, **kwargs)
                submitted.append(future)
                return future
            monkeypatch.setattr(compute._pool, 'submit', record)
            first = asyncio.create_task(compute.run(blocked_compute, started, release, finished))
            second = None
            try:
                assert await asyncio.to_thread(started.wait, 10)
                assert submitted[0].running()
                first.cancel()
                with pytest.raises(asyncio.CancelledError): await first
                second = asyncio.create_task(compute.run(worker_pid))
                await asyncio.sleep(0)
                await asyncio.sleep(0)
                assert len(submitted) == 1 and not second.done() and not finished.is_set()
                second.cancel()
                with pytest.raises(asyncio.CancelledError): await second
                assert len(submitted) == 1
                release.set()
                assert await asyncio.wait_for(compute.run(worker_pid), 10) != os.getpid()
                assert submitted[0].done() and finished.is_set()
                assert len(submitted) == 2
            finally:
                release.set()
                await asyncio.gather(*(task for task in (first, second) if task is not None), return_exceptions=True)


@pytest.mark.asyncio
async def test_host_close_waits_for_running_native_work(monkeypatch):
    with multiprocessing.get_context('spawn').Manager() as manager:
        started, release, finished = (manager.Event() for _ in range(3))
        closing = threading.Event()
        observations = []
        def unblock():
            if closing.wait(10): observations.append(finished.is_set())
            release.set()
        helper = threading.Thread(target=unblock)
        helper.start()
        try:
            async with PluginHost([compute_plugin(max_workers=1, max_pending=1)]) as host:
                compute = host.service('compute', 'compute')
                shutdown = compute._pool.shutdown
                def close_pool(*args, **kwargs):
                    closing.set()
                    return shutdown(*args, **kwargs)
                monkeypatch.setattr(compute._pool, 'shutdown', close_pool)
                pending = asyncio.create_task(compute.run(blocked_compute, started, release, finished))
                assert await asyncio.to_thread(started.wait, 10)
                pending.cancel()
                with pytest.raises(asyncio.CancelledError): await pending
                assert not finished.is_set()
            assert observations == [False] and finished.is_set()
            with pytest.raises(RuntimeError, match='closed'): await compute.run(worker_pid)
        finally:
            release.set()
            helper.join(10)
            assert not helper.is_alive()


@pytest.mark.asyncio
async def test_shared_compute_graph_projection_and_parent_canonical_write(tmp_path):
    source = tmp_path / 'graph.json'
    source.write_text(json.dumps({'nodes': [{'id': 'a', 'weight': 2}, {'id': 'b', 'weight': 3}],
                                 'edges': [['a', 'b']]}))
    def other_consumer(context):
        context.provide('graph', GraphEnvironment(context.require('storage', 'store'),
                                                context.require('compute', 'compute')))
    plugins = [compute_plugin(max_workers=2, max_pending=2),
               graph_plugin(source, name='left', compute=('compute', 'compute')),
               Plugin('right', ('storage', 'compute', 'left'), other_consumer)]
    async with compose(tmp_path / 'run', plugins) as host:
        compute = host.service('compute', 'compute')
        graph = host.service('left', 'graph')
        other = host.service('right', 'graph')
        assert graph.compute is other.compute is compute
        projections = await asyncio.gather(graph.projection_async(), other.projection_async())
        for network, weights in projections:
            assert list(network.edges) == [('a', 'b')] and list(weights) == [2, 3]
        graph.set_weight('a', 7)
        network, weights = await graph.projection_async()
        assert network.nodes['a']['weight'] == 7 and list(weights) == [7, 3]
        store = host.service('storage', 'store')
        assert store.read(lambda reader: reader.query('SELECT weight FROM graph_nodes WHERE id=?', ('a',))) == [(7.0,)]
        store.complete(1)
    async with compose(tmp_path / 'restored', plugins, source=tmp_path / 'run') as host:
        graph = host.service('left', 'graph')
        network, weights = await graph.projection_async()
        assert network.nodes['a']['weight'] == 7 and list(weights) == [7, 3]
        assert host.service('storage', 'store').complete_step == 1


@pytest.mark.asyncio
async def test_graph_snapshot_revision_is_rechecked_after_queued_native_compute(tmp_path):
    source=tmp_path/'graph.json'
    source.write_text(json.dumps({'nodes':[{'id':'a','weight':2},{'id':'b','weight':3}],'edges':[['a','b']]}))
    with multiprocessing.get_context('spawn').Manager() as manager:
        started,release,finished=(manager.Event() for _ in range(3))
        async with compose(tmp_path/'run',[compute_plugin(max_workers=1,max_pending=1),
                          graph_plugin(source,compute=('compute','compute'))]) as host:
            compute=host.service('compute','compute');graph=host.service('graph','graph')
            blocker=asyncio.create_task(compute.run(blocked_compute,started,release,finished))
            projection=None
            try:
                assert await asyncio.to_thread(started.wait,10)
                projection=asyncio.create_task(graph.projection_async())
                await asyncio.sleep(0);await asyncio.sleep(0)
                assert not projection.done()
                graph.set_weight('a',7);release.set()
                with pytest.raises(ValueError,match='graph changed'):await projection
                network,weights=await graph.projection_async()
                assert network.nodes['a']['weight']==7 and list(weights)==[7,3]
            finally:
                release.set()
                await asyncio.gather(*(task for task in (blocker,projection) if task is not None),return_exceptions=True)
