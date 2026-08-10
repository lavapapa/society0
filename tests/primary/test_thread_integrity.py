import json

import pytest

from tests import read_gzip_json

from society0.agent.agent_loop import ActionSet, execute_action_loop
from society0.agent.thread_store import AgentThreadStore
from society0.resource_managers import redact_credentials
from society0 import Society0


pytestmark = pytest.mark.primary


def test_thread_open_rejects_non_integer_checkpoint_without_creating_step_dir(tmp_path):
    store = AgentThreadStore(tmp_path)
    for invalid in (True, False, 1.0, "1", -1):
        with pytest.raises(ValueError, match="checkpoint_step"):
            store.open_thread(
                agent_id="a",
                checkpoint_step=invalid,
                scope={"kind": "test"},
            )
    assert not (tmp_path / "agent_threads" / "threads" / "step_000001").exists()


def test_explicit_thread_id_cannot_be_reused_across_identity(tmp_path):
    store = AgentThreadStore(tmp_path)
    thread_id = "thr_" + "a" * 32
    store.open_thread(
        agent_id="a",
        checkpoint_step=1,
        scope={"kind": "tick", "id": "1"},
        thread_id=thread_id,
    )

    mismatches = [
        {"agent_id": "b", "checkpoint_step": 1, "scope": {"kind": "tick", "id": "1"}},
        {"agent_id": "a", "checkpoint_step": 2, "scope": {"kind": "tick", "id": "1"}},
        {"agent_id": "a", "checkpoint_step": 1, "scope": {"kind": "tick", "id": "2"}},
    ]
    for request in mismatches:
        with pytest.raises(ValueError, match="already exists|identity"):
            store.open_thread(thread_id=thread_id, **request)

    paths = list((tmp_path / "agent_threads" / "threads").glob("*/" + thread_id + ".jsonl"))
    assert [path.parent.name for path in paths] == ["step_000001"]


def test_thread_store_recovers_only_incomplete_tail_and_rejects_hash_corruption(tmp_path):
    store = AgentThreadStore(tmp_path)
    thread_id = store.open_thread(
        agent_id="a",
        checkpoint_step=1,
        scope={"kind": "test"},
    )
    store.append_event(thread_id, "conversation_message", payload={"x": 1})
    path = tmp_path / store.get_thread_reference(thread_id)["path"]
    with path.open("ab") as handle:
        handle.write(b'{"schema_version":1,"sequence":999')
    assert len(store.read_events(thread_id)) == 2
    store.append_event(thread_id, "conversation_message", payload={"x": 2})

    raw_lines = path.read_bytes().splitlines()
    event = json.loads(raw_lines[-1])
    event["payload"] = {"x": 999}
    raw_lines[-1] = json.dumps(event, ensure_ascii=False, separators=(",", ":")).encode()
    path.write_bytes(b"\n".join(raw_lines) + b"\n")
    with pytest.raises(ValueError, match="event hash mismatch"):
        store.read_events(thread_id)


@pytest.mark.asyncio
async def test_successful_action_is_not_relabelled_when_completed_event_write_fails():
    action_set = ActionSet()
    state = []

    async def act():
        state.append("committed")
        return {"ok": True}

    action_set.add_action(
        "act",
        act,
        "act",
        {"type": "object", "properties": {}},
    )

    async def llm_call(_payload):
        return {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call-1",
                    "type": "function",
                    "function": {"name": "act", "arguments": "{}"},
                }
            ],
        }

    def recorder(event_type, _payload):
        if event_type == "tool_execution_completed":
            raise OSError("thread fsync failed")

    with pytest.raises(RuntimeError, match="Agent Thread event"):
        await execute_action_loop(
            "run",
            action_set,
            "system",
            [],
            llm_call,
            max_turns=1,
            thread_event_recorder=recorder,
        )
    assert state == ["committed"]


def test_request_credentials_are_redacted_recursively():
    value = redact_credentials(
        {
            "messages": [{"role": "user", "content": "keep"}],
            "transport": {
                "headers": {
                    "Authorization": "Bearer secret",
                    "nested": {"api-key": "secret-2"},
                },
                "options": [{"password": "secret-3"}],
            },
        }
    )
    serialized = json.dumps(value, ensure_ascii=False)
    assert "secret" not in serialized
    assert value["messages"][0]["content"] == "keep"


@pytest.mark.asyncio
async def test_diagnostic_checkpoint_keeps_open_thread_reference(tmp_path, monkeypatch):
    monkeypatch.setenv("CHROMA_RUNTIME_MODE", "disk")
    engine = Society0(
        save_dir=str(tmp_path),
        base_config={
            "agent_types": [{"id": "rule", "archetype": "rule"}],
            "agents": [{"id": "a", "type": "rule", "state": {}}],
            "environment": {"type": "plain", "state": {}},
        },
    )
    thread_id = engine.log_context.open_agent_thread(
        agent_id="a",
        checkpoint_step=0,
        scope={"kind": "diagnostic"},
    )
    await engine._initialize()
    path = await engine.persistence_manager.save_diagnostic_checkpoint(
        engine.current_world_state,
    )
    payload = read_gzip_json(path)
    assert payload["agent_threads"]["threads"][thread_id]["closed"] is False
    assert payload["agent_threads"]["by_agent"] == {"a": [thread_id]}
