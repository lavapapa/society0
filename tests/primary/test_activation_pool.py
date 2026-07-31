import asyncio

import pytest

from society0 import (
    ActivationBatch,
    ActivationPool,
    ActivationPoolError,
    ActivationResult,
    ActivationSignal,
    ActivationSubmission,
    Society0,
)
from society0.schedule import CodeSchedule, StepContext


pytestmark = pytest.mark.primary


class _Env:
    activation_pool = None


class _World:
    _default_agent_concurrency = 3
    _default_agent_concurrency_source = "society0"
    agents_data = {}


@pytest.mark.asyncio
async def test_step_activation_pool_uses_runtime_concurrency_and_binds_environment():
    env = _Env()
    ctx = StepContext(
        step=0,
        step_name="activate",
        world=_World(),
        env=env,
        params={},
    )

    async with ctx.activation_pool() as pool:
        assert pool.capacity == 3
        assert pool.concurrency_source == "society0"
        assert env.activation_pool is pool

    assert env.activation_pool is None
    assert pool.closed is True


@pytest.mark.asyncio
async def test_activation_pool_refills_a_free_slot_before_slow_work_finishes():
    env = _Env()
    world = _World()
    world._default_agent_concurrency = 2
    ctx = StepContext(step=0, step_name="activate", world=world, env=env, params={})
    slow_release = asyncio.Event()
    fast_release = asyncio.Event()
    slow_started = asyncio.Event()
    fast_started = asyncio.Event()
    refill_started = asyncio.Event()

    async def slow():
        slow_started.set()
        await slow_release.wait()

    async def fast():
        fast_started.set()
        await fast_release.wait()

    async def refill():
        refill_started.set()

    async with ctx.activation_pool() as pool:
        pool.submit("slow", slow)
        pool.submit("fast", fast)
        pool.submit("refill", refill)
        await asyncio.wait_for(slow_started.wait(), timeout=1)
        await asyncio.wait_for(fast_started.wait(), timeout=1)

        fast_release.set()
        await asyncio.wait_for(refill_started.wait(), timeout=1)
        assert slow_release.is_set() is False

        slow_release.set()


@pytest.mark.asyncio
async def test_activation_pool_merges_queued_and_running_duplicates_into_one_round_each():
    env = _Env()
    world = _World()
    world._default_agent_concurrency = 1
    ctx = StepContext(step=0, step_name="activate", world=world, env=env, params={})
    blocker_release = asyncio.Event()
    first_round_started = asyncio.Event()
    first_round_release = asyncio.Event()
    batches = []

    async def blocker():
        await blocker_release.wait()

    async def activate(batch):
        batches.append(batch)
        if len(batches) == 1:
            first_round_started.set()
            await first_round_release.wait()

    async with ctx.activation_pool() as pool:
        pool.submit("blocker", blocker)
        first = pool.submit("agent:alice", activate, payload="agenda-a", dedupe_token="event-a")
        queued_merge = pool.submit(
            "agent:alice",
            activate,
            payload="message-b",
            dedupe_token="event-b",
        )
        duplicate = pool.submit(
            "agent:alice",
            activate,
            payload="message-b-again",
            dedupe_token="event-b",
        )

        assert first.disposition == "queued"
        assert queued_merge.disposition == "merged"
        assert duplicate.disposition == "duplicate_token"
        blocker_release.set()
        await asyncio.wait_for(first_round_started.wait(), timeout=1)
        assert batches[0].payloads == ("agenda-a", "message-b")

        follow_up = pool.submit(
            "agent:alice",
            activate,
            payload="contract-c",
            dedupe_token="event-c",
        )
        running_merge = pool.submit(
            "agent:alice",
            activate,
            payload="payment-d",
            dedupe_token="event-d",
        )
        repeated_running = pool.submit(
            "agent:alice",
            activate,
            payload="payment-d-again",
            dedupe_token="event-d",
        )

        assert follow_up.disposition == "follow_up"
        assert running_merge.disposition == "merged_follow_up"
        assert repeated_running.disposition == "duplicate_token"
        first_round_release.set()

    assert [batch.payloads for batch in batches] == [
        ("agenda-a", "message-b"),
        ("contract-c", "payment-d"),
    ]
    agent_results = [result for result in pool.results if result.key == "agent:alice"]
    assert all(isinstance(result, ActivationResult) for result in agent_results)
    assert all(isinstance(result.batch, ActivationBatch) for result in agent_results)
    assert all(
        isinstance(signal, ActivationSignal)
        for result in agent_results
        for signal in result.batch.signals
    )
    assert isinstance(first, ActivationSubmission)
    assert [(result.round, result.batch.payloads) for result in agent_results] == [
        (1, ("agenda-a", "message-b")),
        (2, ("contract-c", "payment-d")),
    ]


@pytest.mark.asyncio
async def test_activation_pool_surfaces_closure_errors_and_keeps_queryable_results():
    env = _Env()
    ctx = StepContext(
        step=0,
        step_name="activate",
        world=_World(),
        env=env,
        params={},
    )

    async def fail():
        raise ValueError("invalid activation")

    with pytest.raises(ActivationPoolError, match="invalid activation"):
        async with ctx.activation_pool() as pool:
            pool.submit("broken", fail)

    assert pool.closed is True
    assert env.activation_pool is None
    assert len(pool.results) == 1
    assert pool.results[0].key == "broken"
    assert pool.results[0].round == 1
    assert pool.results[0].batch.payloads == (None,)
    assert pool.results[0].batch.dedupe_tokens == ()
    assert pool.results[0].status == "error"
    assert isinstance(pool.results[0].error, ValueError)


@pytest.mark.asyncio
async def test_activation_pool_instruct_merges_prompts_for_one_agent_call():
    class InstructWorld(_World):
        agents_data = {"alice": {"id": "alice", "type": "enterprise"}}
        step = 4

        def __init__(self):
            self.calls = []

        async def instruct_agent(self, agent_id, instruction, **kwargs):
            self.calls.append((agent_id, instruction, kwargs))
            return {"status": "success", "content": "ok"}

    env = _Env()
    world = InstructWorld()
    ctx = StepContext(step=4, step_name="activate", world=world, env=env, params={})

    async with ctx.activation_pool() as pool:
        first = pool.instruct(
            "alice",
            "经营你的企业。",
            fovs=["operating_context"],
            actions=["industry"],
            dedupe_token="initial",
        )
        second = pool.instruct(
            "alice",
            "查看本轮新收到的信息。",
            fovs=["operating_context"],
            actions=["industry"],
            dedupe_token="inbox:42",
        )

        assert first.disposition == "queued"
        assert second.disposition == "merged"

    assert len(world.calls) == 1
    agent_id, instruction, kwargs = world.calls[0]
    assert agent_id == "alice"
    assert instruction == "经营你的企业。\n\n查看本轮新收到的信息。"
    assert kwargs["fovs"] == ["operating_context"]
    assert kwargs["action_tags"] == ["industry"]


@pytest.mark.asyncio
async def test_agent_serial_key_blocks_other_keys_without_occupying_pool_capacity():
    class InstructWorld(_World):
        agents_data = {
            "alice": {"id": "alice", "type": "enterprise"},
            "bob": {"id": "bob", "type": "enterprise"},
        }
        step = 4
        _default_agent_concurrency = 2

        async def instruct_agent(self, agent_id, instruction, **kwargs):
            assert agent_id == "alice"
            alice_instruct_started.set()
            await alice_instruct_release.wait()
            return {"status": "success", "content": "ok"}

    env = _Env()
    world = InstructWorld()
    ctx = StepContext(step=4, step_name="activate", world=world, env=env, params={})
    alice_instruct_started = asyncio.Event()
    alice_instruct_release = asyncio.Event()
    alice_industry_started = asyncio.Event()
    bob_started = asyncio.Event()

    async def run_alice_industry():
        alice_industry_started.set()

    async def run_bob():
        bob_started.set()

    async with ctx.activation_pool() as pool:
        alice_serial = pool.agent_serial_key("alice")
        pool.instruct("alice", "经营企业。")
        pool.submit_agent(
            "alice",
            ("industry", "alice"),
            run_alice_industry,
        )
        pool.submit_agent(
            "bob",
            ("industry", "bob"),
            run_bob,
        )

        await asyncio.wait_for(alice_instruct_started.wait(), timeout=1)
        await asyncio.wait_for(bob_started.wait(), timeout=1)
        assert alice_industry_started.is_set() is False

        alice_instruct_release.set()
        await asyncio.wait_for(alice_industry_started.wait(), timeout=1)

    alice_results = [
        result
        for result in pool.results
        if result.key in {("instruct", "alice"), ("industry", "alice")}
    ]
    assert len(alice_results) == 2
    assert {result.batch.serial_key for result in alice_results} == {alice_serial}


@pytest.mark.asyncio
async def test_rejected_serial_key_change_does_not_consume_dedupe_token():
    env = _Env()
    world = _World()
    world._default_agent_concurrency = 1
    ctx = StepContext(step=0, step_name="activate", world=world, env=env, params={})
    release = asyncio.Event()

    async def run():
        await release.wait()

    async with ctx.activation_pool() as pool:
        pool.submit("work", run, serial_key="serial-a", dedupe_token="first")
        with pytest.raises(ValueError, match="serial_key"):
            pool.submit("work", run, serial_key="serial-b", dedupe_token="retry")
        retry = pool.submit(
            "work",
            run,
            serial_key="serial-a",
            dedupe_token="retry",
        )
        assert retry.disposition == "merged"
        release.set()


@pytest.mark.asyncio
async def test_same_key_rejects_a_different_closure_before_consuming_token():
    env = _Env()
    ctx = StepContext(
        step=0,
        step_name="activate",
        world=_World(),
        env=env,
        params={},
    )
    seen = []

    async def first(batch):
        seen.append(("first", batch.payloads))

    async def second(batch):
        seen.append(("second", batch.payloads))

    async with ctx.activation_pool() as pool:
        pool.submit("shared", first, payload="a", dedupe_token="first")
        with pytest.raises(ValueError, match="handler_id"):
            pool.submit(
                "shared",
                second,
                payload="b",
                dedupe_token="retry",
            )
        accepted = pool.submit(
            "shared",
            first,
            payload="b",
            dedupe_token="retry",
        )
        assert accepted.disposition == "merged"

    assert seen == [("first", ("a", "b"))]


@pytest.mark.asyncio
async def test_instruct_rejects_different_execution_options_for_one_key():
    class InstructWorld(_World):
        agents_data = {"alice": {"id": "alice", "type": "enterprise"}}
        step = 0

        async def instruct_agent(self, agent_id, instruction, **kwargs):
            return {"status": "success", "content": "ok"}

    ctx = StepContext(
        step=0,
        step_name="activate",
        world=InstructWorld(),
        env=_Env(),
        params={},
    )

    async with ctx.activation_pool() as pool:
        pool.instruct(
            "alice",
            "first",
            fovs=["profile"],
            actions=["read"],
        )
        with pytest.raises(ValueError, match="handler_id"):
            pool.instruct(
                "alice",
                "second",
                fovs=["market"],
                actions=["write"],
            )


@pytest.mark.asyncio
async def test_drain_waits_for_work_submitted_on_idle_boundary():
    pool = ActivationPool(
        world=object(),
        capacity=1,
        concurrency_source="test",
    )
    await pool.start()
    first_started = asyncio.Event()
    first_release = asyncio.Event()
    late_started = asyncio.Event()
    late_release = asyncio.Event()

    async def first():
        first_started.set()
        await first_release.wait()

    async def late():
        late_started.set()
        await late_release.wait()

    pool.submit("first", first)
    await asyncio.wait_for(first_started.wait(), timeout=1)

    async def submit_on_idle_boundary():
        await pool._queue._finished.wait()
        pool.submit("late", late)

    late_submitter = asyncio.create_task(submit_on_idle_boundary())
    await asyncio.sleep(0)
    drain_task = asyncio.create_task(pool.drain())
    await asyncio.sleep(0)

    first_release.set()
    await asyncio.wait_for(late_started.wait(), timeout=1)
    assert drain_task.done() is False

    late_release.set()
    await asyncio.wait_for(drain_task, timeout=1)
    await asyncio.wait_for(late_submitter, timeout=1)
    await pool.close()


@pytest.mark.asyncio
async def test_close_waits_for_work_submitted_on_idle_boundary():
    pool = ActivationPool(
        world=object(),
        capacity=1,
        concurrency_source="test",
    )
    await pool.start()
    first_started = asyncio.Event()
    first_release = asyncio.Event()
    late_started = asyncio.Event()
    late_release = asyncio.Event()

    async def first():
        first_started.set()
        await first_release.wait()

    async def late():
        late_started.set()
        await late_release.wait()

    pool.submit("first", first)
    await asyncio.wait_for(first_started.wait(), timeout=1)

    async def submit_on_idle_boundary():
        await pool._queue._finished.wait()
        pool.submit("late", late)

    late_submitter = asyncio.create_task(submit_on_idle_boundary())
    await asyncio.sleep(0)
    close_task = asyncio.create_task(pool.close())
    await asyncio.sleep(0)

    first_release.set()
    await asyncio.wait_for(late_started.wait(), timeout=1)
    assert close_task.done() is False
    assert pool.closed is False

    late_release.set()
    await asyncio.wait_for(close_task, timeout=1)
    await asyncio.wait_for(late_submitter, timeout=1)
    assert pool.closed is True
    assert not pool._execution_tasks


@pytest.mark.asyncio
async def test_environment_can_submit_work_from_its_own_method_during_step_session():
    class SchedulingEnv:
        activation_pool = None

        def __init__(self):
            self.seen = []

        def request(self, label):
            async def handle(batch):
                self.seen.extend(batch.payloads)

            return self.activation_pool.enqueue(
                "shared-work",
                handle,
                payload=label,
                handler_id="scheduling-env:shared-work",
            )

    class SchedulingWorld(_World):
        step = 0

        def __init__(self, env):
            self.env = env

        def get_environment(self):
            return self.env

    env = SchedulingEnv()
    world = SchedulingWorld(env)
    schedule = CodeSchedule()

    @schedule.step(name="activate")
    async def activate(ctx):
        async with ctx.activation_pool():
            ctx.env.request("first")
            ctx.env.request("second")

    await schedule.execute_tick(tick=0, world=world)

    assert env.seen == ["first", "second"]
    assert env.activation_pool is None


@pytest.mark.asyncio
async def test_step_body_error_cancels_running_pool_work_and_unbinds_environment():
    env = _Env()
    ctx = StepContext(
        step=0,
        step_name="activate",
        world=_World(),
        env=env,
        params={},
    )
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def long_running():
        started.set()
        try:
            await asyncio.Future()
        finally:
            cancelled.set()

    with pytest.raises(RuntimeError, match="step failed"):
        async with ctx.activation_pool() as pool:
            pool.submit("long", long_running)
            await asyncio.wait_for(started.wait(), timeout=1)
            raise RuntimeError("step failed")

    assert pool.closed is True
    assert env.activation_pool is None
    assert cancelled.is_set() is True


@pytest.mark.asyncio
async def test_cancelling_step_task_cleans_up_workers_and_environment_binding():
    env = _Env()
    ctx = StepContext(
        step=0,
        step_name="activate",
        world=_World(),
        env=env,
        params={},
    )
    started = asyncio.Event()
    closure_cancelled = asyncio.Event()
    captured_pool = []

    async def long_running():
        started.set()
        try:
            await asyncio.Future()
        finally:
            closure_cancelled.set()

    async def run_step():
        async with ctx.activation_pool() as pool:
            captured_pool.append(pool)
            pool.submit("long", long_running)
            await asyncio.Future()

    step_task = asyncio.create_task(run_step())
    await asyncio.wait_for(started.wait(), timeout=1)
    step_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await step_task

    [pool] = captured_pool
    assert pool.closed is True
    assert env.activation_pool is None
    assert closure_cancelled.is_set() is True


@pytest.mark.asyncio
async def test_cancelling_during_close_cancels_running_work_and_unbinds_immediately():
    env = _Env()
    ctx = StepContext(
        step=0,
        step_name="activate",
        world=_World(),
        env=env,
        params={},
    )
    closure_started = asyncio.Event()
    closure_release = asyncio.Event()
    closure_cancelled = asyncio.Event()
    body_finished = asyncio.Event()
    captured_pool = []

    async def long_running():
        closure_started.set()
        try:
            await closure_release.wait()
        finally:
            closure_cancelled.set()

    async def run_step():
        async with ctx.activation_pool() as pool:
            captured_pool.append(pool)
            pool.submit("long", long_running)
            await closure_started.wait()
            body_finished.set()

    step_task = asyncio.create_task(run_step())
    await asyncio.wait_for(body_finished.wait(), timeout=1)
    await asyncio.sleep(0)
    step_task.cancel()
    try:
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(asyncio.shield(step_task), timeout=0.1)
    finally:
        closure_release.set()
        await asyncio.gather(step_task, return_exceptions=True)

    [pool] = captured_pool
    assert pool.closed is True
    assert env.activation_pool is None
    assert closure_cancelled.is_set() is True


@pytest.mark.asyncio
async def test_society0_runtime_concurrency_sets_default_activation_pool_capacity(tmp_path):
    engine = Society0(
        str(tmp_path),
        base_config={
            "agent_types": [{"id": "rule_agent", "archetype": "rule"}],
            "agents": [{"id": "alice", "type": "rule_agent", "state": {}}],
            "environment": {"type": "plain", "state": {}},
        },
        agent_concurrency=4,
    )
    observed = {}

    @engine.step(name="activate")
    async def activate(ctx):
        async with ctx.activation_pool() as pool:
            observed["capacity"] = pool.capacity
            observed["source"] = pool.concurrency_source
            observed["bound"] = ctx.env.activation_pool is pool

    await engine.run(1)

    assert observed == {
        "capacity": 4,
        "source": "society0",
        "bound": True,
    }
    assert engine.current_world_state.get_environment().activation_pool is None
