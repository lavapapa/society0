"""Primary Society0 runtime facade."""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any, Dict, Optional, Union

import yaml

from .async_utils import invoke_maybe_async
from .context_stack import ContextStack
from .core_data import World
from .environment import EnvironmentTickContext
from .function_registry import FunctionRegistry, register_environment_capabilities
from .logging import ExperimentLogContext
from .models import EmbedModel, LLMModel
from .llm_model_types import ModelProvider
from .persistence import PersistenceManager
from .schedule import CodeSchedule, StepFunction
from .transaction import EventLogger

logger = logging.getLogger(__name__)


class Society0:
    """Code-driven simulation engine for social simulation experiments."""

    def __init__(
        self,
        save_dir: str,
        *,
        base_config: Union[str, dict, None] = None,
        llm: Optional[LLMModel] = None,
        embed: Optional[EmbedModel] = None,
        checkpoint_every: int = 10,
        agent_concurrency: Optional[int] = None,
        log_state_changes: bool = False,
        experiment_log_context: Optional[ExperimentLogContext] = None,
        log_hooks: Optional[Iterable[Callable[[str, Dict[str, Any]], None]]] = None,
    ) -> None:
        self.save_dir = Path(save_dir)
        self.save_dir.mkdir(parents=True, exist_ok=True)
        self.config = self._load_config(base_config)
        self.llm_model = llm
        self.embed_model = embed
        self.checkpoint_every = max(1, int(checkpoint_every))
        self.agent_concurrency = self._validate_optional_concurrency(agent_concurrency)
        self.schedule = CodeSchedule()
        self.persistence_manager = PersistenceManager(str(self.save_dir))
        self.log_state_changes = bool(log_state_changes)
        self.event_logger = EventLogger(
            str(self.save_dir / "events.jsonl"),
            write_state_changes=self.log_state_changes,
        )
        self.log_context = experiment_log_context or ExperimentLogContext(
            self.save_dir / "logs",
            hooks=list(log_hooks or []),
        )
        self.registry = FunctionRegistry()
        self.current_world_state: Optional[World] = None
        self.is_initialized = False
        self._llm_manager = None
        self._embedding_manager = None
        self._model_provider = None

    def step(
        self,
        *,
        name: Optional[str] = None,
        params: Optional[Dict[str, Any]] = None,
    ):
        return self.schedule.step(name=name, params=params)

    def add_step(
        self,
        fn: StepFunction,
        *,
        name: Optional[str] = None,
        params: Optional[Dict[str, Any]] = None,
    ) -> StepFunction:
        return self.schedule.add_step(fn, name=name, params=params)

    async def run(self, steps: int) -> None:
        if steps < 0:
            raise ValueError("steps must be non-negative")
        await self._initialize()
        if not self.schedule.steps:
            raise RuntimeError("No code steps registered. Use @engine.step(...) before run().")

        assert self.current_world_state is not None
        world = self.current_world_state
        started = time.time()
        steps_path = self.save_dir / "steps.jsonl"
        metrics_path = self.save_dir / "metrics.jsonl"
        steps_path.touch(exist_ok=True)
        metrics_path.touch(exist_ok=True)
        (self.save_dir / "events.jsonl").touch(exist_ok=True)

        await self._save_checkpoint_file("checkpoint_000000.json")
        self._write_jsonl(
            self.save_dir / "events.jsonl",
            {
                "event": "run_started",
                "steps": steps,
                "step": world.step,
                "at": time.time(),
                "agent_concurrency": getattr(world, "_default_agent_concurrency", None),
                "agent_concurrency_source": getattr(world, "_default_agent_concurrency_source", None),
                "llm_concurrency": self.llm_model.concurrency if self.llm_model else None,
                "embed_concurrency": self.embed_model.concurrency if self.embed_model else None,
            },
        )

        failed_exc: Optional[BaseException] = None
        failure_info: Optional[Dict[str, Any]] = None
        completed_ticks = 0
        try:
            for tick in range(steps):
                env = world.get_environment()
                world.set_context_stack(ContextStack().push_step(f"step_{world.step}"))
                self.event_logger.set_context(step=world.step)
                tick_started = time.time()
                hook_ctx = EnvironmentTickContext(step=world.step, world=world, log=self.log_context)
                await invoke_maybe_async(env.before_tick, hook_ctx)
                step_entries = await self.schedule.execute_tick(
                    tick=world.step,
                    world=world,
                    log=self.log_context,
                    on_step_event=lambda payload: self._write_jsonl(self.save_dir / "events.jsonl", payload),
                )
                await invoke_maybe_async(env.after_tick, hook_ctx)
                tick_duration = time.time() - tick_started

                for entry in step_entries:
                    self._write_jsonl(steps_path, entry)
                    metrics = entry["result"].get("metrics") or {}
                    if metrics:
                        self._write_jsonl(
                            metrics_path,
                            {
                                "step": entry["step"],
                                "step_name": entry["step_name"],
                                "metrics": metrics,
                            },
                        )

                self._write_jsonl(
                    self.save_dir / "events.jsonl",
                    {
                        "event": "tick_completed",
                        "step": world.step,
                        "duration_sec": tick_duration,
                        "code_steps": len(step_entries),
                    },
                )
                world.advance_step()
                completed_ticks += 1
                if world.step % self.checkpoint_every == 0:
                    await self._save_checkpoint_file(f"checkpoint_{world.step:06d}.json")
        except BaseException as exc:
            failed_exc = exc
            failure_info = {
                "failed_step": world.step,
                "error": str(exc) or repr(exc),
                "error_type": type(exc).__name__,
            }
            self._write_jsonl(
                self.save_dir / "events.jsonl",
                {
                    "event": "run_failed",
                    "steps_requested": steps,
                    **failure_info,
                    "step": world.step,
                    "at": time.time(),
                },
            )
            raise
        finally:
            await self._save_checkpoint_file("checkpoint_final.json")
            total_time = time.time() - started
            if failed_exc is None:
                self._write_jsonl(
                    self.save_dir / "events.jsonl",
                    {
                        "event": "run_completed",
                        "steps_requested": steps,
                        "final_step": world.step,
                        "step": world.step,
                        "duration_sec": total_time,
                        "at": time.time(),
                    },
                )
            await self._save_summary(
                steps_requested=steps,
                steps_completed=completed_ticks,
                total_time=total_time,
                failure=failure_info,
            )
            self.event_logger.close()
            self.persistence_manager.close()
            await self._close_model_managers()

    async def _initialize(self) -> None:
        if self.is_initialized:
            return
        self._initialize_models()
        world = self._create_initial_world()
        default_concurrency, concurrency_source = self._resolve_default_agent_concurrency()
        world._default_agent_concurrency = default_concurrency
        world._default_agent_concurrency_source = concurrency_source
        world.set_log_context(self.log_context)
        world.set_function_registry(self.registry)
        world.set_persistence_manager(self.persistence_manager)
        if self._llm_manager or self._embedding_manager:
            world.set_resource_managers(
                llm_manager=self._llm_manager,
                embedding_manager=self._embedding_manager,
            )
        if self._model_provider is not None:
            world.set_model_provider(self._model_provider)

        env = world.get_environment()
        self._register_environment_functions(env)
        if hasattr(env, "set_resource_handles"):
            vector_client = self.persistence_manager.get_chroma_client()
            embed_call = self._embedding_manager.request if self._embedding_manager else None
            env.set_resource_handles(embed_call=embed_call, vector_client=vector_client)

        has_llm_agents = any(data.get("archetype") == "llm" for data in world.agents_data.values())
        if has_llm_agents and self._llm_manager is None:
            raise ValueError("LLM agents require Society0(..., llm=LLMModel...)")
        if has_llm_agents and self._embedding_manager is None:
            raise ValueError("LLM agents require Society0(..., embed=EmbedModel...) for memory and embeddings")
        if has_llm_agents:
            world.initialize_all_cognitive_systems(
                llm_call=self._llm_manager.request,
                memory_uri=str(self.save_dir / "chroma_store"),
                model_provider=self._model_provider,
                embedding_dim=self.embed_model.dimensions if self.embed_model else None,
                strict=True,
                require_memory=True,
            )

        self.current_world_state = world
        self.event_logger.open()
        self.is_initialized = True

    def _resolve_default_agent_concurrency(self) -> tuple[int, str]:
        if self.agent_concurrency is not None:
            return self.agent_concurrency, "society0"
        if self.llm_model is not None:
            try:
                value = int(self.llm_model.concurrency)
            except (TypeError, ValueError):
                value = 0
            if value > 0:
                return value, "llm_model"
        return 5, "default"

    def _initialize_models(self) -> None:
        if self.llm_model is not None:
            runtime, manager = self.llm_model.build_runtime(log_context=self.log_context)
            self._llm_manager = manager
            self._model_provider = ModelProvider(
                models={self.llm_model.id: runtime},
                default_model_id=self.llm_model.id,
            )
        if self.embed_model is not None:
            self._embedding_manager = self.embed_model.build_manager(log_context=self.log_context)

    def _create_initial_world(self) -> World:
        world = World(
            step=0,
            event_log_path=str(self.save_dir / "events.jsonl"),
            event_logger=self.event_logger,
        )

        agent_types = self.config.get("agent_types") or self.config.get("types") or []
        type_index = {item.get("id"): item for item in agent_types if isinstance(item, dict) and item.get("id")}
        world._agent_types = type_index

        for agent_config in self.config.get("agents", []) or []:
            if not isinstance(agent_config, dict):
                continue
            agent_id = agent_config.get("id")
            if not agent_id:
                continue
            type_ref = agent_config.get("type") or agent_config.get("agent_type") or "unknown"
            type_meta = type_index.get(type_ref, {})
            archetype = agent_config.get("archetype") or type_meta.get("archetype") or "rule"
            world.add_agent_data(agent_id=agent_id, agent_type=type_ref, archetype=archetype)
            world.agents_data[agent_id]["state"].update(agent_config.get("state") or {})
            world.agents_data[agent_id]["properties"].update(agent_config.get("properties") or {})
            persona_instance = agent_config.get("persona") or ""
            persona_type = type_meta.get("persona", "") if isinstance(type_meta, dict) else ""
            world.agents_data[agent_id]["persona"] = persona_instance or persona_type or ""
            world.agents_data[agent_id]["persona_instance"] = persona_instance
            world.agents_data[agent_id]["persona_type"] = persona_type
            if agent_config.get("model"):
                world.agents_data[agent_id]["model"] = agent_config["model"]

        env_config = self.config.get("environment") or {}
        world.set_environment_type(env_config.get("type", "base"))
        world.environment_data["config"] = env_config.get("config", {})
        initial_state = env_config.get("state") or env_config.get("initial_state") or {}
        if isinstance(initial_state, dict):
            world.environment_data["state"].update(initial_state)
        world.environment_data["schema"] = env_config.get("state_schema", {})
        world.environment_data["globals"] = self.config.get("globals", env_config.get("globals", {}))
        return world

    def _register_environment_functions(self, environment: Any) -> None:
        env_meta = getattr(environment.__class__, "__env_meta__", None)
        if env_meta is not None:
            register_environment_capabilities(self.registry, env_meta, environment)

    async def _save_checkpoint_file(self, filename: str) -> None:
        if self.current_world_state is None:
            return
        self.persistence_manager._sync_chroma_to_store()
        path = self.persistence_manager.checkpoints_dir / filename
        world = self.current_world_state
        payload = {
            "step": world.step,
            "timestamp": time.time(),
            "agents_data": world.agents_data,
            "environment_data": world.environment_data,
            "world_state_summary": world.get_state_summary(),
        }
        tmp = path.with_suffix(path.suffix + ".tmp")
        with tmp.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"), default=str)
            handle.write("\n")
        tmp.replace(path)

    async def _save_summary(
        self,
        *,
        steps_requested: int,
        steps_completed: int,
        total_time: float,
        failure: Optional[Dict[str, Any]] = None,
    ) -> None:
        if self.current_world_state is None:
            return
        summary = {
            "steps_requested": steps_requested,
            "steps_run": steps_completed,
            "steps_completed": steps_completed,
            "total_time": total_time,
            "total_execution_time": total_time,
            "average_step_time": total_time / steps_completed if steps_completed else 0,
            "final_step": self.current_world_state.step,
            "world_state_summary": self.current_world_state.get_state_summary(),
            "code_steps": [step.name for step in self.schedule.steps],
            "failed": failure is not None,
            "runtime": {
                "agent_concurrency": getattr(self.current_world_state, "_default_agent_concurrency", None),
                "agent_concurrency_source": getattr(
                    self.current_world_state,
                    "_default_agent_concurrency_source",
                    None,
                ),
                "llm_concurrency": self.llm_model.concurrency if self.llm_model else None,
                "embed_concurrency": self.embed_model.concurrency if self.embed_model else None,
            },
            "agent_operations": self._summarize_agent_operations(),
            "resources": self._summarize_resource_calls(),
            "events": self._summarize_events(),
            "outputs": self._summarize_output_files(),
            "completed_at": time.time(),
        }
        if failure is not None:
            summary["failure"] = dict(failure)
        path = self.save_dir / "summary.json"
        with path.open("w", encoding="utf-8") as handle:
            json.dump(summary, handle, ensure_ascii=False, indent=2, default=str)
            handle.write("\n")

    def _summarize_agent_operations(self) -> Dict[str, Dict[str, Any]]:
        """Aggregate agent-facing step outputs into a researcher-readable summary."""
        path = self.save_dir / "steps.jsonl"
        if not path.exists():
            return {}

        summary: Dict[str, Dict[str, Any]] = {}

        def bucket_for(step_name: str) -> Dict[str, Any]:
            return summary.setdefault(
                step_name,
                {
                    "agent_count": 0,
                    "success_count": 0,
                    "error_count": 0,
                    "turns_total": 0,
                    "turns_count": 0,
                    "turns_max": 0,
                    "action_counts": {},
                    "action_error_count": 0,
                    "by_tick": {},
                    "_seen_action_keys": set(),
                    "_unique_agents": set(),
                    "_slowest_agents_by_turns": [],
                    "_error_samples": [],
                },
            )

        def stable_json(value: Any) -> str:
            try:
                return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
            except TypeError:
                return str(value)

        def tick_bucket_for(bucket: Dict[str, Any], tick: str) -> Dict[str, Any]:
            return bucket["by_tick"].setdefault(
                tick,
                {
                    "agent_count": 0,
                    "success_count": 0,
                    "error_count": 0,
                    "turns_total": 0,
                    "turns_count": 0,
                    "turns_max": 0,
                    "action_counts": {},
                    "action_error_count": 0,
                },
            )

        def count_action(
            bucket: Dict[str, Any],
            action: Dict[str, Any],
            *,
            agent_id: Optional[str] = None,
            tick: Optional[str] = None,
        ) -> None:
            action_name = action.get("action_name") or action.get("name") or action.get("action")
            if not action_name:
                return
            action_key = str(action_name)
            call_id = action.get("call_id") or action.get("id")
            if call_id:
                dedupe_key = f"call:{call_id}"
            else:
                dedupe_key = "fallback:" + stable_json(
                    {
                        "tick": tick,
                        "agent_id": agent_id or action.get("agent_id"),
                        "action_name": action_key,
                        "arguments": action.get("arguments"),
                        "result": action.get("result"),
                        "status": action.get("status"),
                    }
                )
            seen = bucket["_seen_action_keys"]
            if dedupe_key in seen:
                return
            seen.add(dedupe_key)
            action_counts = bucket["action_counts"]
            action_counts[action_key] = action_counts.get(action_key, 0) + 1
            if action.get("status") and action.get("status") != "success":
                bucket["action_error_count"] += 1
                error_samples = bucket["_error_samples"]
                if len(error_samples) < 5:
                    error_samples.append(
                        {
                            "tick": tick,
                            "agent_id": agent_id or action.get("agent_id"),
                            "action_name": action_key,
                            "status": action.get("status"),
                            "error": action.get("error") or action.get("result"),
                        }
                    )
            if tick is not None:
                tick_bucket = tick_bucket_for(bucket, tick)
                tick_actions = tick_bucket["action_counts"]
                tick_actions[action_key] = tick_actions.get(action_key, 0) + 1
                if action.get("status") and action.get("status") != "success":
                    tick_bucket["action_error_count"] += 1

        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                step_name = str(entry.get("step_name") or "unknown_step")
                tick = str(entry.get("step", "unknown"))
                result = entry.get("result") if isinstance(entry.get("result"), dict) else {}
                tables = result.get("tables") if isinstance(result, dict) else {}
                if not isinstance(tables, dict):
                    continue
                bucket = bucket_for(step_name)
                tick_bucket = tick_bucket_for(bucket, tick)

                for rows in tables.values():
                    if not isinstance(rows, list):
                        continue
                    for row in rows:
                        if not isinstance(row, dict):
                            continue
                        action_name = row.get("action_name")
                        if action_name:
                            count_action(bucket, row, agent_id=row.get("agent_id"), tick=tick)
                            continue

                        agent_id = row.get("agent_id")
                        if not agent_id or "status" not in row:
                            continue
                        agent_key = str(agent_id)
                        bucket["_unique_agents"].add(agent_key)
                        bucket["agent_count"] += 1
                        tick_bucket["agent_count"] += 1
                        if row.get("status") == "success":
                            bucket["success_count"] += 1
                            tick_bucket["success_count"] += 1
                        else:
                            bucket["error_count"] += 1
                            tick_bucket["error_count"] += 1
                            error_samples = bucket["_error_samples"]
                            if len(error_samples) < 5:
                                error_samples.append(
                                    {
                                        "tick": tick,
                                        "agent_id": agent_key,
                                        "status": row.get("status"),
                                        "error": row.get("error"),
                                    }
                                )

                        total_turns = row.get("total_turns")
                        if isinstance(total_turns, (int, float)):
                            turns = int(total_turns)
                            bucket["turns_total"] += turns
                            bucket["turns_count"] += 1
                            bucket["turns_max"] = max(bucket["turns_max"], turns)
                            tick_bucket["turns_total"] += turns
                            tick_bucket["turns_count"] += 1
                            tick_bucket["turns_max"] = max(tick_bucket["turns_max"], turns)
                            slow_entry = {
                                "agent_id": agent_key,
                                "total_turns": turns,
                                "status": row.get("status"),
                            }
                            if row.get("error"):
                                slow_entry["error"] = row.get("error")
                            slowest = bucket["_slowest_agents_by_turns"]
                            slowest.append(slow_entry)
                            slowest.sort(key=lambda item: item.get("total_turns", 0), reverse=True)
                            del slowest[5:]

                        nested_actions = row.get("actions")
                        if isinstance(nested_actions, list):
                            for action in nested_actions:
                                if isinstance(action, dict):
                                    count_action(bucket, action, agent_id=agent_key, tick=tick)

        finalized: Dict[str, Dict[str, Any]] = {}
        for step_name, bucket in summary.items():
            turns_count = bucket.pop("turns_count", 0)
            turns_total = bucket.pop("turns_total", 0)
            unique_agents = bucket.pop("_unique_agents", set())
            slowest_agents = bucket.pop("_slowest_agents_by_turns", [])
            error_samples = bucket.pop("_error_samples", [])
            bucket.pop("_seen_action_keys", None)
            if not bucket["agent_count"] and not bucket["action_counts"]:
                continue
            bucket["unique_agent_count"] = len(unique_agents)
            if turns_count:
                bucket["turns_avg"] = round(turns_total / turns_count, 6)
            else:
                bucket["turns_avg"] = 0.0
            bucket["action_counts"] = dict(sorted(bucket["action_counts"].items()))
            bucket["slowest_agents_by_turns"] = slowest_agents
            if error_samples:
                bucket["error_samples"] = error_samples
            by_tick = bucket.get("by_tick", {})
            for tick_bucket in by_tick.values():
                tick_turns_count = tick_bucket.pop("turns_count", 0)
                tick_turns_total = tick_bucket.pop("turns_total", 0)
                tick_bucket["turns_avg"] = (
                    round(tick_turns_total / tick_turns_count, 6)
                    if tick_turns_count
                    else 0.0
                )
                tick_bucket["action_counts"] = dict(sorted(tick_bucket["action_counts"].items()))
            bucket["by_tick"] = dict(sorted(by_tick.items(), key=lambda item: item[0]))
            finalized[step_name] = bucket
        self._attach_resource_calls_to_agent_operations(finalized)
        return finalized

    def _attach_resource_calls_to_agent_operations(self, operations: Dict[str, Dict[str, Any]]) -> None:
        """Attach resource-call cost attribution to agent operation summaries.

        Agent operation rows come from user-designed step tables. Resource call
        traces are written separately by model managers. Joining them by code
        step keeps summary.json useful for runtime explanation without forcing
        agents to scan resource_calls.jsonl for basic attribution.
        """
        if not operations:
            return

        path = self.save_dir / "resource_calls.jsonl"
        if not path.exists():
            return

        def operation_resource_bucket(operation: Dict[str, Any], resource_type: str) -> Dict[str, Any]:
            resources = operation.setdefault("resources", {})
            return resources.setdefault(resource_type, self._new_operation_resource_bucket())

        def tick_resource_bucket(operation: Dict[str, Any], tick: str, resource_type: str) -> Dict[str, Any]:
            tick_bucket = operation.setdefault("by_tick", {}).setdefault(
                tick,
                {
                    "agent_count": 0,
                    "success_count": 0,
                    "error_count": 0,
                    "turns_max": 0,
                    "action_counts": {},
                    "action_error_count": 0,
                    "turns_avg": 0.0,
                },
            )
            resources = tick_bucket.setdefault("resources", {})
            return resources.setdefault(resource_type, self._new_operation_resource_bucket())

        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if record.get("status") == "started":
                    continue
                resource_type = record.get("resource_type")
                if not resource_type:
                    continue
                step_names = self._resource_record_step_names(record)
                if not step_names:
                    continue
                tick = str(record.get("step", "unknown"))
                for step_name in step_names:
                    operation = operations.get(step_name)
                    if operation is None:
                        continue
                    self._accumulate_operation_resource(
                        operation_resource_bucket(operation, str(resource_type)),
                        record,
                    )
                    self._accumulate_operation_resource(
                        tick_resource_bucket(operation, tick, str(resource_type)),
                        record,
                    )

        for operation in operations.values():
            self._finalize_operation_resource_map(operation.get("resources"))
            for tick_bucket in operation.get("by_tick", {}).values():
                self._finalize_operation_resource_map(tick_bucket.get("resources"))

    @staticmethod
    def _new_operation_resource_bucket() -> Dict[str, Any]:
        return {
            "call_count": 0,
            "error_count": 0,
            "duration_sec_total": 0.0,
            "provider_duration_sec_total": 0.0,
            "queue_duration_sec_total": 0.0,
            "input_characters": 0,
            "tools_characters": 0,
            "payload_characters": 0,
            "messages_count_total": 0,
            "messages_count_max": 0,
            "tools_count_total": 0,
            "tools_count_max": 0,
            "texts_count": 0,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "slowest_calls": [],
        }

    @staticmethod
    def _resource_record_step_names(record: Dict[str, Any]) -> list[str]:
        values: list[str] = []
        step_name = record.get("step_name")
        if step_name is not None:
            values.append(str(step_name))
        step_names = record.get("step_names")
        if isinstance(step_names, list):
            values.extend(str(item) for item in step_names if item is not None)
        return list(dict.fromkeys(values))

    def _accumulate_operation_resource(self, bucket: Dict[str, Any], record: Dict[str, Any]) -> None:
        bucket["call_count"] += 1
        if record.get("status") and record.get("status") != "success":
            bucket["error_count"] += 1

        for key in ("duration_sec", "provider_duration_sec", "queue_duration_sec"):
            value = record.get(key)
            if isinstance(value, (int, float)):
                bucket[f"{key}_total"] += float(value)

        for key in (
            "input_characters",
            "tools_characters",
            "payload_characters",
            "texts_count",
            "prompt_tokens",
            "completion_tokens",
            "total_tokens",
        ):
            value = record.get(key)
            if isinstance(value, int):
                bucket[key] += value

        messages_count = record.get("messages_count")
        if isinstance(messages_count, int):
            bucket["messages_count_total"] += messages_count
            bucket["messages_count_max"] = max(bucket["messages_count_max"], messages_count)
        tools_count = record.get("tools_count")
        if isinstance(tools_count, int):
            bucket["tools_count_total"] += tools_count
            bucket["tools_count_max"] = max(bucket["tools_count_max"], tools_count)

        duration = record.get("duration_sec")
        if isinstance(duration, (int, float)):
            slow_call = {
                "duration_sec": float(duration),
                "status": record.get("status"),
                "agent_id": record.get("agent_id"),
                "agent_ids": record.get("agent_ids"),
                "interaction_type": record.get("interaction_type"),
                "interaction_types": record.get("interaction_types"),
                "interaction_name": record.get("interaction_name"),
                "interaction_names": record.get("interaction_names"),
                "input_characters": record.get("input_characters"),
                "tools_characters": record.get("tools_characters"),
                "payload_characters": record.get("payload_characters"),
                "messages_count": record.get("messages_count"),
                "tools_count": record.get("tools_count"),
                "texts_count": record.get("texts_count"),
                "total_tokens": record.get("total_tokens"),
                "error_type": record.get("error_type"),
            }
            slowest = bucket["slowest_calls"]
            slowest.append({key: value for key, value in slow_call.items() if value is not None})
            slowest.sort(key=lambda item: item.get("duration_sec", 0), reverse=True)
            del slowest[3:]

    @staticmethod
    def _finalize_operation_resource_map(resource_map: Optional[Dict[str, Dict[str, Any]]]) -> None:
        if not resource_map:
            return
        for bucket in resource_map.values():
            call_count = bucket.get("call_count", 0)
            for key in ("duration_sec_total", "provider_duration_sec_total", "queue_duration_sec_total"):
                bucket[key] = round(float(bucket.get(key, 0.0)), 6)
            bucket["duration_sec_avg"] = (
                round(bucket["duration_sec_total"] / call_count, 6)
                if call_count
                else 0.0
            )
            bucket["provider_duration_sec_avg"] = (
                round(bucket["provider_duration_sec_total"] / call_count, 6)
                if call_count
                else 0.0
            )
            bucket["queue_duration_sec_avg"] = (
                round(bucket["queue_duration_sec_total"] / call_count, 6)
                if call_count
                else 0.0
            )
            bucket["messages_count_avg"] = (
                round(bucket["messages_count_total"] / call_count, 6)
                if call_count
                else 0.0
            )
            bucket["tools_count_avg"] = (
                round(bucket["tools_count_total"] / call_count, 6)
                if call_count
                else 0.0
            )
            bucket["total_duration_sec"] = bucket["duration_sec_total"]
            bucket["total_provider_duration_sec"] = bucket["provider_duration_sec_total"]
            bucket["total_queue_duration_sec"] = bucket["queue_duration_sec_total"]
            bucket["total_input_characters"] = bucket["input_characters"]
            bucket["total_tools_characters"] = bucket["tools_characters"]
            bucket["total_payload_characters"] = bucket["payload_characters"]
            bucket["slowest_calls"] = [
                {
                    **item,
                    "duration_sec": round(float(item["duration_sec"]), 6),
                }
                for item in bucket.get("slowest_calls", [])
            ]

    def _summarize_resource_calls(self) -> Dict[str, Dict[str, Any]]:
        """Aggregate resource call traces for the run summary."""
        path = self.save_dir / "resource_calls.jsonl"
        if not path.exists():
            return {}

        def percentile(values: list[float], fraction: float) -> float:
            if not values:
                return 0.0
            ordered = sorted(values)
            index = round((len(ordered) - 1) * fraction)
            return float(ordered[int(index)])

        def compact_slow_call(record: Dict[str, Any]) -> Dict[str, Any]:
            keys = (
                "duration_sec",
                "status",
                "step",
                "step_name",
                "step_names",
                "interaction_type",
                "interaction_types",
                "interaction_name",
                "interaction_names",
                "agent_id",
                "agent_ids",
                "input_characters",
                "tools_characters",
                "payload_characters",
                "messages_count",
                "tools_count",
                "texts_count",
                "max_tokens",
                "temperature",
                "top_p",
                "queue_duration_sec",
                "provider_duration_sec",
                "prompt_tokens",
                "completion_tokens",
                "total_tokens",
                "error_type",
                "error_preview",
            )
            compact = {key: record.get(key) for key in keys if record.get(key) is not None}
            for key, value in record.items():
                if (
                    isinstance(key, str)
                    and (key.endswith("_id") or key.endswith("_ids"))
                    and value is not None
                ):
                    compact.setdefault(key, value)
            return compact

        def interaction_key(record: Dict[str, Any]) -> str:
            step_name = record.get("step_name")
            if step_name is None and isinstance(record.get("step_names"), list):
                step_name = ",".join(str(item) for item in record["step_names"][:3])
            interaction_type = record.get("interaction_type")
            if interaction_type is None and isinstance(record.get("interaction_types"), list):
                interaction_type = ",".join(str(item) for item in record["interaction_types"][:3])
            interaction_name = record.get("interaction_name")
            if interaction_name is None and isinstance(record.get("interaction_names"), list):
                interaction_name = ",".join(str(item) for item in record["interaction_names"][:3])
            return " / ".join(
                str(part)
                for part in (step_name or "unknown_step", interaction_type or "unknown_type", interaction_name or "unknown_name")
            )

        summary: Dict[str, Dict[str, Any]] = {}
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                resource_type = record.get("resource_type")
                if not resource_type:
                    continue
                bucket = summary.setdefault(
                    str(resource_type),
                    {
                        "started_count": 0,
                        "call_count": 0,
                        "terminal_count": 0,
                        "incomplete_count": 0,
                        "error_count": 0,
                        "duration_sec_total": 0.0,
                        "duration_sec_max": 0.0,
                        "queue_duration_sec_total": 0.0,
                        "queue_duration_sec_max": 0.0,
                        "provider_duration_sec_total": 0.0,
                        "provider_duration_sec_max": 0.0,
                        "input_characters": 0,
                        "tools_characters": 0,
                        "payload_characters": 0,
                        "messages_count_total": 0,
                        "messages_count_max": 0,
                        "tools_count_total": 0,
                        "tools_count_max": 0,
                        "prompt_tokens": 0,
                        "completion_tokens": 0,
                        "total_tokens": 0,
                        "texts_count": 0,
                "_durations": [],
                "_slowest_calls": [],
                "_error_samples": [],
                "_by_interaction": {},
                "_by_tick": {},
                "_started_request_ids": set(),
                "_terminal_request_ids": set(),
            },
        )
                request_id = record.get("request_id")
                if record.get("status") == "started":
                    bucket["started_count"] += 1
                    if request_id:
                        bucket["_started_request_ids"].add(str(request_id))
                    tick = str(record.get("step", "unknown"))
                    tick_bucket = bucket["_by_tick"].setdefault(
                        tick,
                        {
                            "started_count": 0,
                            "call_count": 0,
                            "error_count": 0,
                            "duration_sec_total": 0.0,
                            "provider_duration_sec_total": 0.0,
                            "queue_duration_sec_total": 0.0,
                            "input_characters": 0,
                            "tools_characters": 0,
                            "payload_characters": 0,
                            "messages_count_total": 0,
                            "messages_count_max": 0,
                            "tools_count_total": 0,
                            "tools_count_max": 0,
                            "prompt_tokens": 0,
                            "completion_tokens": 0,
                            "total_tokens": 0,
                            "texts_count": 0,
                        },
                    )
                    tick_bucket["started_count"] += 1
                    continue

                bucket["call_count"] += 1
                bucket["terminal_count"] += 1
                if request_id:
                    bucket["_terminal_request_ids"].add(str(request_id))
                tick = str(record.get("step", "unknown"))
                tick_bucket = bucket["_by_tick"].setdefault(
                    tick,
                    {
                        "started_count": 0,
                        "call_count": 0,
                        "error_count": 0,
                        "duration_sec_total": 0.0,
                        "provider_duration_sec_total": 0.0,
                        "queue_duration_sec_total": 0.0,
                        "input_characters": 0,
                        "tools_characters": 0,
                        "payload_characters": 0,
                        "messages_count_total": 0,
                        "messages_count_max": 0,
                        "tools_count_total": 0,
                        "tools_count_max": 0,
                        "prompt_tokens": 0,
                        "completion_tokens": 0,
                        "total_tokens": 0,
                        "texts_count": 0,
                    },
                )
                tick_bucket["call_count"] += 1
                if record.get("status") and record.get("status") != "success":
                    bucket["error_count"] += 1
                    tick_bucket["error_count"] += 1
                    error_samples = bucket["_error_samples"]
                    if len(error_samples) < 5:
                        error_samples.append(compact_slow_call(record))
                duration = record.get("duration_sec")
                if isinstance(duration, (int, float)):
                    duration_float = float(duration)
                    bucket["duration_sec_total"] += duration_float
                    bucket["duration_sec_max"] = max(bucket["duration_sec_max"], duration_float)
                    bucket["_durations"].append(duration_float)
                    tick_bucket["duration_sec_total"] += duration_float
                    slowest = bucket["_slowest_calls"]
                    slowest.append(compact_slow_call(record))
                    slowest.sort(key=lambda item: item.get("duration_sec", 0), reverse=True)
                    del slowest[5:]
                for timing_key in ("queue_duration_sec", "provider_duration_sec"):
                    timing_value = record.get(timing_key)
                    if isinstance(timing_value, (int, float)):
                        timing_float = float(timing_value)
                        bucket[f"{timing_key}_total"] += timing_float
                        bucket[f"{timing_key}_max"] = max(bucket[f"{timing_key}_max"], timing_float)
                        tick_bucket[f"{timing_key}_total"] += timing_float
                for key in (
                    "prompt_tokens",
                    "completion_tokens",
                    "total_tokens",
                    "texts_count",
                    "tools_characters",
                    "payload_characters",
                ):
                    value = record.get(key)
                    if isinstance(value, int):
                        bucket[key] += value
                        tick_bucket[key] += value
                input_characters = record.get("input_characters")
                if isinstance(input_characters, int):
                    bucket["input_characters"] += input_characters
                    tick_bucket["input_characters"] += input_characters
                messages_count = record.get("messages_count")
                if isinstance(messages_count, int):
                    bucket["messages_count_total"] += messages_count
                    bucket["messages_count_max"] = max(bucket["messages_count_max"], messages_count)
                    tick_bucket["messages_count_total"] += messages_count
                    tick_bucket["messages_count_max"] = max(tick_bucket["messages_count_max"], messages_count)
                tools_count = record.get("tools_count")
                if isinstance(tools_count, int):
                    bucket["tools_count_total"] += tools_count
                    bucket["tools_count_max"] = max(bucket["tools_count_max"], tools_count)
                    tick_bucket["tools_count_total"] += tools_count
                    tick_bucket["tools_count_max"] = max(tick_bucket["tools_count_max"], tools_count)

                by_interaction = bucket["_by_interaction"]
                interaction_bucket = by_interaction.setdefault(
                    interaction_key(record),
                    {
                        "call_count": 0,
                        "error_count": 0,
                        "duration_sec_total": 0.0,
                        "duration_sec_max": 0.0,
                        "queue_duration_sec_total": 0.0,
                        "queue_duration_sec_max": 0.0,
                        "provider_duration_sec_total": 0.0,
                        "provider_duration_sec_max": 0.0,
                        "input_characters": 0,
                        "tools_characters": 0,
                        "payload_characters": 0,
                        "messages_count_total": 0,
                        "messages_count_max": 0,
                        "tools_count_total": 0,
                        "tools_count_max": 0,
                        "prompt_tokens": 0,
                        "completion_tokens": 0,
                        "total_tokens": 0,
                        "texts_count": 0,
                    },
                )
                interaction_bucket["call_count"] += 1
                if record.get("status") and record.get("status") != "success":
                    interaction_bucket["error_count"] += 1
                if isinstance(duration, (int, float)):
                    duration_float = float(duration)
                    interaction_bucket["duration_sec_total"] += duration_float
                    interaction_bucket["duration_sec_max"] = max(
                        interaction_bucket["duration_sec_max"],
                        duration_float,
                    )
                for timing_key in ("queue_duration_sec", "provider_duration_sec"):
                    timing_value = record.get(timing_key)
                    if isinstance(timing_value, (int, float)):
                        timing_float = float(timing_value)
                        interaction_bucket[f"{timing_key}_total"] += timing_float
                        interaction_bucket[f"{timing_key}_max"] = max(
                            interaction_bucket[f"{timing_key}_max"],
                            timing_float,
                        )
                for key in (
                    "prompt_tokens",
                    "completion_tokens",
                    "total_tokens",
                    "texts_count",
                    "tools_characters",
                    "payload_characters",
                ):
                    value = record.get(key)
                    if isinstance(value, int):
                        interaction_bucket[key] += value
                if isinstance(input_characters, int):
                    interaction_bucket["input_characters"] += input_characters
                if isinstance(messages_count, int):
                    interaction_bucket["messages_count_total"] += messages_count
                    interaction_bucket["messages_count_max"] = max(
                        interaction_bucket["messages_count_max"],
                        messages_count,
                    )
                if isinstance(tools_count, int):
                    interaction_bucket["tools_count_total"] += tools_count
                    interaction_bucket["tools_count_max"] = max(
                        interaction_bucket["tools_count_max"],
                        tools_count,
                    )

        for bucket in summary.values():
            durations = bucket.pop("_durations", [])
            slowest_calls = bucket.pop("_slowest_calls", [])
            error_samples = bucket.pop("_error_samples", [])
            by_interaction = bucket.pop("_by_interaction", {})
            by_tick = bucket.pop("_by_tick", {})
            started_request_ids = bucket.pop("_started_request_ids", set())
            terminal_request_ids = bucket.pop("_terminal_request_ids", set())
            if started_request_ids:
                bucket["incomplete_count"] = len(started_request_ids - terminal_request_ids)
            else:
                bucket["incomplete_count"] = max(bucket["started_count"] - bucket["terminal_count"], 0)
            bucket["duration_sec_total"] = round(bucket["duration_sec_total"], 6)
            bucket["duration_sec_max"] = round(bucket["duration_sec_max"], 6)
            for timing_key in ("queue_duration_sec", "provider_duration_sec"):
                total_key = f"{timing_key}_total"
                max_key = f"{timing_key}_max"
                avg_key = f"{timing_key}_avg"
                bucket[total_key] = round(bucket[total_key], 6)
                bucket[max_key] = round(bucket[max_key], 6)
                bucket[avg_key] = round(
                    bucket[total_key] / bucket["call_count"],
                    6,
                ) if bucket["call_count"] else 0.0
            bucket["duration_sec_avg"] = round(
                bucket["duration_sec_total"] / bucket["call_count"],
                6,
            ) if bucket["call_count"] else 0.0
            bucket["messages_count_avg"] = round(
                bucket["messages_count_total"] / bucket["call_count"],
                6,
            ) if bucket["call_count"] else 0.0
            bucket["tools_count_avg"] = round(
                bucket["tools_count_total"] / bucket["call_count"],
                6,
            ) if bucket["call_count"] else 0.0
            bucket["duration_sec_p50"] = round(percentile(durations, 0.50), 6)
            bucket["duration_sec_p90"] = round(percentile(durations, 0.90), 6)
            bucket["duration_sec_p99"] = round(percentile(durations, 0.99), 6)
            bucket["total_duration_sec"] = bucket["duration_sec_total"]
            bucket["total_provider_duration_sec"] = bucket["provider_duration_sec_total"]
            bucket["total_queue_duration_sec"] = bucket["queue_duration_sec_total"]
            bucket["total_input_characters"] = bucket["input_characters"]
            bucket["total_tools_characters"] = bucket["tools_characters"]
            bucket["total_payload_characters"] = bucket["payload_characters"]
            bucket["slowest_calls"] = [
                {
                    **item,
                    "duration_sec": round(float(item["duration_sec"]), 6),
                }
                for item in slowest_calls
            ]
            if error_samples:
                bucket["error_samples"] = error_samples
            for tick_bucket in by_tick.values():
                for key in ("duration_sec_total", "queue_duration_sec_total", "provider_duration_sec_total"):
                    tick_bucket[key] = round(tick_bucket[key], 6)
                tick_bucket["duration_sec_avg"] = (
                    round(tick_bucket["duration_sec_total"] / tick_bucket["call_count"], 6)
                    if tick_bucket["call_count"]
                    else 0.0
                )
                tick_bucket["provider_duration_sec_avg"] = (
                    round(tick_bucket["provider_duration_sec_total"] / tick_bucket["call_count"], 6)
                    if tick_bucket["call_count"]
                    else 0.0
                )
                tick_bucket["queue_duration_sec_avg"] = (
                    round(tick_bucket["queue_duration_sec_total"] / tick_bucket["call_count"], 6)
                    if tick_bucket["call_count"]
                    else 0.0
                )
                tick_bucket["messages_count_avg"] = (
                    round(tick_bucket["messages_count_total"] / tick_bucket["call_count"], 6)
                    if tick_bucket["call_count"]
                    else 0.0
                )
                tick_bucket["tools_count_avg"] = (
                    round(tick_bucket["tools_count_total"] / tick_bucket["call_count"], 6)
                    if tick_bucket["call_count"]
                    else 0.0
                )
                tick_bucket["total_duration_sec"] = tick_bucket["duration_sec_total"]
                tick_bucket["total_provider_duration_sec"] = tick_bucket["provider_duration_sec_total"]
                tick_bucket["total_queue_duration_sec"] = tick_bucket["queue_duration_sec_total"]
                tick_bucket["total_input_characters"] = tick_bucket["input_characters"]
                tick_bucket["total_tools_characters"] = tick_bucket["tools_characters"]
                tick_bucket["total_payload_characters"] = tick_bucket["payload_characters"]
            bucket["by_tick"] = dict(sorted(by_tick.items(), key=lambda item: item[0]))
            for interaction_bucket in by_interaction.values():
                interaction_bucket["duration_sec_total"] = round(
                    interaction_bucket["duration_sec_total"],
                    6,
                )
                interaction_bucket["duration_sec_max"] = round(
                    interaction_bucket["duration_sec_max"],
                    6,
                )
                for timing_key in ("queue_duration_sec", "provider_duration_sec"):
                    total_key = f"{timing_key}_total"
                    max_key = f"{timing_key}_max"
                    avg_key = f"{timing_key}_avg"
                    interaction_bucket[total_key] = round(interaction_bucket[total_key], 6)
                    interaction_bucket[max_key] = round(interaction_bucket[max_key], 6)
                    interaction_bucket[avg_key] = round(
                        interaction_bucket[total_key] / interaction_bucket["call_count"],
                        6,
                    ) if interaction_bucket["call_count"] else 0.0
                interaction_bucket["duration_sec_avg"] = round(
                    interaction_bucket["duration_sec_total"] / interaction_bucket["call_count"],
                    6,
                ) if interaction_bucket["call_count"] else 0.0
                interaction_bucket["messages_count_avg"] = round(
                    interaction_bucket["messages_count_total"] / interaction_bucket["call_count"],
                    6,
                ) if interaction_bucket["call_count"] else 0.0
                interaction_bucket["tools_count_avg"] = round(
                    interaction_bucket["tools_count_total"] / interaction_bucket["call_count"],
                    6,
                ) if interaction_bucket["call_count"] else 0.0
                interaction_bucket["total_duration_sec"] = interaction_bucket["duration_sec_total"]
                interaction_bucket["total_provider_duration_sec"] = interaction_bucket["provider_duration_sec_total"]
                interaction_bucket["total_queue_duration_sec"] = interaction_bucket["queue_duration_sec_total"]
                interaction_bucket["total_input_characters"] = interaction_bucket["input_characters"]
                interaction_bucket["total_tools_characters"] = interaction_bucket["tools_characters"]
                interaction_bucket["total_payload_characters"] = interaction_bucket["payload_characters"]
            bucket["by_interaction"] = dict(
                sorted(
                    by_interaction.items(),
                    key=lambda item: item[1].get("duration_sec_total", 0),
                    reverse=True,
                )
            )
        return summary

    def _summarize_output_files(self) -> Dict[str, Any]:
        """Summarize run artifact sizes for monitoring and cleanup decisions."""

        def summarize_file(path: Path) -> Dict[str, Any]:
            record: Dict[str, Any] = {"bytes": path.stat().st_size}
            if path.suffix == ".jsonl":
                line_count = 0
                with path.open("r", encoding="utf-8") as handle:
                    for line in handle:
                        if line.strip():
                            line_count += 1
                record["line_count"] = line_count
            return record

        files: Dict[str, Dict[str, Any]] = {}
        total_bytes = 0
        for name in ("events.jsonl", "steps.jsonl", "metrics.jsonl", "resource_calls.jsonl"):
            path = self.save_dir / name
            if not path.exists():
                continue
            files[name] = summarize_file(path)
            total_bytes += files[name]["bytes"]

        checkpoints: Dict[str, Dict[str, Any]] = {}
        checkpoint_total = 0
        checkpoint_dir = self.save_dir / "checkpoints"
        if checkpoint_dir.exists():
            for path in sorted(checkpoint_dir.glob("checkpoint*.json")):
                checkpoints[path.name] = summarize_file(path)
                checkpoint_total += checkpoints[path.name]["bytes"]

        total_bytes += checkpoint_total
        result: Dict[str, Any] = {
            "total_bytes": total_bytes,
            "files": files,
            "checkpoints": {
                "count": len(checkpoints),
                "total_bytes": checkpoint_total,
                "files": checkpoints,
            },
        }
        return result

    def _summarize_events(self) -> Dict[str, Any]:
        """Summarize events.jsonl for progress monitoring and quick inspection."""
        path = self.save_dir / "events.jsonl"
        if not path.exists():
            return {}

        by_event: Dict[str, int] = {}
        by_tick: Dict[str, Dict[str, int]] = {}
        agent_batches: Dict[str, Dict[str, Any]] = {}
        action_counts: Dict[str, int] = {}
        error_samples: list[Dict[str, Any]] = []

        def event_name(record: Dict[str, Any]) -> str:
            return str(record.get("event") or record.get("event_type") or "unknown")

        def record_tick(record: Dict[str, Any]) -> str:
            if record.get("step") is not None:
                return str(record.get("step"))
            event_data = record.get("event_data")
            if isinstance(event_data, dict) and event_data.get("step") is not None:
                return str(event_data.get("step"))
            context = record.get("context")
            if isinstance(context, dict) and context.get("step_id") is not None:
                step_id = str(context.get("step_id"))
                return step_id.removeprefix("step_")
            return "unknown"

        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                name = event_name(record)
                by_event[name] = by_event.get(name, 0) + 1
                tick = record_tick(record)
                tick_events = by_tick.setdefault(tick, {})
                tick_events[name] = tick_events.get(name, 0) + 1

                event_data = record.get("event_data")
                if isinstance(event_data, dict):
                    action = event_data.get("action") or event_data.get("action_name")
                    if action:
                        action_key = str(action)
                        action_counts[action_key] = action_counts.get(action_key, 0) + 1
                    if name.startswith("agent_batch_"):
                        interaction = str(event_data.get("interaction_name") or "unknown")
                        batch = agent_batches.setdefault(
                            interaction,
                            {
                                "latest_event": name,
                                "agent_count": event_data.get("agent_count"),
                                "concurrency": event_data.get("concurrency"),
                                "success_count": 0,
                                "error_count": 0,
                                "completed_count": 0,
                                "duration_sec": 0.0,
                            },
                        )
                        batch["latest_event"] = name
                        for key in ("success_count", "error_count", "completed_count", "duration_sec"):
                            value = event_data.get(key)
                            if isinstance(value, (int, float)):
                                batch[key] = value
                    if event_data.get("error") and len(error_samples) < 5:
                        error_samples.append(
                            {
                                "event": name,
                                "tick": tick,
                                "error": event_data.get("error"),
                                "agent_id": event_data.get("agent_id"),
                                "action": event_data.get("action") or event_data.get("action_name"),
                            }
                        )

                if record.get("error") and len(error_samples) < 5:
                    error_samples.append(
                        {
                            "event": name,
                            "tick": tick,
                            "error": record.get("error"),
                            "error_type": record.get("error_type"),
                        }
                    )

        result: Dict[str, Any] = {
            "total_count": sum(by_event.values()),
            "by_event": dict(sorted(by_event.items())),
            "by_tick": dict(sorted(by_tick.items(), key=lambda item: item[0])),
        }
        if agent_batches:
            result["agent_batches"] = dict(sorted(agent_batches.items()))
        if action_counts:
            result["actions"] = dict(sorted(action_counts.items()))
        if error_samples:
            result["error_samples"] = error_samples
        return result

    async def _close_model_managers(self) -> None:
        if self._llm_manager is not None:
            await self._llm_manager.close()
        if self._embedding_manager is not None:
            await self._embedding_manager.close()

    @staticmethod
    def _write_jsonl(path: Path, payload: Dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")

    @staticmethod
    def _load_config(base_config: Union[str, dict, None]) -> Dict[str, Any]:
        if base_config is None:
            return {}
        if isinstance(base_config, dict):
            return dict(base_config)
        path = Path(base_config)
        if not path.exists():
            raise FileNotFoundError(base_config)
        with path.open("r", encoding="utf-8") as handle:
            if path.suffix.lower() in {".yaml", ".yml"}:
                return yaml.safe_load(handle) or {}
            if path.suffix.lower() == ".json":
                return json.load(handle)
        raise ValueError(f"Unsupported config format: {path.suffix}")

    @staticmethod
    def _validate_optional_concurrency(value: Optional[int]) -> Optional[int]:
        if value is None:
            return None
        parsed = int(value)
        if parsed <= 0:
            raise ValueError("agent_concurrency must be a positive integer")
        return parsed
