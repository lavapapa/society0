import json
from pathlib import Path

from simengine.logging.context import ExperimentLogContext


def _read_jsonl(path: Path):
    if not path.exists():
        return []
    rows = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    return rows


def test_resource_call_trace_file_is_written_under_results_root(tmp_path):
    logs_dir = tmp_path / "results" / "logs"
    ctx = ExperimentLogContext(logs_dir, experiment_id="exp_test")

    ctx.log_resource(
        "embedding",
        "INFO",
        "embedding_request_started",
        request_id="emb_1",
        endpoint_id="embedding_66",
        model="nomic-embed-text",
        texts_count=2,
        dimensions=512,
        input_characters=123,
    )
    ctx.log_resource(
        "embedding",
        "INFO",
        "embedding_request_completed",
        request_id="emb_1",
        endpoint_id="embedding_66",
        model="nomic-embed-text",
        texts_count=2,
        dimensions=512,
        input_characters=123,
        duration_sec=0.42,
        vectors_returned=2,
        retry_count=0,
    )

    ctx.log_resource(
        "llm",
        "INFO",
        "llm_request_started",
        request_id="llm_1",
        endpoint_id="qwen_router",
        model="qwen3-17b",
        agent_id="A0001",
        messages_count=4,
        input_characters=2048,
    )
    ctx.log_resource(
        "llm",
        "ERROR",
        "llm_request_failed",
        request_id="llm_1",
        endpoint_id="qwen_router",
        model="qwen3-17b",
        duration_sec=1.23,
        retry_count=2,
        error_type="TimeoutError",
        error="x" * 400,
    )
    ctx.close()

    trace_file = tmp_path / "results" / "resource_calls.jsonl"
    assert trace_file.exists()

    rows = _read_jsonl(trace_file)
    assert len(rows) == 2

    emb_row = next(row for row in rows if row["request_id"] == "emb_1")
    assert emb_row["resource_type"] == "embedding"
    assert emb_row["status"] == "success"
    assert emb_row["texts_count"] == 2
    assert emb_row["dimensions"] == 512
    assert emb_row["vectors_returned"] == 2
    assert "started_at" in emb_row
    assert "completed_at" in emb_row

    llm_row = next(row for row in rows if row["request_id"] == "llm_1")
    assert llm_row["resource_type"] == "llm"
    assert llm_row["status"] == "failed"
    assert llm_row["messages_count"] == 4
    assert llm_row["input_characters"] == 2048
    assert llm_row["error_type"] == "TimeoutError"
    assert llm_row["error_preview"].endswith("...")
    assert len(llm_row["error_preview"]) <= 243
