import asyncio
from contextlib import asynccontextmanager, contextmanager

import pytest

from society0.kernel import Plugin, PluginHost


@pytest.mark.parametrize("kind", ["missing", "cycle", "duplicate"])
def test_dependency_errors_precede_all_installers(kind):
    installed = []
    install = lambda ctx: installed.append(ctx.name)
    plugins = [Plugin("independent", install=install)]
    if kind == "missing":
        plugins += [Plugin("consumer", requires=("absent",), install=install)]
    elif kind == "cycle":
        plugins += [Plugin("a", ("b",), install), Plugin("b", ("a",), install)]
    else:
        plugins += [Plugin("independent", install=install)]

    async def run():
        with pytest.raises(ValueError, match={"missing": "absent", "cycle": "cycle", "duplicate": "duplicate"}[kind]):
            async with PluginHost(plugins):
                pytest.fail("invalid graph entered")

    asyncio.run(run())
    assert installed == []


def test_dependencies_services_and_reverse_cleanup():
    events = []
    service = object()

    def provider(ctx):
        events.append("provider installed")
        ctx.provide("data", service)
        ctx.on_close(events.append, "provider closed")

    async def consumer(ctx):
        assert ctx.require("provider", "data") is service
        events.append("consumer installed")

        async def close():
            await asyncio.sleep(0)
            assert ctx.require("provider", "data") is service
            events.append("consumer closed")

        ctx.on_close(close)
        ctx.provide("result", 7)

    async def run():
        host = PluginHost([Plugin("consumer", ("provider",), consumer), Plugin("provider", install=provider)])
        with pytest.raises(RuntimeError, match="ready"):
            host.service("provider", "data")
        async with host:
            assert host.service("consumer", "result") == 7
        with pytest.raises(RuntimeError, match="ready"):
            host.service("consumer", "result")
        with pytest.raises(RuntimeError):
            async with host:
                pass

    asyncio.run(run())
    assert events == ["provider installed", "consumer installed", "consumer closed", "provider closed"]


def test_partial_installer_failure_cleans_context_managers_in_reverse():
    events = []

    @contextmanager
    def sync_resource():
        events.append("sync open")
        try:
            yield "sync"
        finally:
            events.append("sync close")

    @asynccontextmanager
    async def async_resource():
        events.append("async open")
        try:
            yield "async"
        finally:
            await asyncio.sleep(0)
            events.append("async close")

    def provider(ctx):
        ctx.on_close(events.append, "provider close")

    async def broken(ctx):
        assert await ctx.enter_context(sync_resource()) == "sync"
        assert await ctx.enter_context(async_resource()) == "async"
        raise LookupError("install failed")

    async def run():
        with pytest.raises(LookupError, match="install failed"):
            async with PluginHost([Plugin("provider", install=provider), Plugin("broken", ("provider",), broken)]):
                pass

    asyncio.run(run())
    assert events == ["sync open", "async open", "async close", "sync close", "provider close"]


def test_duplicate_service_fails_and_cleans_partial_install():
    closed = []

    def install(ctx):
        ctx.on_close(closed.append, True)
        ctx.provide("same", 1)
        ctx.provide("same", 2)

    async def run():
        with pytest.raises(ValueError, match="same"):
            async with PluginHost([Plugin("p", install=install)]):
                pass

    asyncio.run(run())
    assert closed == [True]


@pytest.mark.parametrize("declared", [False, True])
def test_require_rejects_undeclared_dependency_or_missing_service(declared):
    def consumer(ctx):
        ctx.require("provider", "absent")

    async def run():
        with pytest.raises((ValueError, KeyError), match="provider|absent"):
            async with PluginHost([
                Plugin("provider", install=lambda ctx: ctx.provide("present", 1)),
                Plugin("consumer", ("provider",) if declared else (), consumer),
            ]):
                pass

    asyncio.run(run())


def test_hosts_and_plugin_service_names_are_independent():
    def plugin(name, value):
        return Plugin(name, install=lambda ctx: ctx.provide("value", value))

    async def run():
        async with PluginHost([plugin("p", 1), plugin("q", 2)]) as first:
            async with PluginHost([plugin("p", 3)]) as second:
                assert first.service("p", "value") == 1
                assert first.service("q", "value") == 2
                assert second.service("p", "value") == 3
            assert first.service("p", "value") == 1

    asyncio.run(run())


def test_cancelled_install_releases_partial_resources_and_dependencies():
    events = []

    async def run():
        started = asyncio.Event()

        def provider(ctx):
            ctx.on_close(events.append, "provider closed")

        async def waiting(ctx):
            async def close():
                await asyncio.sleep(0)
                events.append("waiting closed")

            ctx.on_close(close)
            started.set()
            await asyncio.Future()

        async def start():
            async with PluginHost([Plugin("provider", install=provider), Plugin("waiting", ("provider",), waiting)]):
                pytest.fail("cancelled installer entered")

        task = asyncio.create_task(start())
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(run())
    assert events == ["waiting closed", "provider closed"]


def test_body_cancellation_releases_resources():
    events = []

    async def run():
        started = asyncio.Event()

        async def task_body():
            async with PluginHost([Plugin("p", install=lambda ctx: ctx.on_close(events.append, "closed"))]):
                started.set()
                await asyncio.Future()

        task = asyncio.create_task(task_body())
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(run())
    assert events == ["closed"]


def test_cleanup_failure_still_closes_earlier_resources():
    events = []

    def install(ctx):
        ctx.on_close(events.append, "first closed")

        def broken_close():
            events.append("second failed")
            raise RuntimeError("cleanup failed")

        ctx.on_close(broken_close)

    async def run():
        with pytest.raises(RuntimeError, match="cleanup failed"):
            async with PluginHost([Plugin("p", install=install)]):
                pass

    asyncio.run(run())
    assert events == ["second failed", "first closed"]


def test_context_manager_cannot_suppress_install_failure():
    @contextmanager
    def suppressor():
        try:
            yield
        except LookupError:
            pass

    async def install(ctx):
        await ctx.enter_context(suppressor())
        raise LookupError("failed")

    async def run():
        with pytest.raises(LookupError, match="failed"):
            async with PluginHost([Plugin("p", install=install)]):
                pytest.fail("failed installation became ready")

    asyncio.run(run())


def test_context_manager_receives_install_error():
    received = []

    @contextmanager
    def resource():
        try:
            yield
        except LookupError as error:
            received.append(str(error))
            raise

    async def install(ctx):
        await ctx.enter_context(resource())
        raise LookupError("original failure")

    async def run():
        with pytest.raises(LookupError, match="original failure"):
            async with PluginHost([Plugin("p", install=install)]):
                pass

    asyncio.run(run())
    assert received == ["original failure"]
