import inspect
import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from simengine.function_registry import FunctionRegistry
from simengine.schedule import StepFlow
from simengine.core_data import ExecutionContext


@pytest.mark.asyncio
async def test_rule_operator_allows_var_keyword_parameters():
    """验证带有 **kwargs 的规则不会被要求提供额外参数。"""

    registry = FunctionRegistry()

    async def sample_rule(env, **kwargs):
        sample_rule.called = True
        sample_rule.received_kwargs = kwargs
        return {"status": "ok"}

    registry.rules["test.rule"] = {
        "function": sample_rule,
        "description": "demo",
        "signature": inspect.signature(sample_rule),
        "meta": None,
        "display_name": "sample_rule",
        "module": "tests",
        "func_name": "sample_rule",
        "canonical_id": "test.rule",
    }

    step_flow = StepFlow.__new__(StepFlow)
    step_flow.registry = registry

    rule_operator = step_flow._create_rule_operator("test.rule")

    class DummyWorld:
        def __init__(self):
            self.step = 0
            self.agents_data = {}

        def get_environment(self):
            return SimpleNamespace()

    context = ExecutionContext(
        world=DummyWorld(),
        step=None,
        node=None,
        caller="unit_test",
        event_logger=None,
    )

    result = await rule_operator([], {}, context)

    assert result.status == "success"
    assert getattr(sample_rule, "called", False) is True
    assert getattr(sample_rule, "received_kwargs", None) == {}
