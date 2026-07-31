"""Code-driven scheduling and step DSL for Society0."""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import random
import statistics
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, Iterable, Iterator, List, Mapping, Optional

from .async_utils import invoke_maybe_async
from .core_data import BaseOperatorResult, ExecutionContext

StepFunction = Callable[["StepContext"], Awaitable[Optional["StepResult"]]]
ACTION_TEXT_PREVIEW_CHARS = 240
MEMORY_DIAGNOSTIC_KEYS = (
    "memory_retrieved",
    "memory_top_k",
    "memory_saved",
    "memory_extraction_enabled",
    "memory_extraction_success",
    "memory_extraction_error",
    "extracted_memory_count",
    "extracted_memories",
)
MEMORY_TABLE_KEYS = tuple(key for key in MEMORY_DIAGNOSTIC_KEYS if key != "extracted_memories")


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
    duration_sec: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        payload = {
            "agent_id": self.agent_id,
            "status": self.status,
            "value": _jsonable(self.value),
            "error": self.error,
            "raw": _jsonable(self.raw),
        }
        if self.duration_sec is not None:
            payload["duration_sec"] = round(float(self.duration_sec), 6)
        return payload


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

    def successful_action_counts(self) -> Dict[str, int]:
        """Count successful action calls by action name."""
        return _action_counts_by_status(self.actions(), success=True)

    def failed_action_counts(self) -> Dict[str, int]:
        """Count failed action calls by action name."""
        return _action_counts_by_status(self.actions(), success=False)

    def action_error_samples(self, *, limit: int = 5) -> List[Dict[str, Any]]:
        """Return compact failed-action samples without treating the agent as failed."""
        return _action_error_samples(self.actions(), limit=limit)

    def action_tag_counts(self) -> Dict[str, int]:
        """Count successful action trace tags across all agent records."""
        counts: Dict[str, int] = {}
        for action in self.actions():
            if str(action.get("status") or "success").lower() != "success":
                continue
            for tag in action.get("tags") or []:
                tag_key = str(tag)
                if not tag_key:
                    continue
                counts[tag_key] = counts.get(tag_key, 0) + 1
        return counts

    def action_duration_summary(self, *, limit: int = 5) -> Dict[str, Any]:
        """Summarize wall-clock duration for actual action/tool calls."""
        return _summarize_action_durations(self.actions(), limit=limit)

    def termination_reason_counts(self) -> Dict[str, int]:
        """Count how successful agent loops terminated."""
        counts: Dict[str, int] = {}
        for record in self.records:
            if record.status != "success":
                continue
            payload = record.raw if isinstance(record.raw, dict) else record.value
            if not isinstance(payload, dict):
                continue
            reason = payload.get("termination_reason")
            if not reason:
                continue
            key = str(reason)
            counts[key] = counts.get(key, 0) + 1
        return counts

    def memory_summary(self) -> Dict[str, Any]:
        """Summarize actual memory diagnostics returned by agent calls."""
        return _summarize_memory_diagnostics(
            (record.agent_id, _memory_payload_from_record(record)) for record in self.records
        )

    def duration_summary(self, *, limit: int = 5) -> Dict[str, Any]:
        """Summarize per-agent wall-clock duration for batch diagnostics."""
        return _summarize_agent_record_durations(self.records, limit=limit)

    def phase_timing_summary(self) -> Dict[str, Any]:
        """Summarize per-agent runtime phase timings for bottleneck diagnostics."""
        return _summarize_agent_phase_timings(self.records)

    def error_samples(self, *, limit: int = 5) -> List[Dict[str, Any]]:
        """Return compact failed-agent samples for step-level diagnostics."""
        return _logic_error_samples(self.records, limit=limit)

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
            if record.duration_sec is not None:
                row["duration_sec"] = round(float(record.duration_sec), 6)
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
            "successful_action_counts": self.successful_action_counts(),
            "failed_action_counts": self.failed_action_counts(),
            "action_tag_counts": self.action_tag_counts(),
            "action_error_samples": self.action_error_samples(),
            "action_duration_summary": self.action_duration_summary(),
            "termination_reason_counts": self.termination_reason_counts(),
            "memory_summary": self.memory_summary(),
            "duration_summary": self.duration_summary(),
            "phase_timing_summary": self.phase_timing_summary(),
            "error_samples": self.error_samples(),
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

    def names(self, kind: str, *, source: Optional[str] = None) -> List[str]:
        return [entry["name"] for entry in self.by_kind(kind) if source is None or entry.get("source") == source]

    def find(
        self,
        name: str,
        *,
        kind: Optional[str] = None,
        source: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Find capabilities by name or alias, optionally narrowing kind/source.

        This is the discovery path for user-facing agents that know a copied
        capability name but do not yet know whether it is a FoV, action, rule,
        or behavior.
        """
        kinds = [_normalize_capability_kind(kind)] if kind is not None else ["fov", "action", "rule", "behavior"]
        matches: List[Dict[str, Any]] = []
        for normalized_kind in kinds:
            for entry in self.by_kind(normalized_kind):
                if source is not None and entry.get("source") != source:
                    continue
                if _capability_entry_matches(entry, name):
                    matches.append(entry)
        return _dedupe_capability_entries(matches)

    def get(self, kind: str, name: str, *, source: Optional[str] = None) -> Optional[Dict[str, Any]]:
        matches = self.find(name, kind=kind, source=source)
        return matches[0] if matches else None

    def has(self, kind: str, name: str, *, source: Optional[str] = None) -> bool:
        return self.get(kind, name, source=source) is not None

    def by_source(self, source: str, *, kind: Optional[str] = None) -> Any:
        if kind is not None:
            return [entry for entry in self.by_kind(kind) if entry.get("source") == source]
        return {
            "fovs": self.by_source(source, kind="fov"),
            "actions": self.by_source(source, kind="action"),
            "rules": self.by_source(source, kind="rule"),
            "behaviors": self.by_source(source, kind="behavior"),
        }

    def by_kind(self, kind: str) -> List[Dict[str, Any]]:
        kind = _normalize_capability_kind(kind)
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
            agent_started = time.time()
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
                return AgentCallRecord(
                    agent_id,
                    "success",
                    _extract_call_value(result),
                    raw=result,
                    duration_sec=time.time() - agent_started,
                )
            except Exception as exc:
                return AgentCallRecord(
                    agent_id,
                    "error",
                    error=str(exc),
                    duration_sec=time.time() - agent_started,
                )

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

    async def remember(
        self,
        episodes_by_agent: Mapping[str, str],
        *,
        timestamp: Optional[int] = None,
        idempotency_key: Optional[str] = None,
        importance: Optional[float] = 3.0,
        metadata: Optional[Mapping[str, Any]] = None,
        metadata_by_agent: Optional[Mapping[str, Mapping[str, Any]]] = None,
        concurrency: Optional[int] = None,
        name: Optional[str] = None,
    ) -> AgentBatchResult:
        """Persist one caller-prepared episodic memory for every agent in the group.

        This is the batch boundary for simulations that consolidate an agent's
        completed turns once per tick.  It deliberately stores supplied text and
        does not run another LLM extraction pass.
        """
        episode_keys = set(episodes_by_agent)
        group_keys = set(self.agent_ids)
        if episode_keys != group_keys:
            raise ValueError(
                "episodes_by_agent keys must exactly match the AgentGroup ids"
            )
        invalid_ids = [
            agent_id
            for agent_id in self.agent_ids
            if not isinstance(episodes_by_agent[agent_id], str)
            or not episodes_by_agent[agent_id].strip()
        ]
        if invalid_ids:
            raise ValueError(
                "Each episode must be a non-empty string; invalid agents: "
                + ", ".join(invalid_ids)
            )

        common_metadata = dict(metadata or {})
        per_agent_metadata = dict(metadata_by_agent or {})
        unknown_metadata_ids = set(per_agent_metadata) - group_keys
        if unknown_metadata_ids:
            raise ValueError(
                "metadata_by_agent contains ids outside the AgentGroup: "
                + ", ".join(sorted(unknown_metadata_ids))
            )

        effective_timestamp = _resolve_memory_timestamp(
            self.world.step if timestamp is None else timestamp
        )
        effective_concurrency, concurrency_source = (
            _resolve_agent_call_concurrency_info(
                self.world,
                explicit_concurrency=concurrency,
                model_id=None,
            )
        )
        interaction_name = name or "remember"
        started = time.time()
        execution_options = {
            "memory": {
                "type": "episodic",
                "caller_prepared": True,
                "llm_extraction": False,
                "timestamp": effective_timestamp,
                "idempotent": idempotency_key is not None,
            }
        }
        _record_agent_batch_event(
            self.world,
            "agent_batch_started",
            interaction_type="remember",
            interaction_name=interaction_name,
            agent_count=len(self.agent_ids),
            concurrency=effective_concurrency,
            concurrency_source=concurrency_source,
            model_id=None,
            fovs=[],
            actions=[],
            target_ids_sample=self.agent_ids[:5],
            execution_options=execution_options,
        )

        async def call(agent_id: str) -> AgentCallRecord:
            agent_started = time.time()
            try:
                agent = self.world.get_agent(agent_id)
                agent_metadata = dict(common_metadata)
                agent_metadata.update(dict(per_agent_metadata.get(agent_id, {})))
                memory_kwargs = {
                    "content": episodes_by_agent[agent_id].strip(),
                    "timestamp": effective_timestamp,
                    "importance": importance,
                    "metadata": agent_metadata,
                    "trace": {
                        "step": effective_timestamp,
                        "interaction_type": "memory_write",
                        "interaction_name": interaction_name,
                    },
                }
                if idempotency_key is not None:
                    if hasattr(agent.memory, "stable_memory_id"):
                        stable_id = agent.memory.stable_memory_id(
                            idempotency_key,
                            memory_type="episodic",
                        )
                    else:
                        branch_id = str(
                            getattr(agent.memory, "branch_id", "main")
                        )
                        digest = hashlib.sha256(
                            (
                                f"{idempotency_key}\0{agent_id}\0"
                                f"{branch_id}"
                            ).encode("utf-8")
                        ).hexdigest()
                        stable_id = f"episodic_{digest}"
                    memory_kwargs["memory_id"] = stable_id
                    agent_metadata.setdefault(
                        "idempotency_key", idempotency_key
                    )
                memory_id = await agent.memory.add_episodic_memory(
                    **memory_kwargs,
                )
                return AgentCallRecord(
                    agent_id,
                    "success",
                    {"memory_id": memory_id},
                    duration_sec=time.time() - agent_started,
                )
            except Exception as exc:
                return AgentCallRecord(
                    agent_id,
                    "error",
                    error=str(exc),
                    duration_sec=time.time() - agent_started,
                )

        batch_result = AgentBatchResult(
            await _run_limited(self.agent_ids, call, effective_concurrency)
        )
        _record_agent_batch_event(
            self.world,
            "agent_batch_completed",
            interaction_type="remember",
            interaction_name=interaction_name,
            agent_count=len(self.agent_ids),
            concurrency=effective_concurrency,
            concurrency_source=concurrency_source,
            model_id=None,
            fovs=[],
            actions=[],
            target_ids_sample=self.agent_ids[:5],
            execution_options=execution_options,
            duration_sec=time.time() - started,
            success_count=batch_result.success_count,
            error_count=batch_result.error_count,
            completed_count=len(self.agent_ids),
            started_count=len(self.agent_ids),
            in_flight_count=0,
            pending_count=0,
            agent_duration_summary=batch_result.duration_summary(),
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
        retrieve_memory: Optional[bool] = None,
        save_memory: Optional[bool] = None,
        extract_memory: bool = True,
        model: Optional[str] = None,
        current_step: Optional[int] = None,
        max_turns: int = 3,
        concurrency: Optional[int] = None,
        name: Optional[str] = None,
        reasoning_stages: Optional[List[Dict[str, Any]]] = None,
        memory_top_k: int = 10,
        terminal_actions: Optional[List[str]] = None,
        completion_action_tags: Optional[List[str]] = None,
        max_action_calls: Optional[int] = None,
        action_call_limits: Optional[Dict[str, int]] = None,
        required_actions: Optional[List[str]] = None,
        required_action_tags: Optional[List[str]] = None,
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
        top_p: Optional[float] = None,
        timeout: Optional[float] = None,
        llm_options: Optional[Dict[str, Any]] = None,
        prior_messages_by_agent: Optional[
            Dict[str, List[Dict[str, Any]]]
        ] = None,
    ) -> AgentBatchResult:
        output_schema = _normalize_output_schema(output)
        llm_request_options = _build_llm_request_options(
            llm_options,
            max_tokens=max_tokens,
            temperature=temperature,
            top_p=top_p,
            timeout=timeout,
        )
        effective_current_step = _resolve_memory_timestamp(
            self.world.step if current_step is None else current_step
        )
        effective_retrieve_memory = (
            bool(memory) if retrieve_memory is None else bool(retrieve_memory)
        )
        effective_save_memory = (
            bool(memory) if save_memory is None else bool(save_memory)
        )
        effective_extract_memory = bool(
            effective_save_memory and extract_memory
        )
        execution_options = _agent_batch_execution_options(
            max_turns=max_turns,
            output_schema=output_schema,
            reasoning_stages=reasoning_stages,
            memory={
                "retrieve": effective_retrieve_memory,
                "save": effective_save_memory,
                "extract": effective_extract_memory,
                "top_k": memory_top_k,
            },
            terminal_actions=terminal_actions,
            completion_action_tags=completion_action_tags,
            max_action_calls=max_action_calls,
            action_call_limits=action_call_limits,
            required_actions=required_actions,
            required_action_tags=required_action_tags,
            llm_request_options=llm_request_options,
        )
        continued_agent_count = sum(
            1
            for agent_id in self.agent_ids
            if (prior_messages_by_agent or {}).get(agent_id)
        )
        execution_options["continued_agent_count"] = continued_agent_count
        execution_options["current_step"] = effective_current_step

        async def call(agent_id: str) -> AgentCallRecord:
            agent_started = time.time()
            try:
                result = await self.world.instruct_agent(
                    agent_id,
                    instruction,
                    fovs=fovs or [],
                    action_tags=actions,
                    current_step=effective_current_step,
                    model_id=model,
                    output_schema=output_schema,
                    max_turns=max_turns,
                    retrieve_memory=effective_retrieve_memory,
                    save_memory=effective_save_memory,
                    extract_memory=effective_extract_memory,
                    memory_top_k=memory_top_k,
                    name=name,
                    reasoning_stages=reasoning_stages,
                    terminal_action_names=terminal_actions,
                    completion_action_tags=completion_action_tags,
                    required_action_names=required_actions,
                    required_action_tags=required_action_tags,
                    max_action_calls=max_action_calls,
                    action_call_limits=action_call_limits,
                    llm_request_options=llm_request_options,
                    prior_messages=(
                        prior_messages_by_agent or {}
                    ).get(agent_id),
                )
                failure_record = _agent_failure_record(
                    agent_id,
                    result,
                    require_structured_output=output_schema is not None,
                    required_actions=required_actions,
                    required_action_tags=required_action_tags,
                )
                if failure_record is not None:
                    failure_record.duration_sec = time.time() - agent_started
                    return failure_record
                return AgentCallRecord(
                    agent_id,
                    "success",
                    _extract_call_value(result),
                    raw=result,
                    duration_sec=time.time() - agent_started,
                )
            except Exception as exc:
                return AgentCallRecord(
                    agent_id,
                    "error",
                    error=str(exc),
                    duration_sec=time.time() - agent_started,
                )

        effective_concurrency, concurrency_source = _resolve_agent_call_concurrency_info(
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
            concurrency_source=concurrency_source,
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
                concurrency_source=concurrency_source,
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
                concurrency_source=concurrency_source,
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
            concurrency_source=concurrency_source,
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
            action_counts=batch_result.action_counts(),
            successful_action_counts=batch_result.successful_action_counts(),
            failed_action_counts=batch_result.failed_action_counts(),
            action_tag_counts=batch_result.action_tag_counts(),
            action_error_samples=batch_result.action_error_samples(),
            action_duration_summary=batch_result.action_duration_summary(),
            termination_reason_counts=batch_result.termination_reason_counts(),
            memory_summary=batch_result.memory_summary(),
            agent_duration_summary=batch_result.duration_summary(),
            phase_timing_summary=batch_result.phase_timing_summary(),
            error_samples=_logic_error_samples(batch_result.records),
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
            agent_started = time.time()
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
                    failure_record.duration_sec = time.time() - agent_started
                    return failure_record
                return AgentCallRecord(
                    agent_id,
                    "success",
                    _extract_call_value(result),
                    raw=result,
                    duration_sec=time.time() - agent_started,
                )
            except Exception as exc:
                return AgentCallRecord(
                    agent_id,
                    "error",
                    error=str(exc),
                    duration_sec=time.time() - agent_started,
                )

        effective_concurrency, concurrency_source = _resolve_agent_call_concurrency_info(
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
            concurrency_source=concurrency_source,
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
                concurrency_source=concurrency_source,
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
                concurrency_source=concurrency_source,
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
            concurrency_source=concurrency_source,
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
            action_counts=batch_result.action_counts(),
            successful_action_counts=batch_result.successful_action_counts(),
            failed_action_counts=batch_result.failed_action_counts(),
            action_tag_counts=batch_result.action_tag_counts(),
            action_error_samples=batch_result.action_error_samples(),
            action_duration_summary=batch_result.action_duration_summary(),
            termination_reason_counts=batch_result.termination_reason_counts(),
            memory_summary=batch_result.memory_summary(),
            agent_duration_summary=batch_result.duration_summary(),
            phase_timing_summary=batch_result.phase_timing_summary(),
            error_samples=_logic_error_samples(batch_result.records),
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

    def activation_pool(self, *, concurrency: Optional[int] = None) -> Any:
        """Create a temporary dynamic activation pool for this code step."""
        from .activation_pool import ActivationPoolSession

        return ActivationPoolSession(context=self, concurrency=concurrency)

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


def _resolve_memory_timestamp(value: Any) -> int:
    if isinstance(value, bool):
        raise ValueError("current_step/timestamp must be a non-negative integer")
    if isinstance(value, int):
        parsed = value
    elif isinstance(value, str) and value.strip().isdigit():
        parsed = int(value.strip())
    else:
        raise ValueError(
            "current_step/timestamp must be a non-negative integer"
        )
    if parsed < 0:
        raise ValueError("current_step/timestamp must be a non-negative integer")
    return parsed


def _resolve_agent_call_concurrency(
    world: Any,
    *,
    explicit_concurrency: Optional[int],
    model_id: Optional[str],
) -> int:
    return _resolve_agent_call_concurrency_info(
        world,
        explicit_concurrency=explicit_concurrency,
        model_id=model_id,
    )[0]


def _resolve_agent_call_concurrency_info(
    world: Any,
    *,
    explicit_concurrency: Optional[int],
    model_id: Optional[str],
) -> tuple[int, str]:
    if explicit_concurrency is not None:
        return _validate_positive_concurrency(explicit_concurrency, "concurrency"), "explicit"

    default_concurrency = getattr(world, "_default_agent_concurrency", None)
    if default_concurrency is not None:
        source = getattr(world, "_default_agent_concurrency_source", None) or "world_default"
        return (
            _validate_positive_concurrency(default_concurrency, "world._default_agent_concurrency"),
            str(source),
        )

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
                    return _validate_positive_concurrency(model_concurrency, "model concurrency"), "model_provider"
        except Exception:
            pass

    return 5, "default"


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
    required_actions: Optional[List[str]] = None,
    required_action_tags: Optional[List[str]] = None,
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
    if required_actions is not None:
        options["required_actions"] = [str(action) for action in required_actions]
    if required_action_tags is not None:
        options["required_action_tags"] = [str(tag) for tag in required_action_tags]
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


def _round_duration(value: float) -> float:
    return round(float(value), 6)


def _agent_record_timing_sample(record: AgentCallRecord) -> Dict[str, Any]:
    sample: Dict[str, Any] = {
        "agent_id": record.agent_id,
        "status": record.status,
        "duration_sec": _round_duration(float(record.duration_sec or 0.0)),
    }
    payload: Dict[str, Any] = {}
    if isinstance(record.raw, dict):
        payload.update(record.raw)
    if isinstance(record.value, dict):
        payload.update(record.value)
    for key in ("total_turns", "llm_calls", "termination_reason", "model_id"):
        if key in payload:
            sample[key] = _jsonable(payload[key])
    if record.error:
        sample["error"] = record.error
    return sample


def _summarize_agent_record_durations(
    records: Iterable[AgentCallRecord],
    *,
    limit: int = 5,
) -> Dict[str, Any]:
    timed_records = [
        record
        for record in records
        if isinstance(record.duration_sec, (int, float))
    ]
    if not timed_records:
        return {}

    durations = [float(record.duration_sec or 0.0) for record in timed_records]
    slowest = sorted(
        timed_records,
        key=lambda record: float(record.duration_sec or 0.0),
        reverse=True,
    )[:limit]
    return {
        "record_count": len(timed_records),
        "total_sec": _round_duration(sum(durations)),
        "mean_sec": _round_duration(statistics.fmean(durations)),
        "min_sec": _round_duration(min(durations)),
        "max_sec": _round_duration(max(durations)),
        "slowest_agents": [_agent_record_timing_sample(record) for record in slowest],
    }


def _phase_timing_payload_from_record(record: AgentCallRecord) -> Dict[str, Any]:
    payload: Dict[str, Any] = {}
    if isinstance(record.raw, dict):
        payload.update(record.raw)
    if isinstance(record.value, dict):
        payload.update(record.value)
    timings = payload.get("phase_timings")
    return timings if isinstance(timings, dict) else {}


def _summarize_agent_phase_timings(records: Iterable[AgentCallRecord]) -> Dict[str, Any]:
    phase_rows: Dict[str, Dict[str, Any]] = {}
    record_count = 0

    for record in records:
        timings = _phase_timing_payload_from_record(record)
        if not timings:
            continue
        record_count += 1
        for phase_name, duration in timings.items():
            if not isinstance(duration, (int, float)):
                continue
            phase_key = str(phase_name)
            if phase_key == "total":
                continue
            duration_sec = max(float(duration), 0.0)
            row = phase_rows.setdefault(
                phase_key,
                {
                    "record_count": 0,
                    "total_sec": 0.0,
                    "max_sec": 0.0,
                },
            )
            row["record_count"] += 1
            row["total_sec"] += duration_sec
            row["max_sec"] = max(float(row["max_sec"]), duration_sec)

    if not phase_rows:
        return {}

    phases: Dict[str, Dict[str, Any]] = {}
    for phase_name, row in sorted(phase_rows.items()):
        count = int(row["record_count"])
        total = float(row["total_sec"])
        phases[phase_name] = {
            "record_count": count,
            "total_sec": _round_duration(total),
            "mean_sec": _round_duration(total / count) if count else 0.0,
            "max_sec": _round_duration(float(row["max_sec"])),
        }

    bottleneck = max(phases.items(), key=lambda item: item[1]["total_sec"])[0]
    return {
        "record_count": record_count,
        "bottleneck": bottleneck,
        "phases": phases,
    }


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
    concurrency_source: str,
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
    action_counts: Optional[Dict[str, int]] = None,
    successful_action_counts: Optional[Dict[str, int]] = None,
    failed_action_counts: Optional[Dict[str, int]] = None,
    action_tag_counts: Optional[Dict[str, int]] = None,
    action_error_samples: Optional[List[Dict[str, Any]]] = None,
    action_duration_summary: Optional[Dict[str, Any]] = None,
    termination_reason_counts: Optional[Dict[str, int]] = None,
    memory_summary: Optional[Dict[str, Any]] = None,
    agent_duration_summary: Optional[Dict[str, Any]] = None,
    phase_timing_summary: Optional[Dict[str, Any]] = None,
    error_samples: Optional[List[Dict[str, Any]]] = None,
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
            "concurrency_source": concurrency_source,
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
        if action_counts is not None:
            event_data["action_counts"] = _jsonable(action_counts)
        if successful_action_counts is not None:
            event_data["successful_action_counts"] = _jsonable(successful_action_counts)
        if failed_action_counts is not None:
            event_data["failed_action_counts"] = _jsonable(failed_action_counts)
        if action_tag_counts is not None:
            event_data["action_tag_counts"] = _jsonable(action_tag_counts)
        if action_error_samples:
            event_data["action_error_samples"] = _jsonable(action_error_samples)
        if action_duration_summary:
            event_data["action_duration_summary"] = _jsonable(action_duration_summary)
        if termination_reason_counts:
            event_data["termination_reason_counts"] = _jsonable(termination_reason_counts)
        if memory_summary:
            event_data["memory_summary"] = _jsonable(memory_summary)
        if agent_duration_summary:
            event_data["agent_duration_summary"] = _jsonable(agent_duration_summary)
        if phase_timing_summary:
            event_data["phase_timing_summary"] = _jsonable(phase_timing_summary)
        if error_samples:
            event_data["error_samples"] = _jsonable(error_samples)

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
                    *MEMORY_TABLE_KEYS,
                ):
                    if key in result and key not in compact:
                        compact[key] = result[key]
                if "extracted_memories" in result and "extracted_memory_count" not in compact:
                    memories = result.get("extracted_memories")
                    if isinstance(memories, list):
                        compact["extracted_memory_count"] = len(memories)
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
            *MEMORY_TABLE_KEYS,
        ):
            if key in result:
                compact[key] = result[key]
        if "extracted_memories" in result and "extracted_memory_count" not in compact:
            memories = result.get("extracted_memories")
            if isinstance(memories, list):
                compact["extracted_memory_count"] = len(memories)
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


def _action_counts_by_status(actions: List[Dict[str, Any]], *, success: bool) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for action in actions:
        name = action.get("action_name")
        if not name:
            continue
        is_success = str(action.get("status") or "success").lower() == "success"
        if is_success != success:
            continue
        action_key = str(name)
        counts[action_key] = counts.get(action_key, 0) + 1
    return counts


def _action_error_samples(actions: List[Dict[str, Any]], *, limit: int = 5) -> List[Dict[str, Any]]:
    samples: List[Dict[str, Any]] = []
    for action in actions:
        if str(action.get("status") or "success").lower() == "success":
            continue
        if len(samples) >= limit:
            break
        sample = {
            "agent_id": action.get("agent_id"),
            "action_name": action.get("action_name"),
            "status": action.get("status"),
            "error": action.get("error") or action.get("result"),
        }
        arguments = action.get("arguments")
        if isinstance(arguments, dict):
            sample["arguments"] = _compact_action_mapping(arguments)
        samples.append({key: _jsonable(value) for key, value in sample.items() if value is not None})
    return samples


def _summarize_action_durations(actions: List[Dict[str, Any]], *, limit: int = 5) -> Dict[str, Any]:
    timed_actions = [
        action
        for action in actions
        if isinstance(action.get("duration_sec"), (int, float))
    ]
    if not timed_actions:
        return {}

    by_action: Dict[str, Dict[str, Any]] = {}
    total_sec = 0.0
    for action in timed_actions:
        action_name = str(action.get("action_name") or "unknown_action")
        duration = max(float(action.get("duration_sec") or 0.0), 0.0)
        total_sec += duration
        row = by_action.setdefault(
            action_name,
            {
                "record_count": 0,
                "total_sec": 0.0,
                "max_sec": 0.0,
            },
        )
        row["record_count"] += 1
        row["total_sec"] += duration
        row["max_sec"] = max(float(row["max_sec"]), duration)

    finalized_by_action: Dict[str, Dict[str, Any]] = {}
    for action_name, row in sorted(by_action.items()):
        count = int(row["record_count"])
        total = float(row["total_sec"])
        finalized_by_action[action_name] = {
            "record_count": count,
            "total_sec": _round_duration(total),
            "mean_sec": _round_duration(total / count) if count else 0.0,
            "max_sec": _round_duration(float(row["max_sec"])),
        }

    slowest_actions = []
    for action in sorted(
        timed_actions,
        key=lambda item: float(item.get("duration_sec") or 0.0),
        reverse=True,
    )[:limit]:
        sample = {
            "agent_id": action.get("agent_id"),
            "action_name": action.get("action_name"),
            "status": action.get("status"),
            "duration_sec": _round_duration(float(action.get("duration_sec") or 0.0)),
        }
        if action.get("error"):
            sample["error"] = action.get("error")
        slowest_actions.append({key: _jsonable(value) for key, value in sample.items() if value is not None})

    bottleneck_action = max(
        finalized_by_action.items(),
        key=lambda item: item[1]["total_sec"],
    )[0]
    return {
        "record_count": len(timed_actions),
        "total_sec": _round_duration(total_sec),
        "mean_sec": _round_duration(total_sec / len(timed_actions)),
        "bottleneck_action": bottleneck_action,
        "by_action": finalized_by_action,
        "slowest_actions": slowest_actions,
    }


def _memory_payload_from_record(record: AgentCallRecord) -> Dict[str, Any]:
    payload: Dict[str, Any] = {}
    if isinstance(record.raw, dict):
        payload.update(record.raw)
    if isinstance(record.value, dict):
        payload.update(record.value)
    return payload


def _has_memory_diagnostics(payload: Dict[str, Any]) -> bool:
    return any(key in payload for key in MEMORY_DIAGNOSTIC_KEYS)


def _summarize_memory_diagnostics(rows: Iterable[tuple[Optional[str], Dict[str, Any]]]) -> Dict[str, Any]:
    summary: Dict[str, Any] = {
        "record_count": 0,
        "retrieve_enabled_count": 0,
        "save_enabled_count": 0,
        "extraction_enabled_count": 0,
        "extraction_success_count": 0,
        "extraction_error_count": 0,
        "extracted_memory_count": 0,
        "top_k_values": set(),
        "error_samples": [],
    }
    for agent_id, payload in rows:
        if not isinstance(payload, dict) or not _has_memory_diagnostics(payload):
            continue
        summary["record_count"] += 1
        if payload.get("memory_retrieved") is True:
            summary["retrieve_enabled_count"] += 1
        if payload.get("memory_saved") is True:
            summary["save_enabled_count"] += 1
        extraction_enabled = payload.get("memory_extraction_enabled") is True
        if extraction_enabled:
            summary["extraction_enabled_count"] += 1
            if payload.get("memory_extraction_success") is True:
                summary["extraction_success_count"] += 1
            else:
                summary["extraction_error_count"] += 1
                error = payload.get("memory_extraction_error")
                if error and len(summary["error_samples"]) < 5:
                    sample: Dict[str, Any] = {"error": str(error)}
                    if agent_id:
                        sample["agent_id"] = str(agent_id)
                    summary["error_samples"].append(sample)
        top_k = payload.get("memory_top_k")
        if isinstance(top_k, (int, float)) and int(top_k) > 0:
            summary["top_k_values"].add(int(top_k))
        extracted_count = payload.get("extracted_memory_count")
        if isinstance(extracted_count, (int, float)):
            summary["extracted_memory_count"] += int(extracted_count)
        else:
            extracted_memories = payload.get("extracted_memories")
            if isinstance(extracted_memories, list):
                summary["extracted_memory_count"] += len(extracted_memories)

    if not summary["record_count"]:
        return {}
    summary["top_k_values"] = sorted(summary["top_k_values"])
    if not summary["error_samples"]:
        summary.pop("error_samples", None)
    return summary


def _missing_required_actions(result: Dict[str, Any], required_actions: Optional[List[str]]) -> List[str]:
    required = [str(action) for action in (required_actions or [])]
    if not required:
        return []
    successful_actions = set()
    for item in result.get("actions") or []:
        if not isinstance(item, dict):
            continue
        action_name = item.get("action_name") or item.get("name") or item.get("action")
        if action_name is None:
            continue
        if str(item.get("status") or "success").lower() == "error":
            continue
        successful_actions.add(str(action_name))
    return [action for action in required if action not in successful_actions]


def _missing_required_action_tags(result: Dict[str, Any], required_tags: Optional[List[str]]) -> List[str]:
    required = [str(tag).lower() for tag in (required_tags or []) if str(tag).strip()]
    if not required:
        return []
    successful_tags = set()
    for item in result.get("actions") or []:
        if not isinstance(item, dict):
            continue
        if str(item.get("status") or "success").lower() == "error":
            continue
        action_name = item.get("action_name") or item.get("name") or item.get("action")
        if action_name is not None:
            successful_tags.add(str(action_name).lower())
            if "." in str(action_name):
                successful_tags.add(str(action_name).rsplit(".", maxsplit=1)[-1].lower())
        successful_tags.update(str(tag).lower() for tag in (item.get("tags") or []))
    return [tag for tag in required if tag not in successful_tags]


def _agent_failure_record(
    agent_id: str,
    result: Any,
    *,
    require_structured_output: bool = False,
    required_actions: Optional[List[str]] = None,
    required_action_tags: Optional[List[str]] = None,
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
        missing_actions = _missing_required_actions(result, required_actions)
        if missing_actions:
            return AgentCallRecord(
                agent_id=agent_id,
                status="error",
                value=_extract_call_value(result),
                error=f"missing required action(s): {', '.join(missing_actions)}",
                raw=result,
            )
        missing_tags = _missing_required_action_tags(result, required_action_tags)
        if missing_tags:
            return AgentCallRecord(
                agent_id=agent_id,
                status="error",
                value=_extract_call_value(result),
                error=f"missing required action tag(s): {', '.join(missing_tags)}",
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


def _normalize_capability_kind(kind: str) -> str:
    normalized = str(kind).strip().lower()
    aliases = {
        "fov": "fov",
        "fovs": "fov",
        "field_of_view": "fov",
        "field_of_views": "fov",
        "action": "action",
        "actions": "action",
        "tool": "action",
        "tools": "action",
        "rule": "rule",
        "rules": "rule",
        "behavior": "behavior",
        "behaviors": "behavior",
    }
    if normalized in aliases:
        return aliases[normalized]
    raise ValueError(f"Unknown capability kind: {kind}")


def _capability_entries(table: Dict[str, Dict[str, Any]], kind: str) -> List[Dict[str, Any]]:
    entries: List[Dict[str, Any]] = []
    for key, entry in table.items():
        canonical_id = entry.get("canonical_id") or key
        display_name = entry.get("display_name") or key.rsplit(".", maxsplit=1)[-1]
        meta = entry.get("meta")
        func_name = entry.get("func_name")
        parameters = entry.get("parameters", {})
        return_value_schema = entry.get("return_value_schema", {})
        state_access = entry.get("state_access_declaration")
        cache_on_step = None
        cache_on_agent = None
        if meta is not None:
            display_name = getattr(meta, "name", display_name) or display_name
            canonical_id = getattr(meta, "canonical_id", None) or canonical_id
            func_name = func_name or getattr(meta, "func_name", None)
            parameters = entry.get("parameters") or getattr(meta, "parameters_schema", {}) or {}
            return_value_schema = (
                entry.get("return_value_schema")
                or getattr(meta, "return_value_schema", {})
                or {}
            )
            state_access = state_access or getattr(meta, "state_access_declaration", None)
            cache_on_step = getattr(meta, "cache_on_step", None)
            cache_on_agent = getattr(meta, "cache_on_agent", None)
        aliases = _capability_aliases(
            canonical_id=canonical_id,
            display_name=display_name,
            key=key,
            func_name=func_name,
        )
        entries.append(
            {
                "id": canonical_id,
                "name": display_name,
                "kind": entry.get("kind") or kind,
                "source": entry.get("source") or "unknown",
                "description": entry.get("description", ""),
                "tags": list(entry.get("tags", []) or []),
                "parameters": _jsonable(parameters),
                "return_value_schema": _jsonable(return_value_schema),
                "state_access": _jsonable(state_access),
                "key": key,
                "func_name": func_name,
                "aliases": aliases,
                "environment_type": entry.get("environment_type"),
                "cache_on_step": cache_on_step,
                "cache_on_agent": cache_on_agent,
            }
        )
    return _dedupe_capability_entries(entries)


def _capability_aliases(
    *,
    canonical_id: Any,
    display_name: Any,
    key: Any,
    func_name: Any,
) -> List[str]:
    aliases: List[str] = []
    for value in (canonical_id, display_name, key, func_name):
        if value is None:
            continue
        text = str(value)
        if not text:
            continue
        aliases.append(text)
        if "." in text:
            aliases.append(text.rsplit(".", maxsplit=1)[-1])
    return list(dict.fromkeys(aliases))


def _capability_entry_matches(entry: Dict[str, Any], name: str) -> bool:
    target = str(name)
    aliases = {str(alias) for alias in entry.get("aliases", []) if alias is not None}
    aliases.update(
        str(value)
        for value in (
            entry.get("id"),
            entry.get("name"),
            entry.get("key"),
            entry.get("func_name"),
        )
        if value is not None
    )
    if target in aliases:
        return True
    lowered = target.lower()
    return lowered in {alias.lower() for alias in aliases}


def _dedupe_capability_entries(entries: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    deduped: List[Dict[str, Any]] = []
    by_identity: Dict[tuple[str, str, str], Dict[str, Any]] = {}
    for entry in entries:
        identity = (
            str(entry.get("kind")),
            str(entry.get("id") or entry.get("key")),
            str(entry.get("name")),
        )
        existing = by_identity.get(identity)
        if existing is not None:
            existing["aliases"] = list(
                dict.fromkeys([*(existing.get("aliases") or []), *(entry.get("aliases") or [])])
            )
            continue
        by_identity[identity] = entry
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
        matching_other_kinds = []
        for other_kind in ("fov", "action", "rule", "behavior"):
            if other_kind == kind:
                continue
            if catalog.get(other_kind, name) is not None:
                matching_other_kinds.append(other_kind)
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
