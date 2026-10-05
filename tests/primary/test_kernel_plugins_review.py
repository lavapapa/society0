"""插件主机的非作者生命周期与冻结合同检查。"""
import asyncio
from contextlib import contextmanager

import pytest

from society0.kernel import Plugin, PluginHost


@pytest.mark.asyncio
async def test_review_context_is_expired_after_host_closes():
    captured = {}

    def provider(ctx):
        ctx.provide('value', object())

    def consumer(ctx):
        captured['ctx'] = ctx
        ctx.require('provider', 'value')

    async with PluginHost([Plugin('provider', install=provider),
                           Plugin('consumer', ('provider',), consumer)]):
        pass
    ctx = captured['ctx']
    with pytest.raises(RuntimeError, match='closed'):
        ctx.require('provider', 'value')
    for operation in [lambda: ctx.provide('late', 1), lambda: ctx.on_close(lambda: None)]:
        with pytest.raises(RuntimeError):
            operation()

    @contextmanager
    def resource():
        pytest.fail('expired context acquired a new resource')
        yield

    with pytest.raises(RuntimeError):
        await ctx.enter_context(resource())


@pytest.mark.asyncio
async def test_review_previous_installer_cannot_register_during_next_install():
    captured = {}

    def first(ctx):
        captured['ctx'] = ctx
        ctx.provide('value', 1)

    async def second(ctx):
        with pytest.raises(RuntimeError):
            captured['ctx'].provide('late', 2)
        with pytest.raises(RuntimeError):
            captured['ctx'].on_close(lambda: None)
        assert ctx.require('first', 'value') == 1

    async with PluginHost([Plugin('first', install=first), Plugin('second', ('first',), second)]):
        pass


@pytest.mark.asyncio
async def test_review_mutating_input_dependencies_does_not_expand_runtime_access():
    dependencies = ['provider']
    captured = {}

    def consumer(ctx):
        captured['ctx'] = ctx

    plugins = [Plugin('provider', install=lambda ctx: ctx.provide('value', 1)),
               Plugin('hidden', install=lambda ctx: ctx.provide('value', 2)),
               Plugin('consumer', dependencies, consumer)]
    async with PluginHost(plugins):
        dependencies.append('hidden')
        with pytest.raises(ValueError, match='undeclared'):
            captured['ctx'].require('hidden', 'value')


@pytest.mark.asyncio
async def test_review_cleanup_failure_cannot_be_suppressed_by_another_resource():
    events = []

    @contextmanager
    def suppressor():
        try:
            yield
        except RuntimeError:
            events.append('suppressed')
        finally:
            events.append('resource closed')

    async def install(ctx):
        ctx.on_close(events.append, 'earliest closed')
        await ctx.enter_context(suppressor())

        def failing_cleanup():
            events.append('cleanup failed')
            raise RuntimeError('lost cleanup error')

        ctx.on_close(failing_cleanup)

    with pytest.raises(RuntimeError, match='lost cleanup error'):
        async with PluginHost([Plugin('p', install=install)]):
            pass
    assert 'earliest closed' in events
    assert 'resource closed' in events


@pytest.mark.asyncio
async def test_review_cancelled_async_cleanup_still_closes_dependency():
    events = []
    closing = asyncio.Event()
    release = asyncio.Event()

    def provider(ctx):
        ctx.on_close(events.append, 'provider closed')

    def consumer(ctx):
        async def cleanup():
            closing.set()
            await release.wait()
            events.append("consumer closed")

        ctx.on_close(cleanup)

    async def use_host():
        async with PluginHost([Plugin('provider', install=provider),
                               Plugin('consumer', ('provider',), consumer)]):
            pass

    task = asyncio.create_task(use_host())
    await closing.wait()
    task.cancel()
    await asyncio.sleep(0)
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done() and events == []
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert events == ["consumer closed", "provider closed"]


@pytest.mark.asyncio
async def test_review_second_enter_does_not_close_first_live_scope():
    events = []

    def install(ctx):
        ctx.provide('value', 3)
        ctx.on_close(events.append, 'closed')

    host = PluginHost([Plugin('p', install=install)])
    async with host:
        with pytest.raises(RuntimeError, match='once'):
            await host.__aenter__()
        assert host.service('p', 'value') == 3
        assert events == []
    assert events == ['closed']


@pytest.mark.asyncio
async def test_cleanup_own_cancellation_is_visible_and_dependency_closes():
    events=[]
    async def cancelled():raise asyncio.CancelledError('cleanup self cancel')
    def consumer(ctx):ctx.on_close(cancelled)
    with pytest.raises(RuntimeError,match='cleanup was cancelled'):
        async with PluginHost([Plugin('provider',install=lambda c:c.on_close(events.append,'closed')),
                               Plugin('consumer',('provider',),consumer)]):pass
    assert events==['closed']


@pytest.mark.asyncio
async def test_installer_own_cancellation_closes_partial_resources():
    events=[]
    async def cancelled(ctx):
        ctx.on_close(events.append,'partial closed')
        raise asyncio.CancelledError('installer self cancel')
    with pytest.raises(asyncio.CancelledError):
        async with PluginHost([Plugin('cancelled',install=cancelled)]):pass
    assert events==['partial closed']


@pytest.mark.asyncio
async def test_cross_task_host_close_keeps_entering_task_alive():
    events=[]
    host=PluginHost([Plugin('resource',install=lambda c:c.on_close(events.append,'closed'))])
    await host.__aenter__()
    await asyncio.create_task(host.__aexit__(None,None,None))
    assert events==['closed']
    assert not asyncio.current_task().cancelling()
