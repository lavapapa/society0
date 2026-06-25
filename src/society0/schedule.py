"""Code-driven scheduling and step DSL for Society0."""

from __future__ import annotations

import asyncio
import inspect
import random
import statistics
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, Iterable, Iterator, List, Optional

from .async_utils import invoke_maybe_async
from .core_data import BaseOperatorResult, ExecutionContext

StepFunction = Callable[["StepContext"], Awaitable[Optional["StepResult"]]]
ACTION_TEXT_PREVIEW_CHARS = 240


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(item) for item in value]
    if hasattr(value, "model_dump"):
        return _jsonable(value.model_dump())
    if hasattr(value, "dict"):
        return _jsonable(value.dict())
    if hasattr(value, "__dict__"):
        return _jsonable(vars(value))
    return str(value)


def _compact_action_text(text: str, *, limit: int = ACTION_TEXT_PREVIEW_CHARS) -> Dict[str, Any]:
    if len(text) <= limit:
        return {"value": text, "length": len(text), "truncated": False}
    return {
        "value": text[:limit].rstrip() + "...",
        "length": len(text),
        "truncated": True,
    }


def _compact_action_mapping(mapping: Dict[str, Any]) -> Dict[str, Any]:
    compact: Dict[str, Any] = {}
    for key, value in mapping.items():
        if isinstance(value, str):
            text_summary = _compact_action_text(value)
            compact[key] = text_summary["value"]
            if text_summary["truncated"]:
                compact[f"{key}_length"] = text_summary["length"]
                compact[f"{key}_truncated"] = True
        elif isinstance(value, dict):
            compact[key] = _compact_action_mapping(value)
        elif isinstance(value, (list, tuple, set)):
            compact[key] = [_jsonable(item) for item in value]
        else:
            compact[key] = _jsonable(value)
    return compact


@dataclass(slots=True)
class StepResult:
    metrics: Dict[str, Any] = field(default_factory=dict)
    tables: Dict[str, Any] = field(default_factory=dict)
    artifacts: Dict[str, Any] = field(default_factory=dict)
    observations: Dict[str, Any] = field(default_factory=dict)
    notes: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "metrics": _jsonable(self.metrics),
            "tables": _jsonable(self.tables),
            "artifacts": _jsonable(self.artifacts),
            "observations": _jsonable(self.observations),
            "notes": self.notes,
        }


@dataclass(slots=True)
class AgentCallRecord:
    agent_id: str
    status: str
    value: Any = None
    error: Optional[str] = None
    raw: Any = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "status": self.status,
            "value": _jsonable(self.value),
            "error": self.error,
            "raw": _jsonable(self.raw),
        }


@dataclass(slots=True)
class BatchProgressState:
    started_count: int
    completed_count: int
    in_flight_count: int
    pending_count: int
    active_agent_ids: List[str]


class AgentBatchResult:
    def __init__(self, records: Iterable[AgentCallRecord]):
        self.records = list(records)
        self._by_agent = {record.agent_id: record for record in self.records}

    @property
    def success_count(self) -> int:
        return sum(1 for record in self.records if record.status == "success")

    @property
    def error_count(self) -> int:
        return sum(1 for record in self.records if record.status != "success")

    def by_agent(self, agent_id: str) -> Optional[AgentCallRecord]:
        return self._by_agent.get(agent_id)

    def actions(self) -> List[Dict[str, Any]]:
        """Return normalized action calls across all agent records."""
        action_rows: List[Dict[str, Any]] = []
        for record in self.records:
            for action in _extract_actions_from_record(record):
                action_rows.append(action)
        return action_rows

    def actions_by_agent(self, agent_id: str) -> List[Dict[str, Any]]:
        """Return normalized action calls for one agent."""
        record = self.by_agent(agent_id)
        if record is None:
            return []
        return _extract_actions_from_record(record)

    def action_counts(self) -> Dict[str, int]:
        """Count action calls by action name."""
        counts: Dict[str, int] = {}
        for action in self.actions():
            name = action.get("action_name")
            if not name:
                continue
            counts[str(name)] = counts.get(str(name), 0) + 1
        return counts

    def table(self) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        for record in self.records:
            row = {"agent_id": record.agent_id, "status": record.status}
            if isinstance(record.value, dict):
                row.update(record.value)
            else:
                row["value"] = record.value
            if record.error:
                row["error"] = record.error
            rows.append(_jsonable(row))
        return rows

    def values(self, field: str) -> List[Any]:
        values: List[Any] = []
        for record in self.records:
            payload = record.value
            if isinstance(payload, dict) and field in payload:
                values.append(payload[field])
        return values

    def mean(self, field: str) -> Optional[float]:
        numeric = [value for value in self.values(field) if isinstance(value, (int, float))]
        if not numeric:
            return None
        return float(statistics.fmean(numeric))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "success_count": self.success_count,
            "error_count": self.error_count,
            "action_counts": self.action_counts(),
            "records": [record.to_dict() for record in self.records],
        }


class CapabilityCatalog:
    def __init__(self, world: Any):
        self.world = world

    def all(self) -> Dict[str, List[Dict[str, Any]]]:
        return {
            "fovs": self.fovs(),
            "actions": self.actions(),
            "rules": self.rules(),
            "behaviors": self.behaviors(),
        }

    def fovs(self) -> List[Dict[str, Any]]:
        return self.by_kind("fov")

    def actions(self) -> List[Dict[str, Any]]:
        return self.by_kind("action")

    def rules(self) -> List[Dict[str, Any]]:
        return self.by_kind("rule")

    def behaviors(self) -> List[Dict[str, Any]]:
        return self.by_kind("behavior")

    def names(self, kind: str) -> List[str]:
        return [entry["name"] for entry in self.by_kind(kind)]

    def has(self, kind: str, name: str) -> bool:
        return name in self.names(kind)

    def by_kind(self, kind: str) -> List[Dict[str, Any]]:
        registry = self.world.get_logic_provider()
        if kind == "fov":
            return _capability_entries(registry.env_fovs, "fov")
        if kind == "action":
            entries = _capability_entries(registry.env_agent_tools, "action")
            entries.extend(_capability_entries(registry.agent_actions, "action"))
            return _dedupe_capability_entries(entries)
        if kind == "rule":
            return _capability_entries(registry.rules, "rule")
        if kind == "behavior":
            return _capability_entries(registry.behaviors, "behavior")
        raise ValueError(f"Unknown capability kind: {kind}")


class AgentGroup:
    def __init__(self, world: Any, agent_ids: Iterable[str]):
        self.world = world
        self.agent_ids = list(dict.fromkeys(agent_ids))

    def __iter__(self) -> Iterator[Any]:
        for agent_id in self.agent_ids:
            yield self.world.get_agent(agent_id)

    def __len__(self) -> int:
        return len(self.agent_ids)

    def ids(self) -> List[str]:
        return list(self.agent_ids)

    async def behavior(
        self,
        behavior_name: str,
        *,
        concurrency: Optional[int] = None,
        name: Optional[str] = None,
        **params: Any,
    ) -> AgentBatchResult:
        resolved = _resolve_logic_entry(self.world, behavior_name, "behavior")
        if resolved is None:
            raise ValueError(_format_missing_logic_error(self.world, behavior_name, "behavior"))

        resolved_name, behavior_info = resolved
        behavior_func = behavior_info["function"]
        behavior_sig = behavior_info.get("signature") or inspect.signature(behavior_func)
        effective_concurrency = _resolve_non_llm_batch_concurrency(
            len(self.agent_ids),
            concurrency,
        )
        started = time.time()
        _record_logic_event(
            self.world,
            "logic_execution_started",
            logic_kind="behavior",
            logic_name=name or resolved_name,
            resolved_name=resolved_name,
            agent_count=len(self.agent_ids),
            concurrency=effective_concurrency,
            target_ids_sample=self.agent_ids[:5],
            param_keys=sorted(str(key) for key in params.keys()),
        )

        async def call(agent_id: str) -> AgentCallRecord:
            try:
                agent = self.world.get_agent(agent_id)
                env = self.world.get_environment()
                context = _build_direct_execution_context(
                    self.world,
                    caller=agent,
                    operator_id=name or resolved_name,
                )
                call_kwargs = _map_registered_call_kwargs(
                    behavior_sig,
                    params,
                    injections={
                        "agent": agent,
                        "env": env,
                        "environment": env,
                        "world": self.world,
                        "context": context,
                        "params": params,
                    },
                    callable_name=resolved_name,
                )
                result = await invoke_maybe_async(behavior_func, **call_kwargs)
                return AgentCallRecord(agent_id, "success", _extract_call_value(result), raw=result)
            except Exception as exc:
                return AgentCallRecord(agent_id, "error", error=str(exc))

        try:
            batch_result = AgentBatchResult(
                await _run_limited(self.agent_ids, call, effective_concurrency)
            )
        except Exception as exc:
            _record_logic_event(
                self.world,
                "logic_execution_failed",
                logic_kind="behavior",
                logic_name=name or resolved_name,
                resolved_name=resolved_name,
                agent_count=len(self.agent_ids),
                concurrency=effective_concurrency,
                target_ids_sample=self.agent_ids[:5],
                param_keys=sorted(str(key) for key in params.keys()),
                duration_sec=time.time() - started,
                error=str(exc),
                error_type=type(exc).__name__,
            )
            raise
        _record_logic_event(
            self.world,
            "logic_execution_completed",
            logic_kind="behavior",
            logic_name=name or resolved_name,
            resolved_name=resolved_name,
            agent_count=len(self.agent_ids),
            concurrency=effective_concurrency,
            target_ids_sample=self.agent_ids[:5],
            param_keys=sorted(str(key) for key in params.keys()),
            duration_sec=time.time() - started,
            success_count=batch_result.success_count,
            error_count=batch_result.error_count,
            error_samples=_logic_error_samples(batch_result.records),
        )
        return batch_result

    async def instruct(
        self,
        instruction: str,
        *,
        fovs: List[str] | None = None,
        actions: List[str] | None = None,
        output: Any = None,
        memory: bool = True,
        extract_memory: bool = True,
        model: Optional[str] = None,
        max_turns: int = 3,
        concurrency: Optional[int] = None,
        name: Optional[str] = None,
        reasoning_stages: Optional[List[Dict[str, Any]]] = None,
        memory_top_k: int = 10,
        terminal_actions: Optional[List[str]] = None,
        completion_action_tags: Optional[List[str]] = None,
        max_action_calls: Optional[int] = None,
        action_call_limits: Optional[Dict[str, int]] = None,
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
        top_p: Optional[float] = None,
        timeout: Optional[float] = None,
        llm_options: Optional[Dict[str, Any]] = None,
    ) -> AgentBatchResult:
        output_schema = _normalize_output_schema(output)
        llm_request_options = _build_llm_request_options(
            llm_options,
            max_tokens=max_tokens,
            temperature=temperature,
            top_p=top_p,
            timeout=timeout,
        )
        effective_extract_memory = bool(memory and extract_memory)
        execution_options = _agent_batch_execution_options(
            max_turns=max_turns,
            output_schema=output_schema,
            reasoning_stages=reasoning_stages,
            memory={
                "retrieve": memory,
                "save": memory,
                "extract": effective_extract_memory,
                "top_k": memory_top_k,
            },
            terminal_actions=terminal_actions,
            completion_action_tags=completion_action_tags,
            max_action_calls=max_action_calls,
            action_call_limits=action_call_limits,
            llm_request_options=llm_request_options,
        )

        async def call(agent_id: str) -> AgentCallRecord:
            try:
                result = await self.world.instruct_agent(
                    agent_id,
                    instruction,
                    fovs=fovs or [],
                    action_tags=actions,
                    current_step=self.world.step,
                    model_id=model,
                    output_schema=output_schema,
                    max_turns=max_turns,
                    retrieve_memory=memory,
                    save_memory=memory,
                    extract_memory=effective_extract_memory,
                    memory_top_k=memory_top_k,
                    name=name,
                    reasoning_stages=reasoning_stages,
                    terminal_action_names=terminal_actions,
                    completion_action_tags=completion_action_tags,
                    max_action_calls=max_action_calls,
                    action_call_limits=action_call_limits,
                    llm_request_options=llm_request_options,
                )
                failure_record = _agent_failure_record(
                    agent_id,
                    result,
                    require_structured_output=output_schema is not None,
                )
                if failure_record is not None:
                    return failure_record
                return AgentCallRecord(agent_id, "success", _extract_call_value(result), raw=result)
            except Exception as exc:
                return AgentCallRecord(agent_id, "error", error=str(exc))

        effective_concurrency = _resolve_agent_call_concurrency(
            self.world,
            explicit_concurrency=concurrency,
            model_id=model,
        )
        batch_started = time.time()
        _record_agent_batch_event(
            self.world,
            "agent_batch_started",
            interaction_type="instruct",
            interaction_name=name,
            agent_count=len(self.agent_ids),
            concurrency=effective_concurrency,
            model_id=model,
            fovs=fovs or [],
            actions=actions,
            target_ids_sample=self.agent_ids[:5],
            execution_options=execution_options,
        )
        completed_count = 0
        success_count = 0
        error_count = 0

        def record_heartbeat(active_agent_ids: List[str], started_count: int) -> None:
            _record_agent_batch_event(
                self.world,
                "agent_batch_heartbeat",
                interaction_type="instruct",
                interaction_name=name,
                agent_count=len(self.agent_ids),
                concurrency=effective_concurrency,
                model_id=model,
                fovs=fovs or [],
                actions=actions,
                target_ids_sample=self.agent_ids[:5],
                execution_options=execution_options,
                duration_sec=time.time() - batch_started,
                success_count=success_count,
                error_count=error_count,
                completed_count=completed_count,
                started_count=started_count,
                in_flight_count=len(active_agent_ids),
                pending_count=max(len(self.agent_ids) - started_count, 0),
                running_agent_ids_sample=active_agent_ids[:5],
            )

        def record_progress(record: AgentCallRecord, state: BatchProgressState) -> None:
            nonlocal completed_count, success_count, error_count
            completed_count += 1
            if record.status == "success":
                success_count += 1
            else:
                error_count += 1
            _record_agent_batch_event(
                self.world,
                "agent_batch_progress",
                interaction_type="instruct",
                interaction_name=name,
                agent_count=len(self.agent_ids),
                concurrency=effective_concurrency,
                model_id=model,
                fovs=fovs or [],
                actions=actions,
                target_ids_sample=self.agent_ids[:5],
                execution_options=execution_options,
                duration_sec=time.time() - batch_started,
                success_count=success_count,
                error_count=error_count,
                completed_count=completed_count,
                started_count=state.started_count,
                in_flight_count=state.in_flight_count,
                pending_count=state.pending_count,
                running_agent_ids_sample=state.active_agent_ids[:5],
                latest_agent_id=record.agent_id,
                latest_status=record.status,
            )

        records = await _run_limited(
            self.agent_ids,
            call,
            effective_concurrency,
            on_item_done=record_progress,
            on_heartbeat=record_heartbeat,
            heartbeat_interval_sec=_resolve_agent_batch_heartbeat_interval(self.world),
        )
        batch_result = AgentBatchResult(records)
        _record_agent_batch_event(
            self.world,
            "agent_batch_completed",
            interaction_type="instruct",
            interaction_name=name,
            agent_count=len(self.agent_ids),
            concurrency=effective_concurrency,
            model_id=model,
            fovs=fovs or [],
            actions=actions,
            target_ids_sample=self.agent_ids[:5],
            execution_options=execution_options,
            duration_sec=time.time() - batch_started,
            success_count=batch_result.success_count,
            error_count=batch_result.error_count,
            completed_count=len(self.agent_ids),
            started_count=len(self.agent_ids),
            in_flight_count=0,
            pending_count=0,
        )
        return batch_result

    async def interview(
        self,
        question: str,
        *,
        fovs: List[str] | None = None,
        output: Any,
        retrieve_memory: bool = True,
        save_memory: bool = False,
        model: Optional[str] = None,
        max_turns: int = 2,
        concurrency: Optional[int] = None,
        name: Optional[str] = None,
        reasoning_stages: Optional[List[Dict[str, Any]]] = None,
        memory_top_k: int = 10,
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
        top_p: Optional[float] = None,
        timeout: Optional[float] = None,
        llm_options: Optional[Dict[str, Any]] = None,
    ) -> AgentBatchResult:
        output_schema = _normalize_output_schema(output)
        llm_request_options = _build_llm_request_options(
            llm_options,
            max_tokens=max_tokens,
            temperature=temperature,
            top_p=top_p,
            timeout=timeout,
        )
        execution_options = _agent_batch_execution_options(
            max_turns=max_turns,
            output_schema=output_schema,
            reasoning_stages=reasoning_stages,
            memory={
                "retrieve": retrieve_memory,
                "save": save_memory,
                "extract": False,
                "top_k": memory_top_k,
            },
            llm_request_options=llm_request_options,
        )

        async def call(agent_id: str) -> AgentCallRecord:
            try:
                result = await self.world.interview_agent(
                    agent_id,
                    question,
                    fovs=fovs or [],
                    current_step=self.world.step,
                    model_id=model,
                    output_schema=output_schema,
                    retrieve_memory=retrieve_memory,
                    save_memory=save_memory,
                    memory_top_k=memory_top_k,
                    max_turns=max_turns,
                    name=name,
                    reasoning_stages=reasoning_stages,
                    llm_request_options=llm_request_options,
                )
                failure_record = _agent_failure_record(
                    agent_id,
                    result,
                    require_structured_output=output_schema is not None,
                )
                if failure_record is not None:
                    return failure_record
                return AgentCallRecord(agent_id, "success", _extract_call_value(result), raw=result)
            except Exception as exc:
                return AgentCallRecord(agent_id, "error", error=str(exc))

        effective_concurrency = _resolve_agent_call_concurrency(
            self.world,
            explicit_concurrency=concurrency,
            model_id=model,
        )
        batch_started = time.time()
        _record_agent_batch_event(
            self.world,
            "agent_batch_started",
            interaction_type="interview",
            interaction_name=name,
            agent_count=len(self.agent_ids),
            concurrency=effective_concurrency,
            model_id=model,
            fovs=fovs or [],
            actions=[],
            target_ids_sample=self.agent_ids[:5],
            execution_options=execution_options,
        )
        completed_count = 0
        success_count = 0
        error_count = 0

        def record_heartbeat(active_agent_ids: List[str], started_count: int) -> None:
            _record_agent_batch_event(
                self.world,
                "agent_batch_heartbeat",
                interaction_type="interview",
                interaction_name=name,
                agent_count=len(self.agent_ids),
                concurrency=effective_concurrency,
                model_id=model,
                fovs=fovs or [],
                actions=[],
                target_ids_sample=self.agent_ids[:5],
                execution_options=execution_options,
                duration_sec=time.time() - batch_started,
                success_count=success_count,
                error_count=error_count,
                completed_count=completed_count,
                started_count=started_count,
                in_flight_count=len(active_agent_ids),
                pending_count=max(len(self.agent_ids) - started_count, 0),
                running_agent_ids_sample=active_agent_ids[:5],
            )

        def record_progress(record: AgentCallRecord, state: BatchProgressState) -> None:
            nonlocal completed_count, success_count, error_count
            completed_count += 1
            if record.status == "success":
                success_count += 1
            else:
                error_count += 1
            _record_agent_batch_event(
                self.world,
                "agent_batch_progress",
                interaction_type="interview",
                interaction_name=name,
                agent_count=len(self.agent_ids),
                concurrency=effective_concurrency,
                model_id=model,
                fovs=fovs or [],
                actions=[],
                target_ids_sample=self.agent_ids[:5],
                execution_options=execution_options,
                duration_sec=time.time() - batch_started,
                success_count=success_count,
                error_count=error_count,
                completed_count=completed_count,
                started_count=state.started_count,
                in_flight_count=state.in_flight_count,
                pending_count=state.pending_count,
                running_agent_ids_sample=state.active_agent_ids[:5],
                latest_agent_id=record.agent_id,
                latest_status=record.status,
            )

        records = await _run_limited(
            self.agent_ids,
            call,
            effective_concurrency,
            on_item_done=record_progress,
            on_heartbeat=record_heartbeat,
            heartbeat_interval_sec=_resolve_agent_batch_heartbeat_interval(self.world),
        )
        batch_result = AgentBatchResult(records)
        _record_agent_batch_event(
            self.world,
            "agent_batch_completed",
            interaction_type="interview",
            interaction_name=name,
            agent_count=len(self.agent_ids),
            concurrency=effective_concurrency,
            model_id=model,
            fovs=fovs or [],
            actions=[],
            target_ids_sample=self.agent_ids[:5],
            execution_options=execution_options,
            duration_sec=time.time() - batch_started,
            success_count=batch_result.success_count,
            error_count=batch_result.error_count,
            completed_count=len(self.agent_ids),
            started_count=len(self.agent_ids),
            in_flight_count=0,
            pending_count=0,
        )
        return batch_result


class AgentSelector:
    def __init__(self, world: Any):
        self.world = world

    def all(self) -> AgentGroup:
        return AgentGroup(self.world, self.world.agents_data.keys())

    def ids(self, agent_ids: Iterable[str]) -> AgentGroup:
        return AgentGroup(self.world, [agent_id for agent_id in agent_ids if agent_id in self.world.agents_data])

    def where(self, **criteria: Any) -> AgentGroup:
        matched = []
        for agent_id, data in self.world.agents_data.items():
            if all(_matches_agent_field(data, key, expected) for key, expected in criteria.items()):
                matched.append(agent_id)
        return AgentGroup(self.world, matched)

    def sample(
        self,
        n: int,
        *,
        seed: Optional[int] = None,
        where: Optional[Dict[str, Any]] = None,
    ) -> AgentGroup:
        group = self.where(**where) if where else self.all()
        ids = group.ids()
        rng = random.Random(seed)
        return AgentGroup(self.world, rng.sample(ids, min(n, len(ids))))

    def filter(self, predicate: Callable[[Any], bool]) -> AgentGroup:
        matched = []
        for agent in self.all():
            if predicate(agent):
                matched.append(agent.id)
        return AgentGroup(self.world, matched)


@dataclass(slots=True)
class StepContext:
    step: int
    step_name: str
    world: Any
    env: Any
    params: Dict[str, Any]
    log: Any = None

    @property
    def agents(self) -> AgentSelector:
        return AgentSelector(self.world)

    @property
    def capabilities(self) -> CapabilityCatalog:
        return CapabilityCatalog(self.world)

    async def rule(self, rule_name: str, *, name: Optional[str] = None, **params: Any) -> Any:
        resolved = _resolve_logic_entry(self.world, rule_name, "rule")
        if resolved is None:
            raise ValueError(_format_missing_logic_error(self.world, rule_name, "rule"))

        resolved_name, rule_info = resolved
        rule_func = rule_info["function"]
        rule_sig = rule_info.get("signature") or inspect.signature(rule_func)
        env = self.world.get_environment()
        started = time.time()
        _record_logic_event(
            self.world,
            "logic_execution_started",
            logic_kind="rule",
            logic_name=name or resolved_name,
            resolved_name=resolved_name,
            param_keys=sorted(str(key) for key in params.keys()),
        )
        context = _build_direct_execution_context(
            self.world,
            caller=env,
            operator_id=name or resolved_name,
        )
        call_kwargs = _map_registered_call_kwargs(
            rule_sig,
            params,
            injections={
                "env": env,
                "environment": env,
                "world": self.world,
                "context": context,
                "params": params,
            },
            callable_name=resolved_name,
        )
        try:
            result = await invoke_maybe_async(rule_func, **call_kwargs)
        except Exception as exc:
            _record_logic_event(
                self.world,
                "logic_execution_failed",
                logic_kind="rule",
                logic_name=name or resolved_name,
                resolved_name=resolved_name,
                param_keys=sorted(str(key) for key in params.keys()),
                duration_sec=time.time() - started,
                error=str(exc),
                error_type=type(exc).__name__,
            )
            raise
        value = _extract_call_value(result)
        _record_logic_event(
            self.world,
            "logic_execution_completed",
            logic_kind="rule",
            logic_name=name or resolved_name,
            resolved_name=resolved_name,
            param_keys=sorted(str(key) for key in params.keys()),
            duration_sec=time.time() - started,
            success_count=1,
            error_count=0,
        )
        return value

    async def behavior(
        self,
        behavior_name: str,
        *,
        agents: Optional[AgentGroup | Iterable[str]] = None,
        concurrency: Optional[int] = None,
        name: Optional[str] = None,
        **params: Any,
    ) -> AgentBatchResult:
        if agents is None:
            group = self.agents.all()
        elif isinstance(agents, AgentGroup):
            group = agents
        else:
            group = self.agents.ids(agents)
        return await group.behavior(
            behavior_name,
            concurrency=concurrency,
            name=name,
            **params,
        )

    def result(
        self,
        *,
        metrics: Optional[Dict[str, Any]] = None,
        tables: Optional[Dict[str, Any]] = None,
        artifacts: Optional[Dict[str, Any]] = None,
        observations: Optional[Dict[str, Any]] = None,
        notes: Optional[str] = None,
    ) -> StepResult:
        return StepResult(
            metrics=metrics or {},
            tables=tables or {},
            artifacts=artifacts or {},
            observations=observations or {},
            notes=notes,
        )


@dataclass(slots=True)
class CodeStep:
    name: str
    fn: StepFunction
    params: Dict[str, Any] = field(default_factory=dict)


class CodeSchedule:
    def __init__(self) -> None:
        self.steps: List[CodeStep] = []

    def add_step(
        self,
        fn: StepFunction,
        *,
        name: Optional[str] = None,
        params: Optional[Dict[str, Any]] = None,
    ) -> StepFunction:
        if not callable(fn):
            raise TypeError("step function must be callable")
        if not inspect.iscoroutinefunction(fn):
            raise TypeError("step function must be async")
        self.steps.append(CodeStep(name=name or fn.__name__, fn=fn, params=dict(params or {})))
        return fn

    def step(
        self,
        *,
        name: Optional[str] = None,
        params: Optional[Dict[str, Any]] = None,
    ) -> Callable[[StepFunction], StepFunction]:
        def decorator(fn: StepFunction) -> StepFunction:
            return self.add_step(fn, name=name, params=params)

        return decorator

    async def execute_tick(
        self,
        *,
        tick: int,
        world: Any,
        log: Any = None,
        on_step_event: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> List[Dict[str, Any]]:
        if not self.steps:
            raise RuntimeError("No code steps registered")
        results = []
        env = world.get_environment()
        for code_step in self.steps:
            started = time.time()
            previous_step_name = getattr(world, "_current_code_step_name", None)
            world._current_code_step_name = code_step.name
            if on_step_event is not None:
                on_step_event(
                    {
                        "event": "code_step_started",
                        "step": tick,
                        "step_name": code_step.name,
                        "at": started,
                    }
                )
            ctx = StepContext(
                step=tick,
                step_name=code_step.name,
                world=world,
                env=env,
                params=dict(code_step.params),
                log=log,
            )
            try:
                result = await code_step.fn(ctx)
                if result is None:
                    result = StepResult()
                if not isinstance(result, StepResult):
                    raise TypeError(f"Step '{code_step.name}' returned {type(result).__name__}, expected StepResult or None")
                entry = {
                    "step": tick,
                    "step_name": code_step.name,
                    "duration_sec": time.time() - started,
                    "result": result.to_dict(),
                }
                results.append(entry)
                if on_step_event is not None:
                    on_step_event(
                        {
                            "event": "code_step_completed",
                            "step": tick,
                            "step_name": code_step.name,
                            "duration_sec": entry["duration_sec"],
                            "at": time.time(),
                        }
                    )
            except Exception as exc:
                if on_step_event is not None:
                    on_step_event(
                        {
                            "event": "code_step_failed",
                            "step": tick,
                            "step_name": code_step.name,
                            "duration_sec": time.time() - started,
                            "error": str(exc),
                            "error_type": type(exc).__name__,
                            "at": time.time(),
                        }
                    )
                raise
            finally:
                world._current_code_step_name = previous_step_name
        return results


async def _run_limited(
    items: Iterable[str],
    call: Callable[[str], Awaitable[AgentCallRecord]],
    concurrency: Optional[int],
    *,
    on_item_done: Optional[Callable[[AgentCallRecord, BatchProgressState], None]] = None,
    on_heartbeat: Optional[Callable[[List[str], int], None]] = None,
    heartbeat_interval_sec: Optional[float] = None,
) -> List[AgentCallRecord]:
    item_list = list(items)
    if concurrency is None or concurrency <= 0:
        concurrency = max(1, len(item_list))
    semaphore = asyncio.Semaphore(concurrency)
    active: Dict[str, float] = {}
    started_count = 0
    completed_count = 0
    state_lock = asyncio.Lock()
    done_event = asyncio.Event()

    async def heartbeat_loop() -> None:
        if on_heartbeat is None or heartbeat_interval_sec is None or heartbeat_interval_sec <= 0:
            return
        while not done_event.is_set():
            try:
                await asyncio.wait_for(done_event.wait(), timeout=heartbeat_interval_sec)
                break
            except asyncio.TimeoutError:
                pass
            async with state_lock:
                active_ids = list(active.keys())
                current_started_count = started_count
                current_completed_count = completed_count
            if current_completed_count < len(item_list):
                on_heartbeat(active_ids, current_started_count)

    async def guarded(item: str) -> AgentCallRecord:
        nonlocal started_count, completed_count
        async with semaphore:
            async with state_lock:
                active[item] = time.time()
                started_count += 1
            try:
                record = await call(item)
            except Exception as exc:
                record = AgentCallRecord(item, "error", error=str(exc))
            async with state_lock:
                active_ids_before_completion = list(active.keys())
                current_started_count = started_count
                active.pop(item, None)
                completed_count += 1
                current_completed_count = completed_count
                progress_state = BatchProgressState(
                    started_count=current_started_count,
                    completed_count=current_completed_count,
                    in_flight_count=len(active_ids_before_completion),
                    pending_count=max(len(item_list) - current_started_count, 0),
                    active_agent_ids=active_ids_before_completion,
                )
            if on_item_done is not None:
                on_item_done(record, progress_state)
            return record

    heartbeat_task = asyncio.create_task(heartbeat_loop())
    try:
        return list(await asyncio.gather(*(guarded(item) for item in item_list)))
    finally:
        done_event.set()
        await heartbeat_task


def _resolve_agent_batch_heartbeat_interval(world: Any) -> float:
    raw_value = getattr(world, "_agent_batch_heartbeat_interval_sec", 10.0)
    try:
        value = float(raw_value)
    except (TypeError, ValueError):
        return 10.0
    return value


def _resolve_non_llm_batch_concurrency(item_count: int, explicit_concurrency: Optional[int]) -> int:
    if explicit_concurrency is None:
        return max(1, item_count)
    try:
        parsed = int(explicit_concurrency)
    except (TypeError, ValueError):
        return max(1, item_count)
    if parsed <= 0:
        return max(1, item_count)
    return parsed


def _resolve_agent_call_concurrency(
    world: Any,
    *,
    explicit_concurrency: Optional[int],
    model_id: Optional[str],
) -> int:
    if explicit_concurrency is not None:
        return _validate_positive_concurrency(explicit_concurrency, "concurrency")

    default_concurrency = getattr(world, "_default_agent_concurrency", None)
    if default_concurrency is not None:
        return _validate_positive_concurrency(default_concurrency, "world._default_agent_concurrency")

    provider = getattr(world, "_model_provider", None)
    if provider is not None:
        try:
            models = getattr(provider, "_models", {})
            default_model_id = provider.get_default_model_id() if hasattr(provider, "get_default_model_id") else None
            target_id = model_id or default_model_id
            runtime = models.get(target_id) or models.get(default_model_id)
            runtime_config = getattr(runtime, "config", None)
            if runtime_config is not None:
                model_concurrency = getattr(runtime_config, "concurrency", None)
                if model_concurrency is not None:
                    return _validate_positive_concurrency(model_concurrency, "model concurrency")
        except Exception:
            pass

    return 5


def _build_llm_request_options(
    base_options: Optional[Dict[str, Any]],
    *,
    max_tokens: Optional[int] = None,
    temperature: Optional[float] = None,
    top_p: Optional[float] = None,
    timeout: Optional[float] = None,
) -> Dict[str, Any]:
    """Build safe per-call LLM request options for the agent runtime."""
    forbidden = {"messages", "tools", "tool_choice", "metadata", "agent_id", "model"}
    options = {
        key: value
        for key, value in dict(base_options or {}).items()
        if key not in forbidden and value is not None
    }

    def set_positive_int(name: str, value: Optional[int]) -> None:
        if value is None:
            return
        normalized = int(value)
        if normalized <= 0:
            raise ValueError(f"{name} must be a positive integer")
        options[name] = normalized

    def set_number(name: str, value: Optional[float], *, minimum: Optional[float] = None) -> None:
        if value is None:
            return
        normalized = float(value)
        if minimum is not None and normalized < minimum:
            raise ValueError(f"{name} must be >= {minimum}")
        options[name] = normalized

    set_positive_int("max_tokens", max_tokens)
    set_number("temperature", temperature, minimum=0.0)
    set_number("top_p", top_p, minimum=0.0)
    set_number("timeout", timeout, minimum=0.0)
    return options


def _summarize_reasoning_stages(reasoning_stages: Optional[List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    summarized = []
    for index, stage in enumerate(reasoning_stages or []):
        if not isinstance(stage, dict):
            summarized.append({"index": index, "name": str(stage)})
            continue
        item: Dict[str, Any] = {"index": index}
        name = stage.get("name")
        if name is not None:
            item["name"] = str(name)
        description = stage.get("desc") or stage.get("description")
        if description is not None:
            item["description_length"] = len(str(description))
        summarized.append(item)
    return summarized


def _summarize_llm_request_options(options: Dict[str, Any]) -> Dict[str, Any]:
    safe_value_keys = {"max_tokens", "temperature", "top_p", "timeout"}
    summarized = {
        key: _jsonable(value)
        for key, value in options.items()
        if key in safe_value_keys
    }
    custom_keys = sorted(str(key) for key in options if key not in safe_value_keys)
    if custom_keys:
        summarized["custom_option_keys"] = custom_keys
    return summarized


def _agent_batch_execution_options(
    *,
    max_turns: int,
    output_schema: Any,
    reasoning_stages: Optional[List[Dict[str, Any]]],
    memory: Dict[str, Any],
    llm_request_options: Dict[str, Any],
    terminal_actions: Optional[List[str]] = None,
    completion_action_tags: Optional[List[str]] = None,
    max_action_calls: Optional[int] = None,
    action_call_limits: Optional[Dict[str, int]] = None,
) -> Dict[str, Any]:
    """Summarize fidelity-relevant runtime options without recording prompts."""
    options: Dict[str, Any] = {
        "max_turns": int(max_turns),
        "output_schema": output_schema is not None,
        "reasoning_stage_count": len(reasoning_stages or []),
        "reasoning_stages": _summarize_reasoning_stages(reasoning_stages),
        "memory": _jsonable(memory),
        "llm_request_options": _summarize_llm_request_options(llm_request_options),
    }
    if terminal_actions is not None:
        options["terminal_actions"] = list(terminal_actions)
    if completion_action_tags is not None:
        options["completion_action_tags"] = list(completion_action_tags)
    if max_action_calls is not None:
        options["max_action_calls"] = int(max_action_calls)
    if action_call_limits is not None:
        options["action_call_limits"] = {str(key): int(value) for key, value in action_call_limits.items()}
    return options


def _logic_error_samples(records: List[AgentCallRecord], *, limit: int = 5) -> List[Dict[str, Any]]:
    samples: List[Dict[str, Any]] = []
    for record in records:
        if record.status == "success":
            continue
        samples.append(
            {
                "agent_id": record.agent_id,
                "status": record.status,
                "error": record.error,
            }
        )
        if len(samples) >= limit:
            break
    return samples


def _record_logic_event(
    world: Any,
    event_type: str,
    *,
    logic_kind: str,
    logic_name: str,
    resolved_name: str,
    param_keys: List[str],
    duration_sec: Optional[float] = None,
    agent_count: Optional[int] = None,
    concurrency: Optional[int] = None,
    target_ids_sample: Optional[List[str]] = None,
    success_count: Optional[int] = None,
    error_count: Optional[int] = None,
    error_samples: Optional[List[Dict[str, Any]]] = None,
    error: Optional[str] = None,
    error_type: Optional[str] = None,
) -> None:
    event_logger = getattr(world, "event_logger", None)
    if event_logger is None:
        return
    try:
        from .events import BaseEvent

        class LogicExecutionEvent(BaseEvent):
            def __init__(self, *, context_stack: List[Dict[str, Any]]):
                super().__init__(event_type=event_type, context_stack=context_stack)
                self.source = "code_schedule"
                self.event_data = event_data

            def to_dict(self) -> Dict[str, Any]:
                payload = super().to_dict()
                payload.update({"source": self.source, "event_data": self.event_data})
                return payload

        context_stack = []
        if hasattr(world, "get_context_stack"):
            try:
                context_stack = world.get_context_stack().to_list()
            except Exception:
                context_stack = []

        event_data: Dict[str, Any] = {
            "step": getattr(world, "step", None),
            "step_name": getattr(world, "_current_code_step_name", None),
            "logic_kind": logic_kind,
            "logic_name": logic_name,
            "resolved_name": resolved_name,
            "param_keys": list(param_keys or []),
        }
        if duration_sec is not None:
            event_data["duration_sec"] = duration_sec
        if agent_count is not None:
            event_data["agent_count"] = agent_count
        if concurrency is not None:
            event_data["concurrency"] = concurrency
        if target_ids_sample is not None:
            event_data["target_ids_sample"] = list(target_ids_sample or [])
        if success_count is not None:
            event_data["success_count"] = success_count
        if error_count is not None:
            event_data["error_count"] = error_count
        if error_samples:
            event_data["error_samples"] = _jsonable(error_samples)
        if error is not None:
            event_data["error"] = error
        if error_type is not None:
            event_data["error_type"] = error_type

        event_logger.write_event(LogicExecutionEvent(context_stack=context_stack))
    except Exception:
        pass


def _record_agent_batch_event(
    world: Any,
    event_type: str,
    *,
    interaction_type: str,
    interaction_name: Optional[str],
    agent_count: int,
    concurrency: int,
    model_id: Optional[str],
    fovs: List[str],
    actions: Optional[List[str]],
    target_ids_sample: List[str],
    execution_options: Optional[Dict[str, Any]] = None,
    duration_sec: Optional[float] = None,
    success_count: Optional[int] = None,
    error_count: Optional[int] = None,
    completed_count: Optional[int] = None,
    started_count: Optional[int] = None,
    in_flight_count: Optional[int] = None,
    pending_count: Optional[int] = None,
    running_agent_ids_sample: Optional[List[str]] = None,
    latest_agent_id: Optional[str] = None,
    latest_status: Optional[str] = None,
) -> None:
    event_logger = getattr(world, "event_logger", None)
    if event_logger is None:
        return
    try:
        from .events import BaseEvent

        class AgentBatchEvent(BaseEvent):
            def __init__(self, *, context_stack: List[Dict[str, Any]]):
                super().__init__(event_type=event_type, context_stack=context_stack)
                self.source = "code_schedule"
                self.event_data = event_data

            def to_dict(self) -> Dict[str, Any]:
                payload = super().to_dict()
                payload.update({"source": self.source, "event_data": self.event_data})
                return payload

        context_stack = []
        if hasattr(world, "get_context_stack"):
            try:
                context_stack = world.get_context_stack().to_list()
            except Exception:
                context_stack = []

        event_data: Dict[str, Any] = {
            "step": getattr(world, "step", None),
            "step_name": getattr(world, "_current_code_step_name", None),
            "interaction_type": interaction_type,
            "interaction_name": interaction_name,
            "agent_count": agent_count,
            "concurrency": concurrency,
            "model_id": model_id,
            "fovs": list(fovs or []),
            "actions": list(actions or []),
            "target_ids_sample": list(target_ids_sample or []),
        }
        if execution_options is not None:
            event_data["execution_options"] = _jsonable(execution_options)
        if duration_sec is not None:
            event_data["duration_sec"] = duration_sec
        if success_count is not None:
            event_data["success_count"] = success_count
        if error_count is not None:
            event_data["error_count"] = error_count
        if completed_count is not None:
            event_data["completed_count"] = completed_count
        if started_count is not None:
            event_data["started_count"] = started_count
        if in_flight_count is not None:
            event_data["in_flight_count"] = in_flight_count
        if pending_count is not None:
            event_data["pending_count"] = pending_count
        if running_agent_ids_sample is not None:
            event_data["running_agent_ids_sample"] = list(running_agent_ids_sample or [])
        if latest_agent_id is not None:
            event_data["latest_agent_id"] = latest_agent_id
        if latest_status is not None:
            event_data["latest_status"] = latest_status

        event_logger.write_event(AgentBatchEvent(context_stack=context_stack))
    except Exception:
        pass


def _validate_positive_concurrency(value: Any, label: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"{label} must be a positive integer") from None
    if parsed <= 0:
        raise ValueError(f"{label} must be a positive integer")
    return parsed


def _matches_agent_field(data: Dict[str, Any], key: str, expected: Any) -> bool:
    if key in data:
        return data.get(key) == expected
    state = data.get("state") or {}
    if isinstance(state, dict) and key in state:
        return state.get(key) == expected
    properties = data.get("properties") or {}
    if isinstance(properties, dict) and key in properties:
        return properties.get(key) == expected
    return False


def _extract_call_value(result: Any) -> Any:
    if isinstance(result, BaseOperatorResult):
        return result.value
    if isinstance(result, dict):
        structured = result.get("structured_output")
        if structured is not None:
            if isinstance(structured, dict):
                compact = dict(structured)
                for key in (
                    "total_turns",
                    "llm_calls",
                    "finish_instruction_called",
                    "actions_available",
                    "model_id",
                ):
                    if key in result and key not in compact:
                        compact[key] = result[key]
                return compact
            return structured
        value = result.get("value")
        if value is not None:
            return value
        compact: Dict[str, Any] = {}
        for key in (
            "performative_output",
            "total_turns",
            "finish_instruction_called",
            "actions_available",
            "model_id",
        ):
            if key in result:
                compact[key] = result[key]
        actions = result.get("actions")
        if actions is not None:
            compact["actions"] = actions
        if result.get("error"):
            compact["error"] = result["error"]
        return compact or result
    return result


def _extract_actions_from_record(record: AgentCallRecord) -> List[Dict[str, Any]]:
    """Normalize action call rows from the public record value or raw agent result."""
    source_actions = None
    if isinstance(record.value, dict) and isinstance(record.value.get("actions"), list):
        source_actions = record.value.get("actions")
    elif isinstance(record.raw, dict) and isinstance(record.raw.get("actions"), list):
        source_actions = record.raw.get("actions")

    if not source_actions:
        return []

    normalized: List[Dict[str, Any]] = []
    for item in source_actions:
        if not isinstance(item, dict):
            continue
        action_name = item.get("action_name") or item.get("name") or item.get("action")
        row = {
            "agent_id": record.agent_id,
            "status": record.status,
            **{str(key): _jsonable(value) for key, value in item.items()},
        }
        if action_name is not None:
            row["action_name"] = str(action_name)
        arguments = row.get("arguments")
        if isinstance(arguments, dict):
            row["arguments"] = _compact_action_mapping(arguments)
        result = row.get("result")
        if isinstance(result, str):
            text_summary = _compact_action_text(result)
            row["result"] = text_summary["value"]
            if text_summary["truncated"]:
                row["result_length"] = text_summary["length"]
                row["result_truncated"] = True
        normalized.append(row)
    return normalized


def _agent_failure_record(
    agent_id: str,
    result: Any,
    *,
    require_structured_output: bool = False,
) -> Optional[AgentCallRecord]:
    if not isinstance(result, dict):
        return None
    status = result.get("status")
    if status is None or status == "success":
        if require_structured_output and result.get("structured_output") is None:
            return AgentCallRecord(
                agent_id=agent_id,
                status="error",
                value=_extract_call_value(result),
                error="missing structured_output",
                raw=result,
            )
        return None
    return AgentCallRecord(
        agent_id=agent_id,
        status=str(status),
        value=_extract_call_value(result),
        error=str(result.get("error") or status),
        raw=result,
    )


def _normalize_output_schema(output: Any) -> Any:
    if output is None or isinstance(output, dict):
        return output
    model_json_schema = getattr(output, "model_json_schema", None)
    if callable(model_json_schema):
        return model_json_schema()
    schema = getattr(output, "schema", None)
    if callable(schema):
        return schema()
    return output


def _capability_entries(table: Dict[str, Dict[str, Any]], kind: str) -> List[Dict[str, Any]]:
    entries: List[Dict[str, Any]] = []
    for key, entry in table.items():
        canonical_id = entry.get("canonical_id") or key
        display_name = entry.get("display_name") or key.rsplit(".", maxsplit=1)[-1]
        meta = entry.get("meta")
        if meta is not None:
            display_name = getattr(meta, "name", display_name) or display_name
            canonical_id = getattr(entry.get("meta"), "canonical_id", None) or canonical_id
        entries.append(
            {
                "id": canonical_id,
                "name": display_name,
                "kind": entry.get("kind") or kind,
                "source": entry.get("source") or "unknown",
                "description": entry.get("description", ""),
                "tags": list(entry.get("tags", []) or []),
                "parameters": _jsonable(entry.get("parameters", {})),
                "key": key,
                "environment_type": entry.get("environment_type"),
            }
        )
    return _dedupe_capability_entries(entries)


def _dedupe_capability_entries(entries: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    deduped: List[Dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for entry in entries:
        identity = (
            str(entry.get("kind")),
            str(entry.get("id") or entry.get("key")),
            str(entry.get("name")),
        )
        if identity in seen:
            continue
        seen.add(identity)
        deduped.append(entry)
    return sorted(deduped, key=lambda item: (str(item.get("kind")), str(item.get("name"))))


def _build_direct_execution_context(world: Any, *, caller: Any, operator_id: Optional[str]) -> ExecutionContext:
    return ExecutionContext(
        world=world,
        step=None,
        node=None,
        caller=caller,
        event_logger=getattr(world, "event_logger", None),
        log_context=getattr(world, "_log_context", None),
        operator_id=operator_id,
    )


def _resolve_logic_entry(world: Any, name: str, kind: str) -> Optional[tuple[str, Dict[str, Any]]]:
    registry = world.get_logic_provider()
    table = registry.behaviors if kind == "behavior" else registry.rules
    if name in table:
        return name, table[name]

    short_name = name.rsplit(".", maxsplit=1)[-1]
    for key, entry in table.items():
        if key.rsplit(".", maxsplit=1)[-1] == short_name:
            return key, entry
        if entry.get("display_name") == name or entry.get("func_name") == name:
            return key, entry
    return None


def _format_missing_logic_error(world: Any, name: str, kind: str) -> str:
    label = "Behavior" if kind == "behavior" else "Rule"
    parts = [f"{label} '{name}' not found."]
    try:
        catalog = CapabilityCatalog(world)
        same_kind = catalog.names(kind)
        if same_kind:
            plural = "behaviors" if kind == "behavior" else "rules"
            parts.append(f"Available {plural}: {', '.join(same_kind[:12])}.")
        matching_other_kinds = [
            other_kind
            for other_kind in ("fov", "action", "rule", "behavior")
            if other_kind != kind and name in catalog.names(other_kind)
        ]
        if matching_other_kinds:
            parts.append(
                f"'{name}' is registered as {', '.join(matching_other_kinds)}, not {kind}."
            )
            parts.append(
                "Use FoVs with fovs=[...], actions with instruct(..., actions=[...]), "
                "rules with ctx.rule(...), and behaviors with ctx.behavior(...) or AgentGroup.behavior(...)."
            )
    except Exception:
        pass
    return " ".join(parts)


def _map_registered_call_kwargs(
    signature: inspect.Signature,
    params: Dict[str, Any],
    *,
    injections: Dict[str, Any],
    callable_name: str,
) -> Dict[str, Any]:
    call_kwargs: Dict[str, Any] = {}
    has_var_keyword = False

    for param_name, param in signature.parameters.items():
        if param_name in {"self", "cls"}:
            continue
        if param.kind == inspect.Parameter.VAR_POSITIONAL:
            continue
        if param.kind == inspect.Parameter.VAR_KEYWORD:
            has_var_keyword = True
            continue

        injected = _injection_for_parameter(param_name, param, injections)
        if injected[0]:
            call_kwargs[param_name] = injected[1]
            continue

        if param_name in params:
            call_kwargs[param_name] = params[param_name]
            continue

        if param.default != inspect.Parameter.empty:
            continue

        raise ValueError(f"Required parameter '{param_name}' missing for '{callable_name}'")

    if has_var_keyword:
        for key, value in params.items():
            call_kwargs.setdefault(key, value)

    return call_kwargs


def _injection_for_parameter(
    param_name: str,
    param: inspect.Parameter,
    injections: Dict[str, Any],
) -> tuple[bool, Any]:
    if param_name in injections:
        return True, injections[param_name]

    annotation = param.annotation
    if annotation != inspect.Parameter.empty:
        annotation_str = str(annotation)
        if "ExecutionContext" in annotation_str and "context" in injections:
            return True, injections["context"]

    return False, None


Schedule = CodeSchedule

try:
    from .legacy.schedule import StepFlow, StepNode  # noqa: F401
except Exception:  # pragma: no cover
    StepFlow = None  # type: ignore
    StepNode = None  # type: ignore


__all__ = [
    "AgentBatchResult",
    "AgentGroup",
    "AgentSelector",
    "CapabilityCatalog",
    "CodeSchedule",
    "CodeStep",
    "Schedule",
    "StepContext",
    "StepFlow",
    "StepNode",
    "StepResult",
]
