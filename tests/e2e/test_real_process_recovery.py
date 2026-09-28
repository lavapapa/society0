"""真实服务下跨进程退出、检查点恢复和记忆召回。"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

import pytest

from society0 import Society0
from society0.incremental_checkpoint import V4CheckpointStore
from society0.observation import ObservationReader
from tests.e2e.test_society0_real_e2e import MemoryCheck, _build_models, _llm_agent_config

pytestmark = [pytest.mark.e2e, pytest.mark.real_e2e, pytest.mark.skipif(
    os.getenv("SOCIETY0_RUN_REAL_E2E") != "1", reason="Real endpoint opt-in required")]


async def _worker(root: Path, mode: str) -> None:
    llm, embed = _build_models()
    source = root / "source"
    target = source if mode == "seed" else root / "resumed"
    engine = Society0(save_dir=str(target), base_config=_llm_agent_config(), llm=llm,
                      embed=embed, checkpoint_every=1,
                      source_run=source if mode == "resume" else None,
                      source_step=1 if mode == "resume" else None)

    @engine.step(name="durable_memory")
    async def durable_memory(ctx):
        if mode == "seed" and ctx.world.step == 1:
            ctx.env.state["memory_protocol_phase"] = "uncommitted"
            raise RuntimeError("injected_after_committed_step")
        group = ctx.agents.all()
        if mode == "seed":
            tids = {aid: ctx.log.open_agent_thread(agent_id=aid, checkpoint_step=1,
                    scope={"kind": "process_recovery"}) for aid in group.agent_ids}
            result = await group.instruct(
                "Remember this durable private access signal exactly: cobalt moon. "
                "Return remembered=true and answer='cobalt moon'.", output=MemoryCheck,
                thread_ids_by_agent=tids, retrieve_memory=True, concurrency=1, max_turns=3)
            assert result.error_count == 0
            memories = await group.extract_thread_memories(tids, timestamp=0,
                idempotency_key="real_process_recovery_seed", concurrency=1)
            assert memories.error_count == 0
            (root / "thread-id.json").write_text(json.dumps(tids["alice"]))
            ctx.env.state["memory_protocol_phase"] = "committed"
        else:
            assert ctx.env.state["memory_protocol_phase"] == "committed"
            result = await group.interview(
                "From your persistent memory, what private access signal were you given? "
                "Return remembered=true only if you recall it, and return the exact signal.",
                output=MemoryCheck, retrieve_memory=True, memory_top_k=1,
                concurrency=1, max_turns=3)
            assert result.error_count == 0
            assert result.values("remembered") == [True]
            assert "cobalt moon" in str(result.values("answer")).lower()
            ctx.env.state["memory_protocol_phase"] = "resumed"
        return ctx.result(tables={"answers": result.table()})

    if mode == "seed":
        try:
            await engine.run(steps=2)
        except RuntimeError as exc:
            assert str(exc) == "injected_after_committed_step"
        else:
            raise AssertionError("failure injection did not execute")
    else:
        await engine.run(steps=1)


def test_real_exit_restore_memory_and_observation(tmp_path):
    original_events = None
    for mode in ("seed", "resume"):
        # 子进程完全退出后才启动下一个，避免复用 Chroma 客户端或内存状态。
        result = subprocess.run([sys.executable, "-c",
            "import asyncio,sys; from pathlib import Path; "
            "from tests.e2e.test_real_process_recovery import _worker; "
            "asyncio.run(_worker(Path(sys.argv[1]),sys.argv[2]))", str(tmp_path), mode],
            capture_output=True, text=True, timeout=600)
        assert result.returncode == 0, result.stdout + result.stderr
        run = tmp_path / ("source" if mode == "seed" else "resumed")
        expected_step = 1 if mode == "seed" else 2
        store = V4CheckpointStore(run)
        resolved = store.resolve()
        assert resolved["step"] == expected_step
        restored = store.restore(expected_step)
        assert restored["environment"]["state"]["memory_protocol_phase"] == (
            "committed" if mode == "seed" else "resumed")
        with tempfile.TemporaryDirectory(prefix="society0-real-index-", dir="/tmp") as local_index:
            reader = ObservationReader(run, index_dir=local_index)
            reader.sync()
            assert reader.status()["committed_checkpoint"]["step"] == expected_step
            thread_id = json.loads((tmp_path / "thread-id.json").read_text())
            page = reader.thread_page(thread_id, checkpoint_id=resolved["checkpoint_id"])
            assert page["total"] > 0
            assert page["durable_through"] is not None
            events = list(page["events"])
            while page["next_cursor"] is not None:
                page = reader.thread_page(thread_id, cursor=page["next_cursor"],
                                          checkpoint_id=resolved["checkpoint_id"])
                events.extend(page["events"])
            if original_events is None:
                original_events = events
            else:
                assert events == original_events
            reader.close()
        assert (run / "chroma_store" / "chroma.sqlite3").exists()
    events = [json.loads(line) for line in (tmp_path / "resumed" / "resource_calls.jsonl").read_text().splitlines()]
    assert any(item.get("resource_type") == "embedding" for item in events)
    assert any(item.get("resource_type") == "llm" for item in events)
