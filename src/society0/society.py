"""Primary Society0 runtime facade."""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any, Dict, Optional, Union

import yaml

from .context_stack import ContextStack
from .core_data import World
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
        self.event_logger = EventLogger(str(self.save_dir / "events.jsonl"))
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
                "at": time.time(),
                "agent_concurrency": getattr(world, "_default_agent_concurrency", None),
                "agent_concurrency_source": getattr(world, "_default_agent_concurrency_source", None),
                "llm_concurrency": self.llm_model.concurrency if self.llm_model else None,
                "embed_concurrency": self.embed_model.concurrency if self.embed_model else None,
            },
        )

        failed_exc: Optional[BaseException] = None
        try:
            for tick in range(steps):
                world.set_context_stack(ContextStack().push_step(f"step_{world.step}"))
                self.event_logger.set_context(step=world.step)
                tick_started = time.time()
                step_entries = await self.schedule.execute_tick(tick=world.step, world=world, log=self.log_context)
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
                if world.step % self.checkpoint_every == 0:
                    await self._save_checkpoint_file(f"checkpoint_{world.step:06d}.json")
        except BaseException as exc:
            failed_exc = exc
            self._write_jsonl(
                self.save_dir / "events.jsonl",
                {
                    "event": "run_failed",
                    "steps_requested": steps,
                    "failed_step": world.step,
                    "error": str(exc),
                    "error_type": type(exc).__name__,
                    "at": time.time(),
                },
            )
            raise
        finally:
            await self._save_checkpoint_file("checkpoint_final.json")
            total_time = time.time() - started
            await self._save_summary(steps, total_time)
            if failed_exc is None:
                self._write_jsonl(
                    self.save_dir / "events.jsonl",
                    {
                        "event": "run_completed",
                        "steps_requested": steps,
                        "final_step": world.step,
                        "duration_sec": total_time,
                        "at": time.time(),
                    },
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
        if has_llm_agents:
            world.initialize_all_cognitive_systems(
                llm_call=self._llm_manager.request,
                memory_uri=str(self.save_dir / "chroma_store"),
                model_provider=self._model_provider,
                embedding_dim=self.embed_model.dimensions if self.embed_model else None,
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
            json.dump(payload, handle, ensure_ascii=False, indent=2, default=str)
            handle.write("\n")
        tmp.replace(path)

    async def _save_summary(self, steps_run: int, total_time: float) -> None:
        if self.current_world_state is None:
            return
        summary = {
            "steps_run": steps_run,
            "total_execution_time": total_time,
            "average_step_time": total_time / steps_run if steps_run else 0,
            "final_step": self.current_world_state.step,
            "world_state_summary": self.current_world_state.get_state_summary(),
            "code_steps": [step.name for step in self.schedule.steps],
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
            "completed_at": time.time(),
        }
        path = self.save_dir / "summary.json"
        with path.open("w", encoding="utf-8") as handle:
            json.dump(summary, handle, ensure_ascii=False, indent=2, default=str)
            handle.write("\n")

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
