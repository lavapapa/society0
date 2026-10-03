import importlib.util
import json
import copy
from pathlib import Path

import pytest

from society0 import EmbedModel, LLMModel


ROOT = Path(__file__).resolve().parents[2]


def load_starter():
    spec = importlib.util.spec_from_file_location(
        "society0_skill_starter", ROOT / "skill/assets/minimal_experiment.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.asyncio
async def test_starter_preflight_initializes_declared_state_without_provider_calls(tmp_path):
    starter = load_starter()
    llm = LLMModel.openai_compatible(
        model="test", base_url="http://127.0.0.1:1/v1", api_key="test", concurrency=1
    )
    embed = EmbedModel.openai_compatible(
        model="test", base_url="http://127.0.0.1:1/v1", api_key="test", dimensions=3
    )
    engine = starter.build_engine(tmp_path / "check", llm, embed)
    await engine.run(steps=0)
    summary = json.loads((tmp_path / "check/summary.json").read_text())
    assert summary["failed"] is False
    assert summary["steps_completed"] == 0
    assert engine.current_world_state.agents_data["alice"]["state"]["attention"] == "正常"
    assert engine.current_world_state.get_environment().state["detail_views"] == []
    assert not (tmp_path / "check/resource_calls.jsonl").exists()


def test_starter_surfaces_failed_agent_operations():
    from society0.schedule import AgentBatchResult, AgentCallRecord

    starter = load_starter()
    failed = AgentBatchResult([AgentCallRecord("alice", "error", error="工具调用失败")])
    with pytest.raises(RuntimeError, match="工具调用失败"):
        starter.require_success(failed, "浏览")


class FakeLLMManager:
    def __init__(self, message, mode):
        self.message, self.mode = message, mode
        self.requests = []
        self.browse_calls = 0

    def tool(self, name, arguments):
        return {"role": "assistant", "content": "", "tool_calls": [{
            "id": f"call_{len(self.requests)}", "type": "function",
            "function": {"name": name, "arguments": json.dumps(arguments, ensure_ascii=False)},
        }]}

    async def request(self, payload):
        self.requests.append(copy.deepcopy(payload))
        names = [tool["function"]["name"] for tool in payload.get("tools", [])]
        if "extract_memories" in names:
            memories = [] if self.mode == "empty" else [{
                "content": f"我读到：{self.message}", "importance": 3,
            }]
            return self.tool("extract_memories", {"memories": memories})
        if "submit_result" in names:
            return self.tool("submit_result", {"result": {"credibility": 3, "reason": "离线测试评分"}})
        self.browse_calls += 1
        if self.browse_calls == 1:
            return self.tool(next(name for name in names if "view_details" in name), {})
        return {"role": "assistant", "content": "已查看，消息尚未经官方证实。", "tool_calls": []}

    async def close(self):
        pass


class FakeEmbeddingManager:
    def __init__(self, mode):
        self.mode = mode
        self.injected_failure = False

    async def request(self, texts, dimensions=None, metadata=None):
        if self.mode == "retrieval_error" and (metadata or {}).get("step") == 1:
            self.injected_failure = True
            raise RuntimeError("离线注入：测量阶段记忆检索失败")
        return {"result": [[0.1, 0.2, 0.3] for _ in texts]}

    async def close(self):
        pass


def install_fake_models(monkeypatch, starter, mode):
    llm_manager = FakeLLMManager(starter.CONFIG["environment"]["state"]["message"], mode)
    embed_manager = FakeEmbeddingManager(mode)
    monkeypatch.setattr(LLMModel, "build_manager", lambda self, **kwargs: llm_manager)
    monkeypatch.setattr(EmbedModel, "build_manager", lambda self, **kwargs: embed_manager)
    llm = LLMModel.openai_compatible(
        model="fake", base_url="http://127.0.0.1:1/v1", api_key="test", concurrency=1,
    )
    embed = EmbedModel.openai_compatible(
        model="fake", base_url="http://127.0.0.1:1/v1", api_key="test", dimensions=3, concurrency=1,
    )
    return llm, embed, llm_manager, embed_manager


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["empty", "retrieval_error"])
async def test_measurement_sees_current_message_without_recall(tmp_path, monkeypatch, mode):
    starter = load_starter()
    llm, embed, fake_llm, fake_embed = install_fake_models(monkeypatch, starter, mode)
    engine = starter.build_engine(tmp_path / "pilot", llm, embed)
    await engine.run(steps=2)
    request = next(payload for payload in fake_llm.requests if any(
        tool["function"]["name"] == "submit_result" for tool in payload.get("tools", [])
    ))
    user_prompt = "\n".join(message.get("content", "") for message in request["messages"]
                            if message["role"] == "user")
    assert "[相关记忆]" not in user_prompt
    assert "[视野信息]" in user_prompt
    assert starter.CONFIG["environment"]["state"]["message"] in user_prompt
    if mode == "retrieval_error":
        assert fake_embed.injected_failure
    summary = json.loads((tmp_path / "pilot/summary.json").read_text())
    assert summary["failed"] is False
    assert summary["steps_completed"] == 2


@pytest.mark.asyncio
async def test_new_engine_does_not_inherit_previous_run_state(tmp_path, monkeypatch):
    starter = load_starter()
    original_config = copy.deepcopy(starter.CONFIG)
    llm, embed, _, _ = install_fake_models(monkeypatch, starter, "normal")
    first = starter.build_engine(tmp_path / "first", llm, embed)
    await first.run(steps=2)
    assert list(first.current_world_state.get_environment().state["detail_views"]) == ["alice"]
    second = starter.build_engine(tmp_path / "second", llm, embed)
    await second.run(steps=0)
    assert list(second.current_world_state.get_environment().state["detail_views"]) == []
    assert starter.CONFIG == original_config
