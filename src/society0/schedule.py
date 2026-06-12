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
            raise ValueError(f"Behavior '{behavior_name}' not found")

        resolved_name, behavior_info = resolved
        behavior_func = behavior_info["function"]
        behavior_sig = behavior_info.get("signature") or inspect.signature(behavior_func)

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

        return AgentBatchResult(await _run_limited(self.agent_ids, call, concurrency))

    async def instruct(
        self,
        instruction: str,
        *,
        fovs: List[str] | None = None,
        actions: List[str] | None = None,
        output: Any = None,
        memory: bool = True,
        model: Optional[str] = None,
        max_turns: int = 3,
        concurrency: Optional[int] = None,
        name: Optional[str] = None,
        reasoning_stages: Optional[List[Dict[str, Any]]] = None,
    ) -> AgentBatchResult:
        output_schema = _normalize_output_schema(output)

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
                    name=name,
                    reasoning_stages=reasoning_stages,
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
        return AgentBatchResult(await _run_limited(self.agent_ids, call, effective_concurrency))

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
    ) -> AgentBatchResult:
        output_schema = _normalize_output_schema(output)

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
                    max_turns=max_turns,
                    name=name,
                    reasoning_stages=reasoning_stages,
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
        return AgentBatchResult(await _run_limited(self.agent_ids, call, effective_concurrency))


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
            raise ValueError(f"Rule '{rule_name}' not found")

        resolved_name, rule_info = resolved
        rule_func = rule_info["function"]
        rule_sig = rule_info.get("signature") or inspect.signature(rule_func)
        env = self.world.get_environment()
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
        result = await invoke_maybe_async(rule_func, **call_kwargs)
        return _extract_call_value(result)

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

    async def execute_tick(self, *, tick: int, world: Any, log: Any = None) -> List[Dict[str, Any]]:
        if not self.steps:
            raise RuntimeError("No code steps registered")
        results = []
        env = world.get_environment()
        for code_step in self.steps:
            started = time.time()
            ctx = StepContext(
                step=tick,
                step_name=code_step.name,
                world=world,
                env=env,
                params=dict(code_step.params),
                log=log,
            )
            result = await code_step.fn(ctx)
            if result is None:
                result = StepResult()
            if not isinstance(result, StepResult):
                raise TypeError(f"Step '{code_step.name}' returned {type(result).__name__}, expected StepResult or None")
            results.append(
                {
                    "step": tick,
                    "step_name": code_step.name,
                    "duration_sec": time.time() - started,
                    "result": result.to_dict(),
                }
            )
        return results


async def _run_limited(
    items: Iterable[str],
    call: Callable[[str], Awaitable[AgentCallRecord]],
    concurrency: Optional[int],
) -> List[AgentCallRecord]:
    item_list = list(items)
    if concurrency is None or concurrency <= 0:
        return list(await asyncio.gather(*(call(item) for item in item_list)))
    semaphore = asyncio.Semaphore(concurrency)

    async def guarded(item: str) -> AgentCallRecord:
        async with semaphore:
            return await call(item)

    return list(await asyncio.gather(*(guarded(item) for item in item_list)))


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
            return structured
        value = result.get("value")
        if value is not None:
            return value
        return result
    return result


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
