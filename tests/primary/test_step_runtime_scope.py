import pytest

from society0 import StepRuntimeScope
from society0.core_data import World


pytestmark = pytest.mark.primary


def test_step_runtime_scope_namespaces_are_isolated_and_invalidate_together() -> None:
    scope = StepRuntimeScope(3)

    fov = scope.namespace("industry_chain.fov")
    phase = scope.namespace("industry_chain.phase_indexes")
    fov["actor:a"] = {"inbox_sequence": 7}
    phase["date"] = "2026-05-07"

    assert scope.namespace("industry_chain.fov") is fov
    assert phase == {"date": "2026-05-07"}

    scope.invalidate()

    assert scope.active is False
    with pytest.raises(RuntimeError, match="已失效"):
        len(fov)
    with pytest.raises(RuntimeError, match="已失效"):
        phase["date"]
    with pytest.raises(RuntimeError, match="已失效"):
        scope.namespace("industry_chain.fov")


def test_world_step_runtime_scope_is_not_canonical_and_expires_on_advance() -> None:
    world = World(step=4)
    scope = world.begin_step_runtime_scope()
    scope.namespace("test")["secret"] = "never-persist"

    assert world.require_step_runtime_scope() is scope
    assert "never-persist" not in repr(world.agents_data)
    assert "never-persist" not in repr(world.environment_data)

    world.advance_step()

    assert world.step == 5
    assert world.get_step_runtime_scope() is None
    with pytest.raises(RuntimeError, match="已失效"):
        scope.namespace("test")


def test_world_rejects_two_active_scopes_for_the_same_step() -> None:
    world = World(step=0)
    world.begin_step_runtime_scope()

    with pytest.raises(RuntimeError, match="已经存在"):
        world.begin_step_runtime_scope()

    world.invalidate_step_runtime_scope()
    replacement = world.begin_step_runtime_scope()
    assert replacement.step == 0


def test_two_worlds_never_share_step_runtime_namespaces() -> None:
    left = World(step=2)
    right = World(step=2)
    left_scope = left.begin_step_runtime_scope()
    right_scope = right.begin_step_runtime_scope()

    left_scope.namespace("same-owner")["value"] = "left"
    right_scope.namespace("same-owner")["value"] = "right"

    assert left_scope.namespace("same-owner")["value"] == "left"
    assert right_scope.namespace("same-owner")["value"] == "right"
    left.invalidate_step_runtime_scope()
    assert right_scope.namespace("same-owner")["value"] == "right"
