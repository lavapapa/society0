"""Real LLM + embedding endpoint E2E tests.

These tests are opt-in so normal local/CI runs do not depend on private
infrastructure. They prefer provider-neutral environment variables and keep a
local platform-root fallback for maintainers.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import asyncio
import pytest
from pydantic import BaseModel, Field

from society0 import EmbedModel, LLMModel, Society0
from tests.e2e.real_endpoint_config import EndpointConfigError, load_endpoint_env
from tests import read_gzip_json


pytestmark = pytest.mark.skipif(
    os.getenv("SOCIETY0_RUN_REAL_E2E") != "1",
    reason="Set SOCIETY0_RUN_REAL_E2E=1 to run real LLM/embedding endpoint e2e tests.",
)
pytestmark = [pytest.mark.e2e, pytest.mark.real_e2e, pytestmark]


class TrustSurvey(BaseModel):
    trust_score: int = Field(ge=1, le=5)
    reason: str = Field(min_length=1)


class MemoryCheck(BaseModel):
    remembered: bool
    answer: str = Field(min_length=1)


class SaturationAnswer(BaseModel):
    ok: bool
    answer: str = Field(min_length=1)


def _load_default_endpoint_env() -> tuple[dict[str, str], dict[str, str]]:
    try:
        return load_endpoint_env()
    except EndpointConfigError as exc:
        pytest.skip(str(exc))


def _build_models(
    *,
    llm_concurrency: int = 1,
    embed_concurrency: int = 1,
) -> tuple[LLMModel, EmbedModel]:
    llm_env, embedding_env = _load_default_endpoint_env()
    endpoints_json = (embedding_env.get("EMBEDDING_ENDPOINTS_JSON") or "").strip()
    embedding_endpoint = _first_embedding_endpoint(endpoints_json) if endpoints_json else {}
    provider_type = str(
        embedding_endpoint.get("provider_type")
        or embedding_env.get("EMBEDDING_PROVIDER_TYPE")
        or "ollama"
    ).lower()
    embedding_base_url = embedding_endpoint.get("base_url") or embedding_env["EMBEDDING_BASE_URL"]
    embedding_model = embedding_endpoint.get("model") or embedding_env["EMBEDDING_MODEL"]
    embedding_api_key = (
        embedding_endpoint.get("api_key")
        or embedding_env.get("EMBEDDING_API_KEY")
        or ("ollama" if provider_type == "ollama" else None)
    )
    embedding_dimensions = _safe_int(
        os.getenv("SOCIETY0_REAL_E2E_EMBED_DIMENSIONS")
        or embedding_endpoint.get("dimensions")
        or embedding_env.get("EMBEDDING_DIMENSIONS"),
        default=768,
    )

    llm = LLMModel.openai_compatible(
        id="default",
        model=llm_env["LLM_MODEL"],
        base_url=llm_env["LLM_BASE_URL"],
        api_key=llm_env.get("LLM_API_KEY"),
        concurrency=llm_concurrency,
        timeout=min(float(llm_env.get("LLM_TIMEOUT") or 180), 180.0),
    )
    if provider_type == "ollama":
        embed = EmbedModel.ollama(
            id="default_embed",
            model=embedding_model,
            base_url=embedding_base_url,
            dimensions=embedding_dimensions,
            concurrency=embed_concurrency,
            timeout=180.0,
        )
    else:
        embed = EmbedModel.openai_compatible(
            id="default_embed",
            model=embedding_model,
            base_url=embedding_base_url,
            api_key=embedding_api_key,
            dimensions=embedding_dimensions,
            concurrency=embed_concurrency,
            timeout=180.0,
        )
    return llm, embed


def _saturation_concurrency() -> int:
    raw = os.getenv("SOCIETY0_REAL_E2E_SATURATION_CONCURRENCY")
    if raw:
        value = _safe_int(raw, default=6)
    else:
        # Keep the default strict enough to catch fan-out regressions while
        # avoiding accidental 1000-request storms from platform defaults.
        value = 6
    return max(2, value)


def _first_embedding_endpoint(endpoints_json: str) -> dict:
    try:
        parsed = json.loads(endpoints_json)
    except json.JSONDecodeError as exc:
        pytest.skip(f"Invalid EMBEDDING_ENDPOINTS_JSON: {exc}")
    if not isinstance(parsed, list) or not parsed:
        return {}
    first = parsed[0]
    if not isinstance(first, dict):
        pytest.skip("First EMBEDDING_ENDPOINTS_JSON item is not an object")
    return first


def _safe_int(value: object, *, default: int) -> int:
    try:
        parsed = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _llm_agent_config() -> dict:
    return {
        "agent_types": [{"id": "participant", "archetype": "llm"}],
        "agents": [
            {
                "id": "alice",
                "type": "participant",
                "persona": "A careful research participant. Answer concise structured surveys.",
                "state": {"attention": "high"},
            }
        ],
        "environment": {
            "type": "plain",
            "state": {"claim": "A new city policy will reduce commuting time."},
        },
    }


def _saturation_agent_config(agent_count: int) -> dict:
    return {
        "agent_types": [{"id": "participant", "archetype": "llm"}],
        "agents": [
            {
                "id": f"participant_{idx}",
                "type": "participant",
                "persona": (
                    "A concise research participant in a simulation test. "
                    "Follow structured output requirements exactly."
                ),
                "state": {"attention": "high", "cohort": "saturation"},
            }
            for idx in range(agent_count)
        ],
        "environment": {
            "type": "plain",
            "state": {"scenario": "endpoint saturation e2e"},
        },
    }


def _social_publish_agent_config(agent_count: int) -> dict:
    return {
        "agent_types": [{"id": "social_user", "archetype": "llm"}],
        "agents": [
            {
                "id": f"user_{idx}",
                "type": "social_user",
                "persona": (
                    "A concise social media user in a simulation test. "
                    "When asked to publish, call publish_post once before finishing the round."
                ),
                "state": {"interest": "campus life"},
            }
            for idx in range(agent_count)
        ],
        "environment": {
            "type": "social_network",
            "config": {"social_media": {"content_length_limit": 280}},
            "state": {},
        },
    }


def _round_robin_llm_agent_config(agent_count: int = 2) -> dict:
    return {
        "agent_types": [{"id": "participant", "archetype": "llm"}],
        "agents": [
            {
                "id": f"participant_{idx}",
                "type": "participant",
                "persona": (
                    "A concise participant in a paired conversation simulation. "
                    "When paired, send exactly one short message to your current partner."
                ),
                "state": {"cohort": "round-robin-real-e2e"},
            }
            for idx in range(agent_count)
        ],
        "environment": {
            "type": "round_robin_conversation",
            "config": {"group_size": agent_count, "session_duration_minutes": 5},
            "state": {},
        },
    }


def _read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _successful_resource_calls(records: list[dict], resource_type: str) -> list[dict]:
    return [
        record
        for record in records
        if record.get("resource_type") == resource_type and record.get("status") == "success"
    ]


def _assert_resource_timing(records: list[dict]) -> None:
    assert records
    for record in records:
        assert isinstance(record.get("duration_sec"), (int, float))
        assert isinstance(record.get("queue_duration_sec"), (int, float))
        assert isinstance(record.get("provider_duration_sec"), (int, float))
        assert record["duration_sec"] >= record["provider_duration_sec"] >= 0
        assert record["queue_duration_sec"] >= 0


def _assert_timing_breakdown(bucket: dict) -> None:
    timing = bucket.get("timing_breakdown")
    assert isinstance(timing, dict)
    assert timing["bottleneck"] in {"provider", "queue", "runtime_overhead", "none"}
    for key in (
        "provider_duration_sec",
        "queue_duration_sec",
        "runtime_overhead_sec",
        "provider_share",
        "queue_share",
        "runtime_overhead_share",
    ):
        assert isinstance(timing.get(key), (int, float))
        assert timing[key] >= 0


def _resource_events(run_dir: Path, resource: str) -> list[dict]:
    return _read_jsonl(run_dir / "logs" / "resources" / f"{resource}.jsonl")


def _agent_events(run_dir: Path, agent_id: str) -> list[dict]:
    return _read_jsonl(run_dir / "logs" / "agents" / f"{agent_id}.jsonl")


def _count_events(run_dir: Path, resource: str, event_name: str) -> int:
    return sum(1 for event in _resource_events(run_dir, resource) if event.get("event") == event_name)


def _trace_values(record: dict, singular_key: str, plural_key: str) -> set:
    values = set()
    singular = record.get(singular_key)
    if singular is not None:
        values.add(singular)
    plural = record.get(plural_key)
    if isinstance(plural, list):
        values.update(item for item in plural if item is not None)
    return values


@pytest.mark.asyncio
async def test_real_endpoint_smoke_llm_and_embedding():
    llm_model, embed_model = _build_models()
    llm_manager = llm_model.build_manager()
    embed_manager = embed_model.build_manager()

    try:
        embedding = await embed_manager.request(["society0 real embedding endpoint smoke"])
        vectors = embedding.get("result") or []
        assert len(vectors) == 1
        assert len(vectors[0]) > 0

        response = await llm_manager.request(
            {
                "messages": [{"role": "user", "content": "Reply with exactly one short word: OK"}],
                "temperature": 0,
                "max_tokens": 16,
            }
        )
        assert response.get("role") == "assistant"
        assert isinstance(response.get("content"), str)
        assert response["content"].strip()
    finally:
        await embed_manager.close()
        await llm_manager.close()


@pytest.mark.saturation
@pytest.mark.asyncio
async def test_real_endpoint_saturation_llm_and_embedding_managers():
    concurrency = _saturation_concurrency()
    llm_model, embed_model = _build_models(
        llm_concurrency=concurrency,
        embed_concurrency=concurrency,
    )
    llm_manager = llm_model.build_manager()
    embed_manager = embed_model.build_manager()

    async def ask_llm(idx: int):
        response = await llm_manager.request(
            {
                "messages": [
                    {
                        "role": "user",
                        "content": f"Return exactly this token and nothing else: SAT-{idx}",
                    }
                ],
                "temperature": 0,
                "max_tokens": 24,
            }
        )
        content = str(response.get("content") or "").strip()
        assert content
        return content

    async def embed_batch(idx: int):
        result = await embed_manager.request(
            [
                f"society0 saturation embedding batch {idx} text {item}"
                for item in range(4)
            ]
        )
        vectors = result.get("result") or []
        assert len(vectors) == 4
        assert all(len(vector) > 0 for vector in vectors)
        return vectors

    try:
        started = time.perf_counter()
        llm_results, embedding_results = await asyncio.gather(
            asyncio.gather(*(ask_llm(idx) for idx in range(concurrency))),
            asyncio.gather(*(embed_batch(idx) for idx in range(concurrency))),
        )
        duration = time.perf_counter() - started
    finally:
        await embed_manager.close()
        await llm_manager.close()

    assert len(llm_results) == concurrency
    assert len(embedding_results) == concurrency
    # This is not a benchmark assertion. It guards against accidental sequential
    # execution with very slow real endpoints while leaving enough room for load.
    assert duration < float(os.getenv("SOCIETY0_REAL_E2E_SATURATION_TIMEOUT_SEC", "240"))


@pytest.mark.asyncio
async def test_real_society0_interview_e2e_writes_artifacts(tmp_path):
    llm_model, embed_model = _build_models()
    engine = Society0(save_dir=str(tmp_path), base_config=_llm_agent_config(), llm=llm_model, embed=embed_model)

    @engine.step(name="credibility_survey")
    async def credibility_survey(ctx):
        survey = await ctx.agents.all().interview(
            "Rate this claim's credibility from 1 to 5 and explain briefly: "
            "A new city policy will reduce commuting time.",
            output=TrustSurvey,
            retrieve_memory=True,
            concurrency=1,
            max_turns=3,
        )
        return ctx.result(
            metrics={"errors": survey.error_count, "avg_trust": survey.mean("trust_score") or 0},
            tables={"survey": survey.table()},
        )

    await engine.run(steps=1)

    metrics = _read_jsonl(tmp_path / "metrics.jsonl")
    assert metrics[0]["metrics"]["errors"] == 0
    assert 1 <= metrics[0]["metrics"]["avg_trust"] <= 5
    assert (tmp_path / "steps.jsonl").is_file()
    assert (tmp_path / "summary.json").is_file()
    assert (tmp_path / "chroma_store" / "chroma.sqlite3").exists()
    assert any(event["event"] == "llm_request_completed" for event in _resource_events(tmp_path, "llm"))
    resource_calls = _read_jsonl(tmp_path / "resource_calls.jsonl")
    _assert_resource_timing(_successful_resource_calls(resource_calls, "llm"))
    assert _successful_resource_calls(resource_calls, "embedding") == []
    assert not (tmp_path / "logs" / "resources" / "embedding.jsonl").exists()


@pytest.mark.saturation
@pytest.mark.asyncio
async def test_real_society0_saturation_default_model_concurrency_memory_and_logs(tmp_path):
    concurrency = _saturation_concurrency()
    llm_model, embed_model = _build_models(
        llm_concurrency=concurrency,
        embed_concurrency=concurrency,
    )
    engine = Society0(
        save_dir=str(tmp_path),
        base_config=_saturation_agent_config(concurrency),
        llm=llm_model,
        embed=embed_model,
    )

    @engine.step(name="seed_memory_under_load")
    async def seed_memory_under_load(ctx):
        original = ctx.world.instruct_agent
        in_flight = 0
        max_in_flight = 0

        async def counted_instruct(agent_id, instruction, **kwargs):
            nonlocal in_flight, max_in_flight
            in_flight += 1
            max_in_flight = max(max_in_flight, in_flight)
            try:
                return await original(agent_id, instruction, **kwargs)
            finally:
                in_flight -= 1

        ctx.world.instruct_agent = counted_instruct
        group = ctx.agents.all()
        thread_ids = {
            agent_id: ctx.log.open_agent_thread(
                agent_id=agent_id,
                checkpoint_step=ctx.world.step + 1,
                scope={"kind": "real_e2e", "tick": ctx.world.step},
            )
            for agent_id in group.agent_ids
        }
        seeded = await group.instruct(
            "Remember this exact private signal for the later survey: cobalt moon. "
            "Return ok=true and answer='cobalt moon'.",
            output=SaturationAnswer,
            retrieve_memory=True,
            thread_ids_by_agent=thread_ids,
            max_turns=3,
            name="saturation_seed",
        )
        extracted = await group.extract_thread_memories(
            thread_ids,
            timestamp=ctx.world.step,
            idempotency_key=f"real_e2e:saturation:{ctx.world.step}",
            name="saturation_memory_extract",
        )
        return ctx.result(
            metrics={
                "seed_errors": seeded.error_count,
                "memory_extract_errors": extracted.error_count,
                "max_instruct_in_flight": max_in_flight,
            },
            tables={"seeded": seeded.table()},
        )

    @engine.step(name="recall_memory_under_load")
    async def recall_memory_under_load(ctx):
        original = ctx.world.interview_agent
        in_flight = 0
        max_in_flight = 0

        async def counted_interview(agent_id, question, **kwargs):
            nonlocal in_flight, max_in_flight
            in_flight += 1
            max_in_flight = max(max_in_flight, in_flight)
            try:
                return await original(agent_id, question, **kwargs)
            finally:
                in_flight -= 1

        ctx.world.interview_agent = counted_interview
        recalled = await ctx.agents.all().interview(
            "Using your memory, answer the private signal from the previous task. "
            "Return ok=true and answer exactly 'cobalt moon' if you remember it.",
            output=SaturationAnswer,
            retrieve_memory=True,
            max_turns=3,
            name="saturation_recall",
        )
        remembered_count = sum(
            1
            for answer in recalled.values("answer")
            if isinstance(answer, str) and "cobalt moon" in answer.lower()
        )
        return ctx.result(
            metrics={
                "recall_errors": recalled.error_count,
                "remembered_count": remembered_count,
                "max_interview_in_flight": max_in_flight,
            },
            tables={"recalled": recalled.table()},
        )

    await engine.run(steps=1)

    metrics = _read_jsonl(tmp_path / "metrics.jsonl")
    seed_metrics = metrics[0]["metrics"]
    recall_metrics = metrics[1]["metrics"]
    events = _read_jsonl(tmp_path / "events.jsonl")
    summary = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))

    assert events[0]["event"] == "run_started"
    assert events[0]["agent_concurrency"] == concurrency
    assert events[0]["agent_concurrency_source"] == "llm_model"
    assert summary["runtime"]["agent_concurrency"] == concurrency
    assert summary["runtime"]["agent_concurrency_source"] == "llm_model"
    assert summary["models"]["llm"]["id"] == llm_model.id
    assert summary["models"]["llm"]["model"] == llm_model.model
    assert summary["models"]["llm"]["concurrency"] == concurrency
    assert "base_url" not in summary["models"]["llm"]
    assert "api_key" not in summary["models"]["llm"]
    assert summary["models"]["embedding"]["id"] == embed_model.id
    assert summary["models"]["embedding"]["model"] == embed_model.model
    assert summary["models"]["embedding"]["concurrency"] == concurrency
    assert summary["models"]["embedding"]["dimensions"] == embed_model.dimensions
    assert "base_url" not in summary["models"]["embedding"]
    assert "api_key" not in summary["models"]["embedding"]

    assert seed_metrics["seed_errors"] == 0
    assert seed_metrics["memory_extract_errors"] == 0
    assert seed_metrics["max_instruct_in_flight"] == concurrency
    assert recall_metrics["recall_errors"] == 0
    assert recall_metrics["max_interview_in_flight"] == concurrency
    assert recall_metrics["remembered_count"] >= max(1, concurrency - 1)

    assert (tmp_path / "chroma_store" / "chroma.sqlite3").exists()
    llm_completed = _count_events(tmp_path, "llm", "llm_request_completed")
    assert llm_completed >= concurrency * 3
    assert _count_events(tmp_path, "embedding", "embedding_request_completed") >= 1
    assert summary["resources"]["llm"]["call_count"] == llm_completed
    assert summary["resources"]["llm"]["error_count"] == 0
    assert summary["resources"]["llm"]["by_interaction_type"]["instruct"]["call_count"] >= concurrency
    assert summary["resources"]["llm"]["by_interaction_type"]["interview"]["call_count"] >= concurrency
    assert summary["resources"]["llm"]["by_interaction_type"]["memory_extract"]["call_count"] >= concurrency
    assert summary["resources"]["llm"]["fidelity"]["agent_loop"]["call_count"] >= concurrency * 2
    assert summary["resources"]["llm"]["fidelity"]["memory_extraction"]["call_count"] >= concurrency
    assert summary["resources"]["embedding"]["fidelity"]["memory_io"]["call_count"] >= 1
    _assert_timing_breakdown(summary["resources"]["llm"])
    _assert_timing_breakdown(summary["resources"]["llm"]["by_interaction_type"]["instruct"])
    _assert_timing_breakdown(summary["resources"]["llm"]["fidelity"]["memory_extraction"])
    _assert_timing_breakdown(summary["resources"]["embedding"])
    _assert_timing_breakdown(summary["resources"]["embedding"]["fidelity"]["memory_io"])
    seed_batch = summary["events"]["agent_batches"]["instruct / saturation_seed"]
    recall_batch = summary["events"]["agent_batches"]["interview / saturation_recall"]
    assert seed_batch["agent_count"] == concurrency
    assert seed_batch["concurrency"] == concurrency
    assert seed_batch["concurrency_source"] == "llm_model"
    assert seed_batch["concurrency_source_counts"] == {"llm_model": 1}
    assert seed_batch["execution_options"]["memory"] == {
        "retrieve": True,
        "top_k": 10,
    }
    assert seed_batch["memory_summary"]["record_count"] == concurrency
    assert seed_batch["memory_summary"]["retrieve_enabled_count"] == concurrency
    assert seed_batch["memory_summary"]["top_k_values"] == [10]
    assert seed_batch["execution_options"]["max_turns"] == 3
    assert seed_batch["execution_options"]["output_schema"] is True
    assert seed_batch["resources"]["llm"]["by_interaction_type"]["instruct"]["call_count"] >= concurrency
    assert seed_batch["resources"]["llm"]["fidelity"]["agent_loop"]["call_count"] >= concurrency
    assert seed_batch["resources"]["llm"]["total_payload_characters"] >= (
        seed_batch["resources"]["llm"]["total_tools_characters"]
    )
    _assert_timing_breakdown(seed_batch["resources"]["llm"])
    assert recall_batch["agent_count"] == concurrency
    assert recall_batch["concurrency"] == concurrency
    assert recall_batch["concurrency_source"] == "llm_model"
    assert recall_batch["concurrency_source_counts"] == {"llm_model": 1}
    assert recall_batch["execution_options"]["memory"] == {
        "retrieve": True,
        "save": False,
        "extract": False,
        "top_k": 10,
    }
    assert recall_batch["memory_summary"]["record_count"] == concurrency
    assert recall_batch["memory_summary"]["retrieve_enabled_count"] == concurrency
    assert recall_batch["memory_summary"]["save_enabled_count"] == 0
    assert recall_batch["memory_summary"]["extraction_enabled_count"] == 0
    assert recall_batch["memory_summary"]["top_k_values"] == [10]
    assert recall_batch["resources"]["llm"]["by_interaction_type"]["interview"]["call_count"] >= concurrency
    assert recall_batch["resources"]["llm"]["fidelity"]["agent_loop"]["call_count"] >= concurrency
    _assert_timing_breakdown(recall_batch["resources"]["llm"])
    assert (
        summary["agent_operations"]["seed_memory_under_load"]["resources"]["llm"]["fidelity"][
            "memory_extraction"
        ]["call_count"]
        >= concurrency
    )
    assert (
        summary["agent_operations"]["recall_memory_under_load"]["resources"]["embedding"]["fidelity"][
            "memory_io"
        ]["call_count"]
        >= 1
    )
    resource_calls = _read_jsonl(tmp_path / "resource_calls.jsonl")
    embedding_traces = [item for item in resource_calls if item.get("resource_type") == "embedding"]
    traced_step_names = {
        step_name
        for item in embedding_traces
        for step_name in _trace_values(item, "step_name", "step_names")
    }
    traced_interaction_types = {
        interaction_type
        for item in embedding_traces
        for interaction_type in _trace_values(item, "interaction_type", "interaction_types")
    }
    assert {"seed_memory_under_load", "recall_memory_under_load"}.issubset(traced_step_names)
    assert {"memory_retrieve", "memory_write"}.issubset(traced_interaction_types)

    # Embedding calls may be cached or microbatched when agents write identical
    # memory text. Per-agent memory behavior is verified from agent logs.
    for idx in range(concurrency):
        agent_id = f"participant_{idx}"
        agent_events = _agent_events(tmp_path, agent_id)
        assert any(event.get("event") == "memory_written" for event in agent_events)
        assert any(
            event.get("event") == "memory_read" and int(event.get("memory_results_count") or 0) >= 1
            for event in agent_events
        )


@pytest.mark.asyncio
async def test_real_society0_explicit_agent_group_concurrency_overrides_model_e2e(tmp_path):
    agent_count = 2
    llm_model, embed_model = _build_models(
        llm_concurrency=agent_count,
        embed_concurrency=agent_count,
    )
    engine = Society0(
        save_dir=str(tmp_path),
        base_config=_saturation_agent_config(agent_count),
        llm=llm_model,
        embed=embed_model,
    )

    @engine.step(name="explicit_concurrency_probe")
    async def explicit_concurrency_probe(ctx):
        original = ctx.world.instruct_agent
        in_flight = 0
        max_in_flight = 0

        async def counted_instruct(agent_id, instruction, **kwargs):
            nonlocal in_flight, max_in_flight
            in_flight += 1
            max_in_flight = max(max_in_flight, in_flight)
            try:
                return await original(agent_id, instruction, **kwargs)
            finally:
                in_flight -= 1

        ctx.world.instruct_agent = counted_instruct
        result = await ctx.agents.all().instruct(
            "Remember this private signal: silver river. "
            "Return ok=true and answer='silver river'.",
            output=SaturationAnswer,
            retrieve_memory=False,
            max_turns=3,
            concurrency=1,
            name="explicit_concurrency_round",
        )
        return ctx.result(
            metrics={
                "errors": result.error_count,
                "success": result.success_count,
                "max_in_flight": max_in_flight,
            },
            tables={"answers": result.table()},
        )

    await engine.run(steps=1)

    metrics = _read_jsonl(tmp_path / "metrics.jsonl")[0]["metrics"]
    summary = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    resource_calls = _read_jsonl(tmp_path / "resource_calls.jsonl")

    assert metrics == {"errors": 0, "success": agent_count, "max_in_flight": 1}
    assert summary["runtime"]["agent_concurrency"] == agent_count
    assert summary["runtime"]["agent_concurrency_source"] == "llm_model"

    batch = summary["events"]["agent_batches"]["instruct / explicit_concurrency_round"]
    assert batch["agent_count"] == agent_count
    assert batch["concurrency"] == 1
    assert batch["concurrency_source"] == "explicit"
    assert batch["concurrency_source_counts"] == {"explicit": 1}
    assert batch["max_in_flight_count"] == 1
    assert batch["max_started_count"] == agent_count
    assert batch["success_count_total"] == agent_count
    assert batch["error_count_total"] == 0
    assert batch["execution_options"]["memory"] == {
        "retrieve": True,
        "save": True,
        "extract": True,
        "top_k": 10,
    }
    assert batch["memory_summary"]["record_count"] == agent_count
    assert batch["memory_summary"]["retrieve_enabled_count"] == agent_count
    assert batch["memory_summary"]["save_enabled_count"] == agent_count
    assert batch["memory_summary"]["extraction_enabled_count"] == agent_count
    assert batch["resources"]["llm"]["by_interaction_type"]["instruct"]["call_count"] >= agent_count
    assert summary["resources"]["llm"]["by_interaction_type"]["memory_extract"]["call_count"] >= agent_count
    assert summary["resources"]["llm"]["fidelity"]["memory_extraction"]["call_count"] >= agent_count
    assert summary["resources"]["embedding"]["fidelity"]["memory_io"]["call_count"] >= 1
    _assert_timing_breakdown(batch["resources"]["llm"])
    _assert_timing_breakdown(summary["resources"]["llm"])
    _assert_timing_breakdown(summary["resources"]["embedding"])

    llm_traces = _successful_resource_calls(resource_calls, "llm")
    assert len([item for item in llm_traces if item.get("interaction_type") == "instruct"]) >= agent_count
    assert len([item for item in llm_traces if item.get("interaction_type") == "memory_extract"]) >= agent_count
    assert _successful_resource_calls(resource_calls, "embedding")


@pytest.mark.asyncio
async def test_real_society0_memory_roundtrip_e2e(tmp_path):
    llm_model, embed_model = _build_models()
    engine = Society0(save_dir=str(tmp_path), base_config=_llm_agent_config(), llm=llm_model, embed=embed_model)

    @engine.registry.env.rule(name="set_memory_protocol")
    async def set_memory_protocol(env, phase: str):
        env.state["memory_protocol_phase"] = phase
        return {"phase": phase}

    @engine.registry.sched.behavior(name="mark_memory_participant")
    async def mark_memory_participant(agent, env, marker: str):
        agent.state["logic_marker"] = marker
        agent.state["protocol_phase_seen"] = env.state.get("memory_protocol_phase")
        return {
            "logic_marker": agent.state["logic_marker"],
            "protocol_phase_seen": agent.state["protocol_phase_seen"],
        }

    @engine.step(name="seed_and_recall")
    async def seed_and_recall(ctx):
        group = ctx.agents.all()
        protocol = await ctx.rule("set_memory_protocol", phase="seed_then_recall")
        marked = await ctx.behavior(
            "mark_memory_participant",
            agents=["alice"],
            marker="logic-before-llm",
            concurrency=1,
        )
        thread_ids = {
            agent_id: ctx.log.open_agent_thread(
                agent_id=agent_id,
                checkpoint_step=ctx.world.step + 1,
                scope={"kind": "real_e2e", "tick": ctx.world.step},
            )
            for agent_id in group.agent_ids
        }
        seeded = await group.instruct(
            "Remember this private signal for the next question: cobalt moon. "
            "Return remembered=true and answer='cobalt moon'.",
            output=MemoryCheck,
            retrieve_memory=True,
            thread_ids_by_agent=thread_ids,
            concurrency=1,
            max_turns=3,
        )
        extracted = await group.extract_thread_memories(
            thread_ids,
            timestamp=ctx.world.step,
            idempotency_key=f"real_e2e:memory_roundtrip:{ctx.world.step}",
            concurrency=1,
            name="memory_roundtrip_extract",
        )
        recalled = await group.interview(
            "Based on your memory, what private signal were you given? "
            "Return remembered=true if you can answer.",
            output=MemoryCheck,
            retrieve_memory=True,
            memory_top_k=1,
            concurrency=1,
            max_turns=3,
        )
        return ctx.result(
            metrics={
                "logic_rule_phase": protocol["phase"],
                "logic_behavior_errors": marked.error_count,
                "logic_behavior_success": marked.success_count,
                "seed_errors": seeded.error_count,
                "memory_extract_errors": extracted.error_count,
                "recall_errors": recalled.error_count,
                "remembered_count": sum(1 for value in recalled.values("remembered") if value is True),
            },
            tables={"logic": marked.table(), "seeded": seeded.table(), "recalled": recalled.table()},
        )

    await engine.run(steps=1)

    metrics = _read_jsonl(tmp_path / "metrics.jsonl")
    assert metrics[0]["metrics"]["logic_rule_phase"] == "seed_then_recall"
    assert metrics[0]["metrics"]["logic_behavior_errors"] == 0
    assert metrics[0]["metrics"]["logic_behavior_success"] == 1
    assert metrics[0]["metrics"]["seed_errors"] == 0
    assert metrics[0]["metrics"]["memory_extract_errors"] == 0
    assert metrics[0]["metrics"]["recall_errors"] == 0
    assert metrics[0]["metrics"]["remembered_count"] >= 1
    summary = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    logic_executions = summary["events"]["logic_executions"]
    assert logic_executions["rule / set_memory_protocol"]["completed_count"] == 1
    assert logic_executions["rule / set_memory_protocol"]["success_count"] == 1
    assert logic_executions["behavior / mark_memory_participant"]["completed_count"] == 1
    assert logic_executions["behavior / mark_memory_participant"]["success_count"] == 1
    assert logic_executions["behavior / mark_memory_participant"]["agent_count_total"] == 1
    assert summary["capabilities"]["by_source"]["experiment"]["rules"] >= 1
    assert summary["capabilities"]["by_source"]["experiment"]["behaviors"] >= 1
    checkpoint = read_gzip_json(
        tmp_path / "checkpoints" / "checkpoint_final.json.gz"
    )
    assert checkpoint["environment_data"]["state"]["memory_protocol_phase"] == "seed_then_recall"
    alice_state = checkpoint["agents_data"]["alice"]["state"]
    assert alice_state["logic_marker"] == "logic-before-llm"
    assert alice_state["protocol_phase_seen"] == "seed_then_recall"
    assert (tmp_path / "chroma_store" / "chroma.sqlite3").exists()
    assert _count_events(tmp_path, "llm", "llm_request_completed") >= 3
    assert any(event["event"] == "embedding_request_completed" for event in _resource_events(tmp_path, "embedding"))
    embedding_traces = [
        item
        for item in _read_jsonl(tmp_path / "resource_calls.jsonl")
        if item.get("resource_type") == "embedding"
    ]
    assert all(_trace_values(item, "agent_id", "agent_ids") == {"alice"} for item in embedding_traces)
    assert all(_trace_values(item, "step_name", "step_names") == {"seed_and_recall"} for item in embedding_traces)
    interaction_types = {
        interaction_type
        for item in embedding_traces
        for interaction_type in _trace_values(item, "interaction_type", "interaction_types")
    }
    assert {"memory_retrieve", "memory_write"}.issubset(interaction_types)


@pytest.mark.asyncio
async def test_real_society0_round_robin_env_logic_and_llm_action_loop_e2e(tmp_path):
    agent_count = 2
    llm_model, embed_model = _build_models(llm_concurrency=agent_count, embed_concurrency=10)
    engine = Society0(
        save_dir=str(tmp_path),
        base_config=_round_robin_llm_agent_config(agent_count),
        llm=llm_model,
        embed=embed_model,
    )

    @engine.step(name="pair_and_converse")
    async def pair_and_converse(ctx):
        assert ctx.capabilities.has("rule", "advance_round_robin_with_pairing", source="environment")
        assert ctx.capabilities.has("behavior", "mark_conversation_participant", source="environment")
        assert ctx.capabilities.has("fov", "get_conversation_fov", source="environment")
        assert ctx.capabilities.has("action", "send_message_to_partner", source="environment")

        pairing = await ctx.rule("advance_round_robin_with_pairing", round_number=1)
        marked = await ctx.agents.all().behavior(
            "mark_conversation_participant",
            marker="paired-before-llm",
            concurrency=1,
        )
        messages = await ctx.agents.all().instruct(
            "Read your conversation context and call send_message_to_partner exactly once. "
            "Send one short friendly message to your current partner, then stop.",
            fovs=["get_conversation_fov"],
            actions=["send_message_to_partner"],
            max_turns=3,
            max_tokens=80,
            temperature=0,
            action_call_limits={"send_message_to_partner": 1},
            required_actions=["send_message_to_partner"],
            reasoning_stages=[
                {
                    "name": "situate",
                    "description": "Use the environment FoV to identify the current partner before sending.",
                }
            ],
            name="paired_message_round",
        )
        return ctx.result(
            metrics={
                "successful_pairs": pairing["successful_pairs"],
                "logic_behavior_success": marked.success_count,
                "logic_behavior_errors": marked.error_count,
                "message_success": messages.success_count,
                "message_errors": messages.error_count,
                "message_action_count": messages.action_counts().get("send_message_to_partner", 0),
            },
            tables={
                "marked": marked.table(),
                "messages": messages.table(),
                "message_actions": messages.actions(),
            },
            observations={
                "message_action_counts": messages.action_counts(),
                "message_action_tags": messages.action_tag_counts(),
            },
        )

    await engine.run(steps=1)

    metrics = _read_jsonl(tmp_path / "metrics.jsonl")[0]["metrics"]
    summary = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    diagnostics = (tmp_path / "diagnostics.md").read_text(encoding="utf-8")
    resource_calls = _read_jsonl(tmp_path / "resource_calls.jsonl")
    checkpoint = read_gzip_json(
        tmp_path / "checkpoints" / "checkpoint_final.json.gz"
    )

    assert metrics["successful_pairs"] == 1
    assert metrics["logic_behavior_success"] == agent_count
    assert metrics["logic_behavior_errors"] == 0
    assert metrics["message_success"] == agent_count
    assert metrics["message_errors"] == 0
    assert metrics["message_action_count"] == agent_count

    logic_executions = summary["events"]["logic_executions"]
    assert logic_executions["rule / advance_round_robin_with_pairing"]["completed_count"] == 1
    assert logic_executions["rule / advance_round_robin_with_pairing"]["success_count"] == 1
    assert logic_executions["behavior / mark_conversation_participant"]["completed_count"] == 1
    assert logic_executions["behavior / mark_conversation_participant"]["success_count"] == agent_count
    assert logic_executions["behavior / mark_conversation_participant"]["agent_count_total"] == agent_count

    capabilities = summary["capabilities"]
    assert capabilities["environment_type"] == "round_robin_conversation"
    assert capabilities["by_source"]["environment"]["rules"] >= 1
    assert capabilities["by_source"]["environment"]["behaviors"] >= 1
    assert capabilities["by_source"]["environment"]["fovs"] >= 1
    assert capabilities["by_source"]["environment"]["actions"] >= 1

    batch = summary["events"]["agent_batches"]["instruct / paired_message_round"]
    assert batch["agent_count"] == agent_count
    assert batch["execution_options"]["memory"] == {
        "retrieve": True,
        "save": True,
        "extract": True,
        "top_k": 10,
    }
    assert batch["execution_options"]["required_actions"] == ["send_message_to_partner"]
    assert batch["execution_options"]["reasoning_stage_count"] == 1
    assert batch["action_counts"].get("send_message_to_partner") == agent_count
    assert batch["successful_action_counts"].get("send_message_to_partner") == agent_count
    assert batch["failed_action_counts"].get("send_message_to_partner", 0) == 0
    assert batch["action_semantics"]["required_actions"]["observed_counts"]["send_message_to_partner"] == agent_count
    assert summary["resources"]["llm"]["fidelity"]["memory_extraction"]["call_count"] >= agent_count
    assert summary["resources"]["embedding"]["fidelity"]["memory_io"]["call_count"] >= 1

    assert checkpoint["environment_data"]["state"]["message_counter"] == agent_count
    for agent_id in ("participant_0", "participant_1"):
        assert checkpoint["agents_data"][agent_id]["state"]["conversation_marker"] == "paired-before-llm"
    round_messages = checkpoint["environment_data"]["state"]["round_messages"]["1"]
    assert sum(len(messages) for messages in round_messages.values()) == agent_count

    llm_traces = _successful_resource_calls(resource_calls, "llm")
    embedding_traces = _successful_resource_calls(resource_calls, "embedding")
    assert len([item for item in llm_traces if item.get("interaction_type") == "instruct"]) >= agent_count
    assert len([item for item in llm_traces if item.get("interaction_type") == "memory_extract"]) >= agent_count
    assert embedding_traces
    assert "### rule / advance_round_robin_with_pairing" in diagnostics
    assert "### behavior / mark_conversation_participant" in diagnostics
    assert "Action semantics: required_actions configured [send_message_to_partner]" in diagnostics
    assert "Memory: retrieved 2/2, saved 2, extractive enabled 2" in diagnostics


@pytest.mark.asyncio
async def test_real_society0_social_publish_action_e2e(tmp_path):
    agent_count = _safe_int(os.getenv("SOCIETY0_REAL_E2E_ACTION_AGENT_COUNT"), default=2)
    agent_count = max(2, min(agent_count, 20))
    llm_model, embed_model = _build_models(llm_concurrency=agent_count, embed_concurrency=agent_count)
    engine = Society0(
        save_dir=str(tmp_path),
        base_config=_social_publish_agent_config(agent_count),
        llm=llm_model,
        embed=embed_model,
    )

    @engine.step(name="publish_once")
    async def publish_once(ctx):
        started = time.perf_counter()
        result = await ctx.agents.all().instruct(
            "You must call the publish_post tool exactly once. Publish a short original post "
            "about campus life, then finish the round without calling more tools.",
            actions=["publish_post"],
            output=None,
            max_turns=3,
            max_tokens=160,
            temperature=0,
            action_call_limits={"publish_post": 1},
            required_actions=["publish_post"],
            name="publish_round",
        )
        duration = time.perf_counter() - started
        return ctx.result(
            metrics={
                "publish_errors": result.error_count,
                "publish_success": result.success_count,
                "publish_action_count": result.action_counts().get("publish_post", 0),
                "duration_sec": duration,
            },
            tables={"published": result.table()},
        )

    await engine.run(steps=1)

    metrics = _read_jsonl(tmp_path / "metrics.jsonl")[0]["metrics"]
    checkpoint = read_gzip_json(
        tmp_path / "checkpoints" / "checkpoint_final.json.gz"
    )
    posts = checkpoint["environment_data"]["state"].get("posts", {})
    llm_request_count = _count_events(tmp_path, "llm", "llm_request_completed")
    events = _read_jsonl(tmp_path / "events.jsonl")
    resource_calls = _read_jsonl(tmp_path / "resource_calls.jsonl")
    summary = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))

    assert metrics["publish_errors"] == 0
    assert metrics["publish_success"] == agent_count
    assert metrics["publish_action_count"] == agent_count
    author_ids = {post.get("author_id") for post in posts.values()}
    assert len(posts) == agent_count
    assert {f"user_{idx}" for idx in range(agent_count)}.issubset(author_ids)
    assert llm_request_count >= agent_count * 2
    assert not any("embedding" in post for post in posts.values())
    assert any(event.get("event") == "code_step_started" and event.get("step_name") == "publish_once" for event in events)
    assert sum(1 for event in events if event.get("event_type") == "agent_action") >= agent_count
    batch_events = [event for event in events if event.get("event_type", "").startswith("agent_batch_")]
    lifecycle_events = [
        event
        for event in batch_events
        if event.get("event_type") in {"agent_batch_started", "agent_batch_completed"}
    ]
    progress_events = [event for event in batch_events if event.get("event_type") == "agent_batch_progress"]
    assert [event["event_type"] for event in lifecycle_events] == ["agent_batch_started", "agent_batch_completed"]
    assert lifecycle_events[0]["event_data"]["concurrency"] == agent_count
    assert lifecycle_events[1]["event_data"]["success_count"] == agent_count
    assert [event["event_data"]["completed_count"] for event in progress_events] == list(range(1, agent_count + 1))
    assert progress_events[-1]["event_data"]["success_count"] == agent_count
    publish_batch = summary["events"]["agent_batches"]["instruct / publish_round"]
    assert publish_batch["execution_options"]["memory"] == {
        "retrieve": True,
        "save": True,
        "extract": True,
        "top_k": 10,
    }
    assert publish_batch["execution_options"]["required_actions"] == ["publish_post"]
    assert publish_batch["successful_action_counts"].get("publish_post") == agent_count
    assert publish_batch["failed_action_counts"] == {}
    assert publish_batch["termination_reason_counts"] == {"action_budget_exhausted": agent_count}
    assert publish_batch["action_semantics"]["required_actions"]["configured"] == ["publish_post"]
    assert publish_batch["action_semantics"]["required_actions"]["observed_counts"]["publish_post"] == agent_count
    llm_traces = _successful_resource_calls(resource_calls, "llm")
    embedding_traces = _successful_resource_calls(resource_calls, "embedding")
    publish_llm_traces = [item for item in llm_traces if item.get("interaction_type") == "instruct"]
    memory_extract_traces = [item for item in llm_traces if item.get("interaction_type") == "memory_extract"]
    env_embedding_traces = [
        item for item in embedding_traces if item.get("interaction_type") == "env_post_embedding"
    ]
    memory_embedding_traces = [
        item for item in embedding_traces if item.get("interaction_type") == "memory_write"
    ]
    llm_started = [
        item
        for item in resource_calls
        if item.get("resource_type") == "llm" and item.get("status") == "started"
    ]
    embedding_started = [
        item
        for item in resource_calls
        if item.get("resource_type") == "embedding" and item.get("status") == "started"
    ]
    assert llm_traces
    _assert_resource_timing(llm_traces)
    _assert_resource_timing(embedding_traces)
    assert len(llm_started) >= agent_count * 2
    assert embedding_started
    embedded_agent_ids = {
        agent_id
        for item in embedding_traces
        for agent_id in (item.get("agent_ids") or ([item.get("agent_id")] if item.get("agent_id") else []))
    }
    embedded_post_ids = {
        post_id
        for item in embedding_traces
        for post_id in (item.get("post_ids") or ([item.get("post_id")] if item.get("post_id") else []))
    }
    assert len(publish_llm_traces) >= agent_count
    assert len(memory_extract_traces) >= agent_count
    assert all(item.get("step_name") == "publish_once" for item in llm_traces)
    assert all(item.get("interaction_name") == "publish_round" for item in publish_llm_traces)
    # Agent instructions use the per-step budget; memory extraction is a
    # separate LLM interaction with its own fixed bounded output budget.
    assert all(item.get("max_tokens") == 160 for item in publish_llm_traces)
    assert all(item.get("max_tokens") == 2048 for item in memory_extract_traces)
    # The required-action budget may trigger a tool-free closing request; at
    # least one instruct call must still carry the publish tool definition.
    assert any(item.get("tools_characters", 0) > 0 for item in publish_llm_traces)
    assert all(item.get("payload_characters", 0) >= item.get("tools_characters", 0) for item in llm_traces)
    assert 1 <= sum(item.get("texts_count") or 0 for item in env_embedding_traces) <= agent_count
    assert sum(item.get("texts_count") or 0 for item in memory_embedding_traces) >= agent_count
    assert all(item.get("step_name") == "publish_once" for item in embedding_traces)
    assert summary["resources"]["llm"]["by_interaction_type"]["memory_extract"]["call_count"] >= agent_count
    assert summary["resources"]["llm"]["fidelity"]["memory_extraction"]["call_count"] >= agent_count
    assert summary["resources"]["embedding"]["fidelity"]["memory_io"]["call_count"] >= 1
    assert summary["resources"]["embedding"]["fidelity"]["environment"]["call_count"] >= 1
    assert embedded_agent_ids == author_ids
    assert embedded_post_ids == set(posts.keys())


@pytest.mark.asyncio
async def test_real_society0_environment_action_tag_e2e(tmp_path):
    llm_model, embed_model = _build_models(llm_concurrency=1, embed_concurrency=1)
    engine = Society0(
        save_dir=str(tmp_path),
        base_config=_social_publish_agent_config(2),
        llm=llm_model,
        embed=embed_model,
    )

    @engine.step(name="inspect_environment_actions")
    async def inspect_environment_actions(ctx):
        ctx.env.state["posts"]["post_hot"] = {
            "post_id": "post_hot",
            "author_id": "user_1",
            "content": "Campus cafe prices changed today and students are discussing the update.",
            "created_tick": ctx.step,
            "likes": ["user_0", "user_1"],
            "replies": [],
            "reply_to": None,
            "view_count": 0,
            "tags": ["campus"],
        }
        viewer = ctx.agents.ids(["user_0"])
        result = await viewer.instruct(
            "Use the environment tools to inspect the current hot posts. "
            "Call get_trending_posts exactly once, then stop.",
            actions=["environment"],
            output=None,
            max_turns=2,
            max_tokens=100,
            temperature=0,
            max_action_calls=1,
            action_call_limits={"get_trending_posts": 1},
            required_actions=["get_trending_posts"],
            name="environment_action_lookup",
        )
        return ctx.result(
            metrics={
                "lookup_errors": result.error_count,
                "lookup_success": result.success_count,
                "trending_calls": result.action_counts().get("get_trending_posts", 0),
            },
            tables={"lookup": result.table(), "lookup_actions": result.actions()},
        )

    await engine.run(steps=1)

    metrics = _read_jsonl(tmp_path / "metrics.jsonl")[0]["metrics"]
    summary = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    checkpoint = read_gzip_json(
        tmp_path / "checkpoints" / "checkpoint_final.json.gz"
    )

    assert metrics["lookup_errors"] == 0
    assert metrics["lookup_success"] == 1
    assert metrics["trending_calls"] == 1
    post_state = checkpoint["environment_data"]["state"]["posts"]["post_hot"]
    assert int(post_state.get("view_count") or 0) >= 1

    action_names = {entry["name"] for entry in summary["capabilities"]["by_kind"]["actions"]}
    trending_capability = next(
        entry
        for entry in summary["capabilities"]["by_kind"]["actions"]
        if entry["name"] == "get_trending_posts"
    )
    assert "get_trending_posts" in action_names
    assert "environment" in trending_capability["tags"]

    batch = summary["events"]["agent_batches"]["instruct / environment_action_lookup"]
    assert batch["actions"] == ["environment"]
    assert batch["concurrency"] == 1
    assert batch["successful_action_counts"].get("get_trending_posts") == 1
    assert batch["failed_action_counts"].get("get_trending_posts", 0) == 0
    assert batch["action_semantics"]["required_actions"]["configured"] == ["get_trending_posts"]
    assert batch["action_semantics"]["required_actions"]["observed_counts"]["get_trending_posts"] == 1
    assert batch["execution_options"]["memory"] == {
        "retrieve": True,
        "save": True,
        "extract": True,
        "top_k": 10,
    }
    assert batch["memory_summary"]["record_count"] == 1
    assert batch["memory_summary"]["save_enabled_count"] == 1
    assert batch["memory_summary"]["extraction_enabled_count"] == 1
    assert batch["resources"]["llm"]["by_interaction_type"]["instruct"]["call_count"] >= 1
    assert summary["resources"]["llm"]["by_interaction_type"]["memory_extract"]["call_count"] >= 1
    assert summary["resources"]["embedding"]["fidelity"]["memory_io"]["call_count"] >= 1


@pytest.mark.asyncio
async def test_real_society0_terminal_action_retry_preserves_agent_loop_e2e(tmp_path):
    llm_model, embed_model = _build_models(llm_concurrency=1, embed_concurrency=1)
    engine = Society0(
        save_dir=str(tmp_path),
        base_config=_llm_agent_config(),
        llm=llm_model,
        embed=embed_model,
    )
    attempts: list[dict] = []

    @engine.registry.env.action(
        name="submit_final_decision",
        desc=(
            "Submit the final decision for this round. If the tool returns an "
            "error, inspect the error and call this tool again with a corrected decision."
        ),
        tags=["decision"],
    )
    async def submit_final_decision(agent, env, decision: str = "approve"):
        attempts.append({"agent_id": agent.id, "decision": decision})
        env.state["last_terminal_decision_attempt"] = decision
        if len(attempts) == 1:
            return (
                "Error: transient submission rejection. "
                "You must call submit_final_decision again with decision='approve'."
            )
        return {"ok": True, "accepted": True, "decision": decision}

    @engine.step(name="terminal_retry")
    async def terminal_retry(ctx):
        result = await ctx.agents.all().instruct(
            "Call submit_final_decision to submit your final decision. "
            "If the tool returns an error, read the error and retry by calling "
            "submit_final_decision again once with decision='approve'.",
            actions=["env.submit_final_decision"],
            output=None,
            max_turns=4,
            max_tokens=120,
            temperature=0,
            terminal_actions=["submit_final_decision"],
            # Two attempts are part of this scenario: the first transient
            # rejection must consume one budget slot, while the retry uses the
            # second. Failed attempts remain counted by the runtime.
            action_call_limits={"submit_final_decision": 2},
            required_actions=["submit_final_decision"],
            reasoning_stages=[
                {
                    "name": "correct",
                    "description": "Use the tool result to decide whether a retry is required.",
                }
            ],
            name="terminal_retry_round",
        )
        rows = result.table()
        return ctx.result(
            metrics={
                "retry_errors": result.error_count,
                "retry_success": result.success_count,
                "submit_attempts": result.action_counts().get("submit_final_decision", 0),
                "failed_submit_attempts": result.failed_action_counts().get("submit_final_decision", 0),
                "max_turns": max(row.get("total_turns", 0) for row in rows),
            },
            tables={"terminal_retry": rows, "terminal_retry_actions": result.actions()},
        )

    await engine.run(steps=1)

    metrics = _read_jsonl(tmp_path / "metrics.jsonl")[0]["metrics"]
    summary = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    resource_calls = _read_jsonl(tmp_path / "resource_calls.jsonl")
    diagnostic_report = (tmp_path / "diagnostics.md").read_text(encoding="utf-8")
    llm_traces = _successful_resource_calls(resource_calls, "llm")
    embedding_traces = _successful_resource_calls(resource_calls, "embedding")

    assert len(attempts) == 2
    assert metrics["retry_errors"] == 0
    assert metrics["retry_success"] == 1
    assert metrics["submit_attempts"] == 2
    assert metrics["failed_submit_attempts"] == 1
    assert metrics["max_turns"] == 2

    batch = summary["events"]["agent_batches"]["instruct / terminal_retry_round"]
    assert batch["actions"] == ["env.submit_final_decision"]
    assert batch["execution_options"]["terminal_actions"] == ["submit_final_decision"]
    assert batch["execution_options"]["required_actions"] == ["submit_final_decision"]
    assert batch["execution_options"]["reasoning_stage_count"] == 1
    experiment_action = next(
        entry
        for entry in summary["capabilities"]["by_kind"]["actions"]
        if entry["name"] == "submit_final_decision"
    )
    assert experiment_action["source"] == "experiment"
    assert set(experiment_action["tags"]) >= {
        "environment",
        "experiment",
        "decision",
        "submit_final_decision",
        "env.submit_final_decision",
    }
    assert batch["action_counts"]["submit_final_decision"] == 2
    assert batch["successful_action_counts"]["submit_final_decision"] == 1
    assert batch["failed_action_counts"]["submit_final_decision"] == 1
    assert batch["action_tag_counts"]["submit_final_decision"] == 1
    assert batch["action_tag_counts"]["env.submit_final_decision"] == 1
    assert batch["action_tag_counts"]["decision"] == 1
    assert batch["termination_reason_counts"] == {"terminal_action": 1}
    assert batch["agent_duration_summary"]["slowest_agents"][0]["termination_reason"] == "terminal_action"
    assert batch["memory_summary"]["retrieve_enabled_count"] == 1
    assert batch["memory_summary"]["save_enabled_count"] == 1
    assert batch["memory_summary"]["extraction_enabled_count"] == 1
    assert batch["resources"]["llm"]["by_interaction_type"]["instruct"]["call_count"] >= 2
    assert summary["resources"]["llm"]["by_interaction_type"]["memory_extract"]["call_count"] >= 1
    assert summary["resources"]["embedding"]["fidelity"]["memory_io"]["call_count"] >= 1
    assert summary["outputs"]["files"]["diagnostics.md"]["bytes"] > 0
    assert (
        "Actions: attempted submit_final_decision=2; successful submit_final_decision=1; "
        "failed submit_final_decision=1."
    ) in diagnostic_report
    assert "Successful action tags:" in diagnostic_report
    for tag in [
        "decision=1",
        "env.submit_final_decision=1",
        "environment=1",
        "experiment=1",
        "submit_final_decision=1",
    ]:
        assert tag in diagnostic_report
    assert "Termination reasons: terminal_action=1." in diagnostic_report
    assert (
        "Action semantics: required_actions configured [submit_final_decision], "
        "observed submit_final_decision=1."
    ) in diagnostic_report
    assert "Memory: retrieved 1/1, saved 1, extractive enabled 1" in diagnostic_report
    assert len([item for item in llm_traces if item.get("interaction_type") == "instruct"]) >= 2
    assert len([item for item in llm_traces if item.get("interaction_type") == "memory_extract"]) >= 1
    assert embedding_traces


@pytest.mark.asyncio
async def test_real_society0_social_browse_completion_tags_default_memory_e2e(tmp_path):
    agent_count = _safe_int(os.getenv("SOCIETY0_REAL_E2E_BROWSE_AGENT_COUNT"), default=2)
    agent_count = max(2, min(agent_count, 6))
    llm_model, embed_model = _build_models(llm_concurrency=agent_count, embed_concurrency=10)
    engine = Society0(
        save_dir=str(tmp_path),
        base_config=_social_publish_agent_config(agent_count),
        llm=llm_model,
        embed=embed_model,
    )

    @engine.step(name="publish_once")
    async def publish_once(ctx):
        result = await ctx.agents.all().instruct(
            "You must call publish_post exactly once. Publish a short original post about campus life, "
            "then finish the round without calling more tools.",
            actions=["publish_post"],
            output=None,
            max_turns=3,
            max_tokens=80,
            temperature=0,
            reasoning_stages=[
                {
                    "name": "plan",
                    "description": "Choose a concise campus-life topic before publishing.",
                }
            ],
            action_call_limits={"publish_post": 1},
            required_actions=["publish_post"],
            name="publish_round",
        )
        return ctx.result(
            metrics={
                "publish_errors": result.error_count,
                "publish_success": result.success_count,
                "publish_action_count": result.action_counts().get("publish_post", 0),
            },
            tables={"published": result.table(), "publish_actions": result.actions()},
            observations={
                "action_counts": result.action_counts(),
                "action_tag_counts": result.action_tag_counts(),
            },
        )

    @engine.step(name="browse_once")
    async def browse_once(ctx):
        result = await ctx.agents.all().instruct(
            "Browse your recommended feed. Call get_trending_posts at most once if useful. "
            "Then make one real interaction by commenting on post_1 if it exists. "
            "After the comment, stop the round.",
            fovs=["recommended_feed"],
            actions=["get_trending_posts", "comment"],
            output=None,
            max_turns=3,
            max_tokens=120,
            temperature=0,
            reasoning_stages=[
                {
                    "name": "observe",
                    "description": "Inspect visible feed context before deciding whether and how to comment.",
                }
            ],
            action_call_limits={"get_trending_posts": 1, "comment": 1},
            completion_action_tags=["social_write"],
            required_action_tags=["social_write"],
            name="browse_round",
        )
        rows = result.table()
        return ctx.result(
            metrics={
                "browse_errors": result.error_count,
                "browse_success": result.success_count,
                "max_browse_turns": max(row.get("total_turns", 0) for row in rows),
                "comment_count": result.action_counts().get("comment", 0),
                "social_write_count": result.action_tag_counts().get("social_write", 0),
            },
            tables={"browse": rows, "browse_actions": result.actions()},
            observations={
                "action_counts": result.action_counts(),
                "action_tag_counts": result.action_tag_counts(),
            },
        )

    await engine.run(steps=1)

    metrics = _read_jsonl(tmp_path / "metrics.jsonl")
    publish_metrics = metrics[0]["metrics"]
    browse_metrics = metrics[1]["metrics"]
    summary = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    resource_calls = _read_jsonl(tmp_path / "resource_calls.jsonl")
    llm_traces = _successful_resource_calls(resource_calls, "llm")
    browse_llm_traces = [item for item in llm_traces if item.get("step_name") == "browse_once"]

    assert publish_metrics["publish_errors"] == 0
    assert publish_metrics["publish_success"] == agent_count
    assert publish_metrics["publish_action_count"] == agent_count
    assert browse_metrics["browse_errors"] == 0
    assert browse_metrics["browse_success"] == agent_count
    assert browse_metrics["max_browse_turns"] <= 3
    assert browse_metrics["comment_count"] >= 1
    assert browse_metrics["social_write_count"] >= 1
    assert summary["agent_operations"]["publish_once"]["action_counts"].get("publish_post", 0) >= 1
    assert summary["agent_operations"]["publish_once"]["action_tag_counts"].get("social_write", 0) >= 1
    assert summary["agent_operations"]["browse_once"]["turns_max"] <= 3
    assert summary["agent_operations"]["browse_once"]["action_counts"].get("comment", 0) >= 1
    assert summary["agent_operations"]["browse_once"]["action_tag_counts"].get("social_write", 0) >= 1
    assert summary["agent_operations"]["browse_once"]["action_tag_counts"].get("social_read", 0) >= 1
    assert summary["agent_operations"]["browse_once"]["resources"]["llm"]["payload_characters"] >= (
        summary["agent_operations"]["browse_once"]["resources"]["llm"]["tools_characters"]
    )
    assert summary["agent_operations"]["browse_once"]["resources"]["llm"]["tools_count_max"] >= 1
    browse_agent_loop_traces = [item for item in browse_llm_traces if item.get("interaction_type") == "instruct"]
    browse_memory_extract_traces = [
        item for item in browse_llm_traces if item.get("interaction_type") == "memory_extract"
    ]
    assert len(browse_agent_loop_traces) <= agent_count * 3
    assert len(browse_memory_extract_traces) >= agent_count
    assert all(item.get("max_tokens") == 120 for item in browse_agent_loop_traces)
    _assert_resource_timing(browse_llm_traces)
    publish_batch = summary["events"]["agent_batches"]["instruct / publish_round"]
    browse_batch = summary["events"]["agent_batches"]["instruct / browse_round"]
    assert publish_batch["execution_options"]["memory"] == {
        "retrieve": True,
        "save": True,
        "extract": True,
        "top_k": 10,
    }
    assert publish_batch["execution_options"]["reasoning_stage_count"] == 1
    assert publish_batch["execution_options"]["reasoning_stages"][0]["name"] == "plan"
    assert publish_batch["action_counts"].get("publish_post", 0) >= 1
    assert publish_batch["successful_action_counts"].get("publish_post", 0) >= 1
    assert publish_batch["failed_action_counts"].get("publish_post", 0) == 0
    assert publish_batch["action_tag_counts"].get("social_write", 0) >= 1
    assert publish_batch["action_duration_summary"]["record_count"] >= agent_count
    assert publish_batch["action_duration_summary"]["by_action"]["publish_post"]["record_count"] >= agent_count
    assert publish_batch["action_duration_summary"]["bottleneck_action"] in publish_batch[
        "action_duration_summary"
    ]["by_action"]
    assert publish_batch["termination_reason_counts"] == {"action_budget_exhausted": agent_count}
    publish_duration_summary = publish_batch["agent_duration_summary"]
    assert publish_duration_summary["record_count"] == agent_count
    assert publish_duration_summary["total_sec"] >= publish_duration_summary["max_sec"] >= publish_duration_summary["min_sec"] > 0
    assert len(publish_duration_summary["slowest_agents"]) == agent_count
    assert {
        sample["termination_reason"] for sample in publish_duration_summary["slowest_agents"]
    } == {"action_budget_exhausted"}
    publish_phase_summary = publish_batch["phase_timing_summary"]
    assert publish_phase_summary["record_count"] == agent_count
    assert publish_phase_summary["phases"]["agent_loop"]["record_count"] == agent_count
    assert publish_phase_summary["phases"]["memory_extract"]["record_count"] == agent_count
    assert publish_phase_summary["phases"]["memory_write"]["record_count"] >= 1
    assert publish_phase_summary["bottleneck"] in publish_phase_summary["phases"]
    assert browse_batch["execution_options"]["memory"] == {
        "retrieve": True,
        "save": True,
        "extract": True,
        "top_k": 10,
    }
    assert browse_batch["execution_options"]["reasoning_stage_count"] == 1
    assert browse_batch["execution_options"]["reasoning_stages"][0]["name"] == "observe"
    assert browse_batch["action_counts"].get("comment", 0) >= 1
    assert browse_batch["successful_action_counts"].get("comment", 0) >= 1
    assert browse_batch["failed_action_counts"].get("comment", 0) == 0
    assert browse_batch["action_tag_counts"].get("social_read", 0) >= 1
    assert browse_batch["action_tag_counts"].get("social_write", 0) >= 1
    assert browse_batch["action_duration_summary"]["record_count"] >= 1
    assert browse_batch["action_duration_summary"]["by_action"]["comment"]["record_count"] >= 1
    assert browse_batch["action_duration_summary"]["bottleneck_action"] in browse_batch[
        "action_duration_summary"
    ]["by_action"]
    assert browse_batch["action_semantics"]["completion_action_tags"]["configured"] == ["social_write"]
    assert browse_batch["action_semantics"]["completion_action_tags"]["observed_counts"]["social_write"] >= 1
    assert browse_batch["termination_reason_counts"] == {"completion_action_tag": agent_count}
    browse_duration_summary = browse_batch["agent_duration_summary"]
    assert browse_duration_summary["record_count"] == agent_count
    assert browse_duration_summary["total_sec"] >= browse_duration_summary["max_sec"] >= browse_duration_summary["min_sec"] > 0
    assert len(browse_duration_summary["slowest_agents"]) == agent_count
    assert {
        sample["termination_reason"] for sample in browse_duration_summary["slowest_agents"]
    } == {"completion_action_tag"}
    browse_phase_summary = browse_batch["phase_timing_summary"]
    assert browse_phase_summary["record_count"] == agent_count
    assert browse_phase_summary["phases"]["fov_collection"]["record_count"] == agent_count
    assert browse_phase_summary["phases"]["agent_loop"]["record_count"] == agent_count
    assert browse_phase_summary["phases"]["memory_extract"]["record_count"] == agent_count
    assert browse_phase_summary["bottleneck"] in browse_phase_summary["phases"]
    assert summary["agent_operations"]["publish_once"]["resources"]["llm"]["fidelity"][
        "memory_extraction"
    ]["call_count"] >= agent_count
    assert summary["agent_operations"]["browse_once"]["resources"]["llm"]["fidelity"][
        "memory_extraction"
    ]["call_count"] >= agent_count
    assert summary["agent_operations"]["browse_once"]["resources"]["embedding"]["fidelity"][
        "memory_io"
    ]["call_count"] >= 1


@pytest.mark.asyncio
async def test_real_society0_multi_tick_social_workflow_e2e(tmp_path):
    agent_count = _safe_int(os.getenv("SOCIETY0_REAL_E2E_MULTI_TICK_AGENT_COUNT"), default=2)
    agent_count = max(2, min(agent_count, 4))
    llm_model, embed_model = _build_models(llm_concurrency=agent_count, embed_concurrency=10)
    engine = Society0(
        save_dir=str(tmp_path),
        base_config=_social_publish_agent_config(agent_count),
        llm=llm_model,
        embed=embed_model,
    )

    @engine.step(name="publish_first_tick")
    async def publish_first_tick(ctx):
        if ctx.step != 0:
            return ctx.result(metrics={"published_this_tick": 0})
        result = await ctx.agents.all().instruct(
            "Call publish_post once with a concise original campus-life post, then stop.",
            actions=["publish_post"],
            output=None,
            max_turns=3,
            max_tokens=80,
            temperature=0,
            action_call_limits={"publish_post": 1},
            name="multi_tick_publish",
        )
        return ctx.result(
            metrics={"published_this_tick": result.success_count, "publish_errors": result.error_count},
            tables={"published": result.table()},
        )

    @engine.step(name="browse_second_tick")
    async def browse_second_tick(ctx):
        if ctx.step != 1:
            return ctx.result(metrics={"browsed_this_tick": 0})
        result = await ctx.agents.all().instruct(
            "Browse the recommended feed. Comment once on post_1 if it is visible, then stop.",
            fovs=["recommended_feed"],
            actions=["comment"],
            output=None,
            max_turns=3,
            max_tokens=120,
            temperature=0,
            action_call_limits={"comment": 1},
            completion_action_tags=["social_write"],
            name="multi_tick_browse",
        )
        rows = result.table()
        return ctx.result(
            metrics={
                "browsed_this_tick": result.success_count,
                "browse_errors": result.error_count,
                "max_browse_turns": max(row.get("total_turns", 0) for row in rows),
                "comment_count": result.action_counts().get("comment", 0),
            },
            tables={"browse": rows, "browse_actions": result.actions()},
            observations={"action_counts": result.action_counts()},
        )

    await engine.run(steps=2)

    metrics = _read_jsonl(tmp_path / "metrics.jsonl")
    summary = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    checkpoint = read_gzip_json(
        tmp_path / "checkpoints" / "checkpoint_final.json.gz"
    )
    resource_calls = _read_jsonl(tmp_path / "resource_calls.jsonl")
    diagnostic_report = (tmp_path / "diagnostics.md").read_text(encoding="utf-8")
    events = _read_jsonl(tmp_path / "events.jsonl")

    posts = checkpoint["environment_data"]["state"].get("posts", {})
    recommended_posts = checkpoint["environment_data"]["state"].get("recommended_posts", {})
    llm_traces = _successful_resource_calls(resource_calls, "llm")
    embedding_traces = _successful_resource_calls(resource_calls, "embedding")

    assert summary["steps_requested"] == 2
    assert summary["steps_completed"] == 2
    assert summary["final_step"] == 2
    assert summary["outputs"]["files"]["diagnostics.md"]["bytes"] > 0
    assert len(posts) == agent_count
    assert any(int(post.get("view_count") or 0) >= 1 for post in posts.values())
    assert recommended_posts
    assert metrics[0]["step"] == 0 and metrics[0]["metrics"]["published_this_tick"] == agent_count
    assert metrics[1]["step"] == 0 and metrics[1]["metrics"]["browsed_this_tick"] == 0
    assert metrics[2]["step"] == 1 and metrics[2]["metrics"]["published_this_tick"] == 0
    assert metrics[3]["step"] == 1 and metrics[3]["metrics"]["browsed_this_tick"] == agent_count
    assert metrics[3]["metrics"]["max_browse_turns"] <= 2
    assert metrics[3]["metrics"]["comment_count"] >= 1

    assert summary["agent_operations"]["publish_first_tick"]["agent_count"] == agent_count
    assert summary["agent_operations"]["publish_first_tick"]["action_counts"].get("publish_post") == agent_count
    assert summary["agent_operations"]["browse_second_tick"]["agent_count"] == agent_count
    assert summary["agent_operations"]["browse_second_tick"]["action_counts"].get("comment", 0) >= 1
    assert summary["agent_operations"]["publish_first_tick"]["resources"]["llm"]["call_count"] >= agent_count
    assert summary["agent_operations"]["publish_first_tick"]["resources"]["embedding"]["call_count"] >= 1
    assert summary["agent_operations"]["browse_second_tick"]["resources"]["llm"]["call_count"] >= agent_count
    assert summary["agent_operations"]["browse_second_tick"]["resources"]["embedding"]["call_count"] >= 1
    assert summary["resources"]["llm"]["fidelity"]["agent_loop"]["call_count"] >= agent_count * 2
    assert summary["resources"]["llm"]["fidelity"]["memory_extraction"]["call_count"] >= agent_count * 2
    assert summary["resources"]["embedding"]["fidelity"]["memory_io"]["call_count"] >= 1
    assert summary["resources"]["embedding"]["fidelity"]["environment"]["call_count"] >= 2
    assert (
        summary["resources"]["embedding"]["by_interaction_type"]["env_post_embedding"]["call_count"]
        >= 1
    )
    assert (
        summary["resources"]["embedding"]["by_interaction_type"]["semantic_recommendation"]["call_count"]
        >= 1
    )
    assert (
        summary["agent_operations"]["publish_first_tick"]["resources"]["embedding"]["fidelity"][
            "environment"
        ]["call_count"]
        >= 1
    )
    assert (
        summary["agent_operations"]["browse_second_tick"]["resources"]["embedding"]["fidelity"][
            "environment"
        ]["call_count"]
        >= 1
    )
    # Message count is model/provider-dependent (the observed run may finish
    # in three messages); require only the invariant system+user request
    # envelope while retaining the full observed statistic in the summary.
    assert summary["agent_operations"]["browse_second_tick"]["resources"]["llm"]["messages_count_max"] >= 2
    assert summary["agent_operations"]["publish_first_tick"]["resources"]["llm"]["tools_characters"] > 0
    assert summary["agent_operations"]["browse_second_tick"]["resources"]["llm"]["payload_characters"] >= (
        summary["agent_operations"]["browse_second_tick"]["resources"]["llm"]["tools_characters"]
    )
    capabilities = summary["capabilities"]
    assert capabilities["environment_type"] == "social_network"
    assert capabilities["by_source"]["environment"]["fovs"] >= 1
    assert capabilities["by_source"]["environment"]["actions"] >= 1
    assert capabilities["by_source"]["environment"]["rules"] >= 1
    fov_names = {entry["name"] for entry in capabilities["by_kind"]["fovs"]}
    action_names = {entry["name"] for entry in capabilities["by_kind"]["actions"]}
    assert "recommended_feed" in fov_names
    assert {"publish_post", "comment", "get_trending_posts"}.issubset(action_names)
    publish_batch = summary["events"]["agent_batches"]["instruct / multi_tick_publish"]
    browse_batch = summary["events"]["agent_batches"]["instruct / multi_tick_browse"]
    assert publish_batch["execution_options"]["memory"] == {
        "retrieve": True,
        "save": True,
        "extract": True,
        "top_k": 10,
    }
    assert publish_batch["memory_summary"]["retrieve_enabled_count"] == agent_count
    assert publish_batch["memory_summary"]["save_enabled_count"] == agent_count
    assert publish_batch["memory_summary"]["extraction_enabled_count"] == agent_count
    assert publish_batch["execution_options"]["action_call_limits"] == {"publish_post": 1}
    assert browse_batch["fovs"] == ["recommended_feed"]
    assert browse_batch["actions"] == ["comment"]
    assert browse_batch["execution_options"]["memory"] == {
        "retrieve": True,
        "save": True,
        "extract": True,
        "top_k": 10,
    }
    assert browse_batch["execution_options"]["completion_action_tags"] == ["social_write"]
    assert browse_batch["execution_options"]["action_call_limits"] == {"comment": 1}
    assert browse_batch["successful_action_counts"].get("comment", 0) >= 1
    assert browse_batch["failed_action_counts"].get("comment", 0) == 0
    assert browse_batch["action_semantics"]["completion_action_tags"]["observed_counts"]["social_write"] >= 1
    assert browse_batch["memory_summary"]["retrieve_enabled_count"] == agent_count
    assert browse_batch["memory_summary"]["save_enabled_count"] == agent_count
    assert browse_batch["memory_summary"]["extraction_enabled_count"] == agent_count

    assert len([item for item in llm_traces if item.get("step_name") == "publish_first_tick"]) >= agent_count * 2
    assert len([item for item in llm_traces if item.get("step_name") == "browse_second_tick"]) >= agent_count * 2
    assert any(item.get("interaction_type") == "env_post_embedding" for item in embedding_traces)
    assert any(item.get("interaction_type") == "semantic_recommendation" for item in embedding_traces)
    assert any(event.get("event_type") == "social_recommendation_state_flushed" for event in events)
    assert "Environment: `social_network`." in diagnostic_report
    assert "## Environment Hooks" in diagnostic_report
    assert "### after_tick" in diagnostic_report
    assert "## Agent Batches" in diagnostic_report
    assert "### instruct / multi_tick_publish" in diagnostic_report
    assert "### instruct / multi_tick_browse" in diagnostic_report
    assert "Memory: retrieved" in diagnostic_report
    assert "Action semantics: completion_action_tags configured [social_write]" in diagnostic_report
