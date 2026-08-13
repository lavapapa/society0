import errno

import pytest

from society0 import Society0
from society0.incremental_checkpoint import V4CheckpointStore
from society0.persistence import PersistenceManager
from society0.recovery import classify_step_failure


pytestmark = pytest.mark.primary


def _config() -> dict:
    return {
        "agent_types": [{"id": "worker", "archetype": "rule"}],
        "agents": [{"id": "worker-a", "type": "worker", "state": {}}],
        "environment": {
            "type": "plain",
            "state": {"counter": 0},
            "state_schema": {
                "type": "object",
                "properties": {
                    "counter": {
                        "type": "integer",
                        "persistence": {"kind": "replaceable"},
                    }
                },
                "additionalProperties": False,
            },
        },
    }


def test_step_failure_only_marks_transient_transport_errors_retryable() -> None:
    timeout = classify_step_failure(
        TimeoutError("provider timeout"),
        failed_step=5,
        last_complete_step=4,
    )
    disk_full = classify_step_failure(
        OSError(errno.ENOSPC, "disk full"),
        failed_step=5,
        last_complete_step=4,
    )
    invariant = classify_step_failure(
        ValueError("quantity invariant failed"),
        failed_step=5,
        last_complete_step=4,
    )

    assert timeout.retryable is True
    assert timeout.recoverable is True
    assert timeout.failure_class == "provider_timeout"
    assert timeout.retry_scope == "agent_activation"
    assert disk_full.retryable is False
    assert disk_full.retry_scope == "step"
    assert invariant.retryable is False
    assert len(timeout.error_fingerprint) == 64


def test_wrapped_provider_timeout_message_remains_retryable() -> None:
    failure = classify_step_failure(
        RuntimeError("Agent failed: ReadTimeout: request timed out"),
        failed_step=5,
        last_complete_step=4,
    )

    assert failure.retryable is True
    assert failure.recoverable is True
    assert failure.failure_class == "provider_timeout"
    assert failure.retry_scope == "agent_activation"


def test_wrapped_empty_model_response_remains_retryable() -> None:
    failure = classify_step_failure(
        RuntimeError(
            "Activation pool failed: Agent actor-a activation failed: "
            "empty_model_response"
        ),
        failed_step=5,
        last_complete_step=4,
    )

    assert failure.retryable is True
    assert failure.recoverable is True
    assert failure.failure_class == "provider_empty_response"
    assert failure.retry_scope == "agent_activation"


def test_tool_schema_error_stays_with_the_agent_activation() -> None:
    failure = classify_step_failure(
        ValueError("Tool schema error for query_inventory: additional properties"),
        failed_step=5,
        last_complete_step=4,
    )

    assert failure.retryable is False
    assert failure.recoverable is True
    assert failure.failure_class == "tool_schema_error"
    assert failure.retry_scope == "agent_activation"


def test_world_writer_failure_remains_a_step_recovery_boundary() -> None:
    failure = classify_step_failure(
        RuntimeError("world writer failed to persist checkpoint"),
        failed_step=5,
        last_complete_step=4,
    )

    assert failure.retryable is False
    assert failure.failure_class == "world_writer_error"
    assert failure.retry_scope == "step"


def test_world_writer_schema_diagnostic_is_not_downgraded_to_tool_schema() -> None:
    failure = classify_step_failure(
        RuntimeError("world writer schema mismatch while persisting checkpoint"),
        failed_step=5,
        last_complete_step=4,
    )

    assert failure.failure_class == "world_writer_error"
    assert failure.retry_scope == "step"
    assert failure.retryable is False


def test_generic_schema_message_stays_fail_closed_at_step_boundary() -> None:
    failure = classify_step_failure(
        ValueError("state schema mismatch"),
        failed_step=5,
        last_complete_step=4,
    )

    assert failure.failure_class == "unclassified"
    assert failure.retry_scope == "step"
    assert failure.retryable is False


def test_timeout_hidden_in_exception_cause_remains_retryable() -> None:
    try:
        raise RuntimeError("request timed out")
    except RuntimeError as timeout:
        wrapped = ValueError("agent activation failed")
        wrapped.__cause__ = timeout

    failure = classify_step_failure(
        wrapped,
        failed_step=5,
        last_complete_step=4,
    )

    assert failure.retryable is True


def test_step_failure_fingerprint_is_stable_for_the_same_failure() -> None:
    first = classify_step_failure(
        RuntimeError("boom"),
        failed_step=2,
        last_complete_step=1,
    )
    second = classify_step_failure(
        RuntimeError("boom"),
        failed_step=2,
        last_complete_step=1,
    )

    assert first.error_fingerprint == second.error_fingerprint


def test_resolve_last_complete_is_an_explicit_checkpoint_api(tmp_path) -> None:
    try:
        PersistenceManager.resolve_last_complete_from(tmp_path)
    except FileNotFoundError as exc:
        assert "No v4 complete checkpoints" in str(exc)
    else:
        raise AssertionError("空目录不应产生可恢复 checkpoint")


@pytest.mark.asyncio
async def test_failed_step_restores_from_last_complete_checkpoint_in_new_run(
    tmp_path,
) -> None:
    source_dir = tmp_path / "failed-run"
    source = Society0(
        save_dir=str(source_dir),
        base_config=_config(),
        checkpoint_every=1,
    )

    @source.step(name="controlled_step")
    async def controlled_step(ctx):
        ctx.runtime_scope.namespace("test")["ephemeral"] = "never-persist"
        ctx.env.state["counter"] += 1
        if ctx.step == 1:
            raise RuntimeError("deterministic failure")
        return None

    with pytest.raises(RuntimeError, match="deterministic failure"):
        await source.run(steps=2)

    record = PersistenceManager.resolve_last_complete_from(source_dir)
    assert record["step"] == 1
    assert set(record) == {
        "step",
        "checkpoint_id",
        "marker",
        "manifest",
        "marker_file",
        "manifest_file",
    }
    assert record["marker"]["checkpoint_version"] == V4CheckpointStore.VERSION
    assert record["marker"]["complete"] is True
    assert record["manifest"]["checkpoint_version"] == V4CheckpointStore.VERSION
    assert record["manifest"]["replacement_file"].startswith(
        "checkpoints/v4/replacements/"
    )
    assert record["manifest"]["new_segments"] == []
    restored = V4CheckpointStore(source_dir).restore(record["step"])
    assert restored["environment"]["state"]["counter"] == 1
    assert "never-persist" not in repr(restored)

    destination_dir = tmp_path / "recovered-run"
    destination = Society0(
        save_dir=str(destination_dir),
        base_config=_config(),
        checkpoint_every=1,
        source_run=str(source_dir),
        source_step=1,
    )

    @destination.step(name="controlled_step")
    async def recovered_step(ctx):
        assert ctx.runtime_scope.step == 1
        ctx.env.state["counter"] += 1
        return None

    restored_step = await destination.restore(source_dir, step=1)
    assert restored_step == 1
    assert destination.current_world_state is not None
    assert destination.current_world_state.get_step_runtime_scope() is None

    await destination.run(steps=1)

    recovered = PersistenceManager.resolve_last_complete_from(destination_dir)
    assert recovered["step"] == 2
    assert V4CheckpointStore(destination_dir).restore(2)["environment"]["state"]["counter"] == 2


@pytest.mark.asyncio
async def test_v4_restore_rejects_a_different_application_contract(tmp_path) -> None:
    source_dir = tmp_path / "identity-source"
    source = Society0(
        save_dir=str(source_dir),
        base_config=_config(),
        checkpoint_every=1,
        resume_contract={"experiment": "baseline"},
    )

    @source.step(name="advance")
    async def advance(ctx):
        ctx.env.state["counter"] += 1

    await source.run(steps=1)

    destination = Society0(
        save_dir=str(tmp_path / "identity-destination"),
        base_config=_config(),
        source_run=str(source_dir),
        source_step=1,
        resume_contract={"experiment": "different-policy"},
    )

    with pytest.raises(ValueError, match="resume identity does not match"):
        await destination.restore(source_dir, step=1)


@pytest.mark.asyncio
async def test_v4_fork_keeps_target_world_and_inherits_checkpoint_position(
    tmp_path,
) -> None:
    source_dir = tmp_path / "fork-source"
    source = Society0(
        save_dir=str(source_dir),
        base_config=_config(),
        checkpoint_every=1,
        resume_contract={"policy": "common-prefix"},
    )

    @source.step(name="advance")
    async def advance(ctx):
        ctx.env.state["counter"] += 1

    await source.run(steps=1)

    target_config = _config()
    target_config["environment"]["state"]["counter"] = 100
    destination_dir = tmp_path / "fork-destination"
    destination = Society0(
        save_dir=str(destination_dir),
        base_config=target_config,
        checkpoint_every=1,
        fork_run=str(source_dir),
        fork_step=1,
        resume_contract={"policy": "announced-tax"},
    )

    @destination.step(name="advance")
    async def advance_fork(ctx):
        assert ctx.step == 1
        assert ctx.env.state["counter"] == 100
        ctx.env.state["counter"] += 1

    await destination.run(steps=1)

    assert destination.forked_checkpoint is not None
    assert destination.forked_checkpoint["step"] == 1
    restored = V4CheckpointStore(destination_dir).restore(2)
    assert restored["environment"]["state"]["counter"] == 101


def test_source_and_fork_inputs_are_mutually_exclusive(tmp_path) -> None:
    with pytest.raises(ValueError, match="mutually exclusive"):
        Society0(
            save_dir=str(tmp_path / "destination"),
            base_config=_config(),
            source_run=str(tmp_path / "resume"),
            fork_run=str(tmp_path / "fork"),
        )
