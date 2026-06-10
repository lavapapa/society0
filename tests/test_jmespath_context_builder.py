import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
SIMENGINE_SRC = PROJECT_ROOT / "libs" / "simengine" / "src"
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if str(SIMENGINE_SRC) not in sys.path:
    sys.path.insert(0, str(SIMENGINE_SRC))

from simengine.jmespath_context import JmespathContextBuilder


class _StubAgent:
    def __init__(self, agent_id: str, state: dict):
        self.id = agent_id
        self._state = state

    def get_raw_data(self):
        return {"state": self._state}


class _StubWorld:
    def __init__(self):
        self.step = 4
        self._agents = [
            _StubAgent("agentA", {"score": 0.8}),
            _StubAgent("agentB", {"score": 0.2}),
        ]
        self.agents_data = {agent.id: agent.get_raw_data() for agent in self._agents}
        self.environment_data = {"globals": {"tick": 1}}

    def get_all_agents(self):
        return list(self._agents)


def test_builder_records_selector_and_operator_snapshots():
    builder = JmespathContextBuilder()
    builder.reset(step_number=7)

    builder.record_node_inputs("collectNode", {"threshold": 0.5})
    builder.record_selector(
        "collectNode",
        {"type": "all_agents"},
        [_StubAgent("agentA", {"score": 0.8})],
    )

    builder.record_operator_result(
        node_id="collectNode",
        operator_id="collectSurvey",
        operator_type="instruct",
        description="collect base data",
        execution={
            "agent_id": "agentA",
            "status": "success",
            "output": {"score": 0.8},
            "result": {"score": 0.8},
            "value": {"score": 0.8},
            "structured_output": {"score": 0.8},
            "metadata": {"operator_id": "collectSurvey"},
            "execution_time": 0.12,
        },
    )

    builder.record_operator_result(
        node_id="collectNode",
        operator_id="collectSurvey",
        operator_type="instruct",
        description="collect base data",
        execution={
            "agent_id": "agentB",
            "status": "success",
            "output": {"score": 0.2},
            "result": {"score": 0.2},
            "value": {"score": 0.2},
            "structured_output": {"score": 0.2},
            "metadata": {"operator_id": "collectSurvey"},
            "execution_time": 0.10,
        },
    )

    builder.record_converter_output("collectNode", {"summary": "ok"})

    world = _StubWorld()
    root = builder.build_root(world=world, step_context={"collectNode": {"summary": "ok"}})

    assert root["step"]["number"] == 7
    selector_info = root["nodes"]["collectNode"]["selector"]
    assert selector_info["match_count"] == 1
    assert selector_info["matched_ids"] == ["agentA"]

    operator_info = root["operators"]["collectSurvey"]
    assert set(operator_info["agents"].keys()) == {"agentA", "agentB"}
    assert operator_info["output"] == {
        "agentA": {"score": 0.8},
        "agentB": {"score": 0.2},
    }
    assert operator_info["result"] == operator_info["output"]
    assert operator_info["agents"]["agentA"]["structured_output"] == {"score": 0.8}

    assert root["nodes"]["collectNode"]["converter"]["output"] == {"summary": "ok"}
    assert root["world"]["agents"]["agentA"]["state"]["score"] == 0.8
    assert root["debug"]["selectors"][0]["node_id"] == "collectNode"
