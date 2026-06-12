"""Real LLM + embedding endpoint E2E tests.

These tests are opt-in so normal local/CI runs do not depend on private
infrastructure. They load model settings from a caller-provided platform root.
"""

from __future__ import annotations

import json
import os
import importlib.util
import time
from pathlib import Path

import asyncio
import pytest
from pydantic import BaseModel, Field

from society0 import EmbedModel, LLMModel, Society0


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
    raw_platform_root = os.getenv("SOCIETY0_PLATFORM_ROOT")
    if not raw_platform_root:
        pytest.skip("Set SOCIETY0_PLATFORM_ROOT to run real endpoint e2e tests.")

    platform_root = Path(raw_platform_root)
    if not platform_root.exists():
        pytest.skip(f"Society0 platform repo is not available at {platform_root}")

    secrets_path = platform_root / "core" / "services" / "secrets_service.py"
    try:
        spec = importlib.util.spec_from_file_location("society0_platform_secrets_service", secrets_path)
        if spec is None or spec.loader is None:
            pytest.skip(f"Cannot load Society0 SecretsService from {secrets_path}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        secrets_service = module.SecretsService
    except Exception as exc:  # pragma: no cover - local infrastructure guard
        pytest.skip(f"Cannot import Society0 SecretsService: {exc}")

    secrets = secrets_service()
    return secrets.get_llm_config(), secrets.get_embedding_config()


def _build_models(
    *,
    llm_concurrency: int = 1,
    embed_concurrency: int = 1,
) -> tuple[LLMModel, EmbedModel]:
    llm_env, embedding_env = _load_default_endpoint_env()
    endpoints_json = (embedding_env.get("EMBEDDING_ENDPOINTS_JSON") or "").strip()
    embedding_endpoint = _first_embedding_endpoint(endpoints_json) if endpoints_json else {}
    provider_type = str(embedding_endpoint.get("provider_type") or "ollama").lower()
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


def _read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _resource_events(run_dir: Path, resource: str) -> list[dict]:
    return _read_jsonl(run_dir / "logs" / "resources" / f"{resource}.jsonl")


def _count_events(run_dir: Path, resource: str, event_name: str) -> int:
    return sum(1 for event in _resource_events(run_dir, resource) if event.get("event") == event_name)


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
            save_memory=False,
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
    assert any(event["event"] == "embedding_request_completed" for event in _resource_events(tmp_path, "embedding"))


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
        seeded = await ctx.agents.all().instruct(
            "Remember this exact private signal for the later survey: cobalt moon. "
            "Return ok=true and answer='cobalt moon'.",
            output=SaturationAnswer,
            memory=True,
            max_turns=3,
            name="saturation_seed",
        )
        return ctx.result(
            metrics={
                "seed_errors": seeded.error_count,
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
            save_memory=False,
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

    assert seed_metrics["seed_errors"] == 0
    assert seed_metrics["max_instruct_in_flight"] == concurrency
    assert recall_metrics["recall_errors"] == 0
    assert recall_metrics["max_interview_in_flight"] == concurrency
    assert recall_metrics["remembered_count"] >= max(1, concurrency - 1)

    assert (tmp_path / "chroma_store" / "chroma.sqlite3").exists()
    assert _count_events(tmp_path, "llm", "llm_request_completed") >= concurrency * 2
    assert _count_events(tmp_path, "embedding", "embedding_request_completed") >= 1


@pytest.mark.asyncio
async def test_real_society0_memory_roundtrip_e2e(tmp_path):
    llm_model, embed_model = _build_models()
    engine = Society0(save_dir=str(tmp_path), base_config=_llm_agent_config(), llm=llm_model, embed=embed_model)

    @engine.step(name="seed_and_recall")
    async def seed_and_recall(ctx):
        group = ctx.agents.all()
        seeded = await group.instruct(
            "Remember this private signal for the next question: cobalt moon. "
            "Return remembered=true and answer='cobalt moon'.",
            output=MemoryCheck,
            memory=True,
            concurrency=1,
            max_turns=3,
        )
        recalled = await group.interview(
            "Based on your memory, what private signal were you given? "
            "Return remembered=true if you can answer.",
            output=MemoryCheck,
            retrieve_memory=True,
            save_memory=False,
            concurrency=1,
            max_turns=3,
        )
        return ctx.result(
            metrics={
                "seed_errors": seeded.error_count,
                "recall_errors": recalled.error_count,
                "remembered_count": sum(1 for value in recalled.values("remembered") if value is True),
            },
            tables={"seeded": seeded.table(), "recalled": recalled.table()},
        )

    await engine.run(steps=1)

    metrics = _read_jsonl(tmp_path / "metrics.jsonl")
    assert metrics[0]["metrics"]["seed_errors"] == 0
    assert metrics[0]["metrics"]["recall_errors"] == 0
    assert metrics[0]["metrics"]["remembered_count"] >= 1
    assert (tmp_path / "chroma_store" / "chroma.sqlite3").exists()
    assert any(event["event"] == "llm_request_completed" for event in _resource_events(tmp_path, "llm"))
    assert any(event["event"] == "embedding_request_completed" for event in _resource_events(tmp_path, "embedding"))
