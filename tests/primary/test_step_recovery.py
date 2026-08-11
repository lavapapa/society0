import errno

import pytest

from society0 import Society0
from society0.persistence import PersistenceManager
from society0.recovery import classify_step_failure
from tests import read_gzip_json


pytestmark = pytest.mark.primary


def _config() -> dict:
    return {
        "agent_types": [{"id": "worker", "archetype": "rule"}],
        "agents": [{"id": "worker-a", "type": "worker", "state": {}}],
        "environment": {"type": "plain", "state": {"counter": 0}},
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
    assert disk_full.retryable is False
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
        assert "No complete checkpoints" in str(exc)
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
    assert record["checkpoint_data"]["environment_data"]["state"]["counter"] == 1
    assert "never-persist" not in repr(record["checkpoint_data"])
    diagnostic = read_gzip_json(
        source_dir / "checkpoints" / "checkpoint_final.json.gz"
    )
    assert diagnostic["environment_data"]["state"]["counter"] == 2
    assert diagnostic["failure"]["last_complete_step"] == 1
    assert "never-persist" not in repr(diagnostic)

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
    assert recovered["checkpoint_data"]["environment_data"]["state"]["counter"] == 2
