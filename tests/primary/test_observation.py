"""外部读取保持独立，固定版本与生产者进度分别报告。"""
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

from society0.observation import ObservationReader, write_runtime_status


def test_status_and_watch_do_not_require_running_engine(tmp_path):
    write_runtime_status(tmp_path, run_id="run-a", phase="calculating", executing_step=3)
    with ObservationReader(tmp_path, index_dir=tmp_path / "index") as reader:
        status = reader.status()
        assert status["phase"] == "calculating"
        assert status["committed_checkpoint"] is None
        assert status["indexed_checkpoint"] is None
        page = reader.watch()
        assert page["items"]
        assert reader.watch(cursor=page["next_cursor"])["items"] == []
        write_runtime_status(tmp_path, run_id="run-a", phase="failed", executing_step=3)
        assert reader.watch(cursor=page["next_cursor"])["items"][0]["phase"] == "failed"


def test_independent_query_while_producer_blocks(tmp_path):
    producer = subprocess.Popen([sys.executable, "-c", """
import sys,time
from society0.observation import write_runtime_status
write_runtime_status(sys.argv[1],run_id='blocked',phase='calculating',executing_step=1)
time.sleep(15)
""", str(tmp_path)])
    try:
        deadline = time.monotonic() + 10
        while not (tmp_path / "runtime-status.json").exists():
            assert time.monotonic() < deadline
            time.sleep(.02)
        response = subprocess.run([sys.executable, "-m", "society0.observation", str(tmp_path),
                                   "--index-dir", str(tmp_path / "index"), "--method", "status"],
                                  capture_output=True, text=True, timeout=10, check=True)
        status = json.loads(response.stdout)
        assert producer.poll() is None
        assert status["phase"] == "calculating"
        assert status["status_age_seconds"] >= 0
    finally:
        producer.terminate()
        producer.wait(timeout=5)


def test_query_refuses_other_run_cursor(tmp_path):
    write_runtime_status(tmp_path, run_id="first", phase="running")
    with ObservationReader(tmp_path, index_dir=tmp_path / "index") as reader:
        cursor = reader.watch()["next_cursor"]
        other = tmp_path / "other"
        other.mkdir()
        write_runtime_status(other, run_id="second", phase="running")
        with ObservationReader(other) as second:
            with pytest.raises(ValueError, match="cursor_mismatch"):
                second.watch(cursor=cursor)


def test_committed_versions_pagination_and_large_content(tmp_path, monkeypatch):
    from society0.incremental_checkpoint import V4CheckpointStore, SealedTickDelta
    store = V4CheckpointStore(tmp_path)
    first = store.publish(SealedTickDelta(1, (
        {"sequence": 0, "operation": "set", "path": ["balances", "a"], "value": 5},
        {"sequence": 1, "operation": "set", "path": ["balances", "b"], "value": "中" * 10000},
    ), ()))
    monkeypatch.setattr(store, "restore", lambda *a: pytest.fail("query restored World"))
    with ObservationReader(tmp_path, index_dir=tmp_path / "index") as reader:
        with pytest.raises(ValueError, match="index_pending"):
            reader.state_page(first["checkpoint_id"])
        reader.sync()
        page = reader.state_page(first["checkpoint_id"], ["balances"], limit=1)
        assert page["total"] == 2
        second = store.publish(SealedTickDelta(2, (
            {"sequence": 0, "operation": "set", "path": ["balances", "a"], "value": 9},
            {"sequence": 1, "operation": "delete", "path": ["balances", "b"]},
        ), ()))
        assert reader.status()["committed_checkpoint"]["checkpoint_id"] == second["checkpoint_id"]
        assert reader.status()["indexed_checkpoint"]["checkpoint_id"] == first["checkpoint_id"]
        reader.sync()
        old = reader.state_page(first["checkpoint_id"], ["balances"], cursor=page["next_cursor"])
        assert old["items"][0]["path"] == ["balances", "b"]
        ref = old["items"][0]["content_ref"]
        data, offset = [], 0
        while True:
            result = reader.read_content(ref, offset=offset, max_bytes=997)
            data.append(result["data"])
            if result["next_offset"] is None:
                break
            offset = result["next_offset"]
        assert json.loads(b"".join(data))["value"] == "中" * 10000
        assert reader.state_page(second["checkpoint_id"], ["balances"])["total"] == 1
        with pytest.raises(ValueError, match="cursor_mismatch"):
            reader.state_page(second["checkpoint_id"], ["balances"], cursor=page["next_cursor"])


def test_insertion_order_empty_containers_and_delete_recreate(tmp_path):
    from society0.incremental_checkpoint import V4CheckpointStore, SealedTickDelta
    store = V4CheckpointStore(tmp_path)
    operations = [
        {"path": ["rows"], "operation": "set", "value": []},
        *({"path": ["rows"], "operation": "append", "value": i} for i in range(12)),
        {"path": ["map"], "operation": "set", "value": {}},
        {"path": ["map"], "operation": "map_create", "id": "z", "value": 1},
        {"path": ["map"], "operation": "map_create", "id": "a", "value": 2},
    ]
    for i, op in enumerate(operations):
        op["sequence"] = i
    first = store.publish(SealedTickDelta(1, tuple(operations), ()))
    with ObservationReader(tmp_path) as reader:
        reader.sync()
        rows = reader.state_page(first["checkpoint_id"], ["rows"])["items"]
        assert [row["path"][-1] for row in rows[1:]] == list(range(12))
        assert [row["value"] for row in rows[1:]] == list(range(12))
        changes = (
            {"sequence": 0, "path": ["map", "z"], "operation": "delete"},
            {"sequence": 1, "path": ["map", "z"], "operation": "set", "value": 3},
        )
        second = store.publish(SealedTickDelta(2, changes, ()))
        reader.sync()
        assert [row["path"][-1] for row in reader.state_page(second["checkpoint_id"], ["map"])["items"][1:]] == ["a", "z"]


def test_atomic_entry_and_invalid_nested_operation(tmp_path):
    from society0.incremental_checkpoint import V4CheckpointStore, SealedTickDelta
    store = V4CheckpointStore(tmp_path)
    first = store.publish(SealedTickDelta(1, ({"sequence": 0, "path": ["item"], "operation": "set", "value": {"x": 1}},), ()))
    with ObservationReader(tmp_path) as reader:
        reader.sync()
        with pytest.raises(ValueError, match="unsupported_path.*item"):
            reader.state_page(first["checkpoint_id"], ["item", "x"])
        store.publish(SealedTickDelta(2, ({"sequence": 0, "path": ["item", "x"], "operation": "set", "value": 2},), ()))
        with pytest.raises(ValueError, match="unsupported_operation"):
            reader.sync()
        assert reader.status()["indexed_checkpoint"]["checkpoint_id"] == first["checkpoint_id"]


def test_marker_controls_visibility_when_pointer_missing_or_invalid(tmp_path):
    from society0.incremental_checkpoint import V4CheckpointStore, SealedTickDelta
    store = V4CheckpointStore(tmp_path)
    marker = store.publish(SealedTickDelta(1, (), ()))
    latest = tmp_path / "checkpoints/v4/latest.json"
    latest.unlink()
    with ObservationReader(tmp_path) as reader:
        assert reader.status()["committed_checkpoint"]["checkpoint_id"] == marker["checkpoint_id"]
        latest.write_text(json.dumps({**marker, "checkpoint_id": "uncommitted", "step": 9}))
        assert reader.status()["committed_checkpoint"]["checkpoint_id"] == marker["checkpoint_id"]


def test_cross_directory_base_index_preserves_source(tmp_path):
    from society0.incremental_checkpoint import V4CheckpointStore
    source = V4CheckpointStore(tmp_path / 'source')
    first = source.publish_root(({'sequence': 0, 'path': ['a'], 'operation': 'set', 'value': 1},), metadata={'run_id': 'source'})
    target = V4CheckpointStore(tmp_path / 'target')
    second = target.publish_root(({'sequence': 0, 'path': ['b'], 'operation': 'set', 'value': 2},),
        metadata={'run_id': 'target', 'base_checkpoint': {'source_root': '../source', 'step': 0,
                  'checkpoint_id': first['checkpoint_id'], 'branch_id': 'main'}})
    with ObservationReader(tmp_path / 'target') as reader:
        reader.sync()
        rows = reader.state_page(second['checkpoint_id'])['items']
        assert [(row['path'],row['value']) for row in rows] == [(['a'],1),(['b'],2)]
        assert str(tmp_path / 'source') in rows[0]['content_ref']['source']


def test_index_process_crash_and_explicit_rebuild(tmp_path):
    from society0.incremental_checkpoint import V4CheckpointStore, SealedTickDelta
    store = V4CheckpointStore(tmp_path)
    store.publish(SealedTickDelta(1, tuple({"sequence": i, "path": [str(i)], "operation": "set", "value": i} for i in range(20)), ()))
    script = '''
import sys,time
from pathlib import Path
import society0.checkpoint_records as records
from society0.observation import ObservationReader
original = records.iter_metadata
def slow(*args, **kwargs):
    for i, row in enumerate(original(*args, **kwargs)):
        if i == 10:
            Path(sys.argv[1], 'index-paused').touch()
            time.sleep(30)
        yield row
records.iter_metadata = slow
with ObservationReader(sys.argv[1],index_dir=Path(sys.argv[1])/'index') as reader:
    reader.sync()
'''
    process = subprocess.Popen([sys.executable, "-c", script, str(tmp_path)])
    try:
        deadline = time.monotonic() + 10
        while not (tmp_path / "index-paused").exists():
            assert time.monotonic() < deadline
            time.sleep(.02)
        process.kill()
        process.wait(timeout=5)
        with ObservationReader(tmp_path, index_dir=tmp_path / "index") as reader:
            assert reader.status()["indexed_checkpoint"] is None
            assert reader.db.execute("SELECT count(*) FROM entries").fetchone()[0] == 0
            reader.sync()
            assert reader.state_page()["total"] == 20
        (tmp_path / "index/observation.sqlite").write_bytes(b"broken index")
        with ObservationReader(tmp_path, index_dir=tmp_path / "index", rebuild=True) as reader:
            reader.sync()
            assert reader.state_page()["total"] == 20
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)


def test_http_status_remains_available_during_indexing(tmp_path):
    import socket
    import urllib.request
    from society0.incremental_checkpoint import V4CheckpointStore, SealedTickDelta
    store = V4CheckpointStore(tmp_path)
    marker = store.publish(SealedTickDelta(1, tuple({"sequence": i, "path": [str(i)], "operation": "set", "value": i} for i in range(10)), ()))
    write_runtime_status(tmp_path, run_id="http", phase="calculating")
    with socket.socket() as socket_:
        socket_.bind(("127.0.0.1", 0))
        port = socket_.getsockname()[1]
    script = '''
import sys,time
import society0.checkpoint_records as records
from society0.observation import main
original = records.iter_metadata
def slow(*args, **kwargs):
    for row in original(*args, **kwargs):
        time.sleep(.15)
        yield row
records.iter_metadata = slow
main()
'''
    process = subprocess.Popen([sys.executable, "-c", script, str(tmp_path), "--index-dir", str(tmp_path / "index"), "--serve", str(port)])
    try:
        deadline = time.monotonic() + 10
        responses = []
        while time.monotonic() < deadline:
            try:
                request = urllib.request.Request(f"http://127.0.0.1:{port}", data=b'{"method":"status"}', headers={"Content-Type":"application/json"})
                before = time.monotonic()
                with urllib.request.urlopen(request, timeout=1) as response:
                    status = json.load(response)["result"]
                responses.append(time.monotonic()-before)
                assert status["committed_checkpoint"]["checkpoint_id"] == marker["checkpoint_id"]
                if status["indexed_checkpoint"]:
                    break
            except OSError:
                time.sleep(.02)
                continue
            time.sleep(.03)
        assert len(responses) >= 5
        assert status["indexed_checkpoint"]["checkpoint_id"] == marker["checkpoint_id"]
        assert max(responses) < 1
    finally:
        process.terminate()
        process.wait(timeout=5)


@pytest.mark.asyncio
async def test_engine_queries_complete_state_and_dataset_commit(tmp_path):
    from society0 import Society0, StepResult, persistent_state_schema, replaceable_map, append_only_list
    from society0.incremental_checkpoint import V4CheckpointStore
    schema = persistent_state_schema(accounts=replaceable_map(), events=append_only_list())
    config = {"agents": [], "environment": {"type": "plain", "state": {"accounts": {"b": {"x": 1}, "a": {"x": 2}}, "events": []}, "state_schema": schema}}
    engine = Society0(save_dir=str(tmp_path), base_config=config)
    @engine.step(name="change")
    async def change(ctx):
        ctx.env.state["accounts"]["b"]["x"] = 3
        del ctx.env.state["accounts"]["a"]
        ctx.env.state["accounts"]["a"] = {"x": 4}
        ctx.env.state["events"].extend(range(12))
        return StepResult(tables={"rows": ({"i": i} for i in range(3))})
    await engine.run(steps=1)
    reference = json.loads((tmp_path / "steps.jsonl").read_text())["result"]["tables"]["rows"]
    with ObservationReader(tmp_path) as reader:
        with pytest.raises(ValueError, match="dataset_not_committed"):
            reader.dataset_page(reference)
        reader.sync()
        rows = reader.state_page(path=["environment", "state", "accounts"])["items"]
        assert {row["path"][-1]: row["value"] for row in rows} == {"b": {"x":3}, "a": {"x":4}}
        events = reader.state_page(path=["environment", "state", "events"])["items"]
        assert [row["value"] for row in events[1:]] == list(range(12))
        assert reader.dataset_page(reference)["total"] == 3
        from society0.result_datasets import write_dataset
        orphan = write_dataset(tmp_path, [{"not": "committed"}], step=0, name="orphan")
        with pytest.raises(ValueError, match="dataset_not_committed"):
            reader.dataset_page(orphan)


def test_thread_live_and_committed_boundaries_are_distinct(tmp_path):
    from society0.agent.thread_store import AgentThreadStore
    from society0.incremental_checkpoint import V4CheckpointStore, SealedTickDelta
    writer = AgentThreadStore(tmp_path)
    tid = writer.open_thread(agent_id="a", checkpoint_step=1, scope={"kind":"test"})
    writer.append_event(tid, "response", payload={"x":1})
    with ObservationReader(tmp_path) as reader:
        assert reader.thread_page(tid)["durable_through"] is None
        with pytest.raises(ValueError, match="index_pending"):
            reader.thread_page(tid, checkpoint_id="uncommitted")
        writer.close_thread(tid)
        checkpoint_id = "test-thread-commit"
        manifest = writer.publish_epoch_manifest(checkpoint_id, [1])
        store = V4CheckpointStore(tmp_path)
        marker = store.publish(SealedTickDelta(1, (), ()), checkpoint_id=checkpoint_id, thread_manifest=manifest)
        reader.sync()
        page = reader.thread_page(tid, checkpoint_id=marker["checkpoint_id"], limit=1)
        assert page["durable_through"] is not None
        tail = reader.thread_page(tid, checkpoint_id=marker["checkpoint_id"], cursor=page["next_cursor"])
        assert tail["total"] == page["total"]
