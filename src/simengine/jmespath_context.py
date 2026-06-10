"""JMESPath 上下文聚合器。

该模块提供 `JmespathContextBuilder`，用于在 StepFlow 执行过程中收集
selector/operator/converter 的快照，并构建统一的 JMESPath 根上下文。
"""

from __future__ import annotations

from types import MappingProxyType
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple


@dataclass
class SelectorSnapshot:
    """记录 selector 的配置与命中情况。"""

    params: Dict[str, Any]
    match_count: int
    matched_ids: List[str]
    diagnostics: Dict[str, Any] = field(default_factory=dict)
    _view: Dict[str, Any] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._view = {
            "params": self.params,
            "match_count": self.match_count,
            "matched_ids": self.matched_ids,
        }
        if self.diagnostics:
            self._view["diagnostics"] = self.diagnostics

    def as_view(self) -> Dict[str, Any]:
        if self.diagnostics:
            self._view["diagnostics"] = self.diagnostics
        elif "diagnostics" in self._view:
            self._view.pop("diagnostics", None)
        return self._view


@dataclass
class OperatorExecutionRecord:
    """记录单个 operator 在特定目标上的执行结果。"""

    agent_id: str
    status: str
    output: Any
    result: Any
    value: Any
    structured_output: Any
    error_message: Optional[str]
    metadata: Dict[str, Any]
    execution_time: Optional[float]
    extras: Dict[str, Any] = field(default_factory=dict)
    inputs: Optional[Dict[str, Any]] = None
    _view: Dict[str, Any] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        view: Dict[str, Any] = {
            "agent_id": self.agent_id,
            "status": self.status,
            "output": self.output,
            "result": self.result,
            "value": self.value,
            "error_message": self.error_message,
            "execution_time": self.execution_time,
            "metadata": self.metadata,
        }
        if self.structured_output is not None:
            view["structured_output"] = self.structured_output
        if self.inputs is not None:
            view["inputs"] = self.inputs
        if self.extras:
            view.update(self.extras)
        self._view = view

    def to_dict(self) -> Dict[str, Any]:
        return self._view


@dataclass
class OperatorSnapshot:
    """聚合单个 operator 在当前步内的执行记录。"""

    id: str
    node_id: str
    type: str
    description: Optional[str]
    executions: List[OperatorExecutionRecord] = field(default_factory=list)
    name: Optional[str] = None
    _view: Dict[str, Any] = field(init=False, repr=False)
    _aggregated_outputs: Dict[str, Any] = field(default_factory=dict, init=False, repr=False)

    def __post_init__(self) -> None:
        self._view = {
            "id": self.id,
            "node_id": self.node_id,
            "type": self.type,
            "executions": [],
            "agents": {},
            "status_summary": {},
            "output": None,
            "result": None,
            "by_index": [],
            "by_agent": {},
            "outputs": [],
            "inputs_by_index": [],
            "inputs_by_agent": {},
        }
        if self.description:
            self._view["desc"] = self.description

    def add_execution(self, record: OperatorExecutionRecord) -> None:
        self.executions.append(record)
        execution_view = record.to_dict()
        agent_id = record.agent_id

        executions_list = self._view["executions"]
        executions_list.append(execution_view)
        self._view["by_index"].append(execution_view)
        self._view["outputs"].append(execution_view.get("output"))
        self._view["inputs_by_index"].append(execution_view.get("inputs"))

        if agent_id not in (None, ""):
            self._view["agents"][agent_id] = execution_view
            self._view["by_agent"][agent_id] = execution_view
            self._view["inputs_by_agent"][agent_id] = execution_view.get("inputs")

        status = execution_view.get("status")
        if status:
            summary = self._view["status_summary"]
            summary[status] = summary.get(status, 0) + 1

        output_value = execution_view.get("output")
        if len(self.executions) == 1:
            self._aggregated_outputs.clear()
            if agent_id not in (None, "", "global"):
                self._aggregated_outputs[agent_id] = output_value
            self._view["output"] = output_value
            self._view["result"] = output_value
            inputs_value = execution_view.get("inputs")
            if inputs_value is not None:
                self._view["inputs"] = inputs_value
        else:
            if not self._aggregated_outputs and len(self.executions) >= 2:
                first_view = self.executions[0].to_dict()
                first_agent = first_view.get("agent_id")
                first_output = first_view.get("output")
                if first_agent not in (None, "", "global"):
                    self._aggregated_outputs[first_agent] = first_output
            if agent_id not in (None, "", "global"):
                self._aggregated_outputs[agent_id] = output_value
            if self._aggregated_outputs:
                self._view["output"] = self._aggregated_outputs
                self._view["result"] = self._aggregated_outputs
            else:
                self._view["output"] = output_value
                self._view["result"] = output_value
            self._view.pop("inputs", None)

        if self.name is not None:
            self._view["name"] = self.name

    def to_dict(self) -> Dict[str, Any]:
        return self._view


@dataclass
class NodeSnapshot:
    """记录单个节点在当前步内的上下文快照。"""

    id: str
    selector: Optional[SelectorSnapshot] = None
    inputs: Dict[str, Any] = field(default_factory=dict)
    operators: Dict[str, OperatorSnapshot] = field(default_factory=dict)
    converter_output: Optional[Dict[str, Any]] = None
    _view: Dict[str, Any] = field(init=False, repr=False)

    def ensure_operator(
        self,
        operator_id: str,
        operator_type: str,
        description: Optional[str],
    ) -> OperatorSnapshot:
        if operator_id not in self.operators:
            self.operators[operator_id] = OperatorSnapshot(
                id=operator_id,
                node_id=self.id,
                type=operator_type,
                description=description,
            )
            self._view["operators"][operator_id] = self.operators[operator_id].to_dict()
        return self.operators[operator_id]

    def __post_init__(self) -> None:
        self._view = {
            "id": self.id,
            "inputs": self.inputs,
            "operators": {},
        }

    def update_selector_view(self) -> None:
        if self.selector is None:
            self._view.pop("selector", None)
        else:
            self._view["selector"] = self.selector.as_view()

    def update_inputs_view(self) -> None:
        self._view["inputs"] = self.inputs

    def update_converter_view(self) -> None:
        if self.converter_output is None:
            self._view.pop("converter", None)
        else:
            self._view["converter"] = {"output": self.converter_output}

    def to_dict(self) -> Dict[str, Any]:
        return self._view


class JmespathContextBuilder:
    """负责构建 StepFlow 的 JMESPath 根上下文。"""

    def __init__(self) -> None:
        self._step_number: int = 0
        self._nodes: Dict[str, NodeSnapshot] = {}
        self._selector_diagnostics: List[Dict[str, Any]] = []
        self._cached_world_snapshot: Optional[MappingProxyType] = None
        self._cached_world_version: Optional[int] = None
        self._nodes_view: Dict[str, Dict[str, Any]] = {}
        self._operators_index: Dict[str, Dict[str, Any]] = {}
        self._root_data: Dict[str, Any] = {}
        self._root_view: MappingProxyType = MappingProxyType({})
        self._initialize_root(step_number=0)

    def reset(self, step_number: int) -> None:
        """重置状态，准备记录新的 StepFlow 执行。"""
        self._step_number = step_number
        self._nodes = {}
        self._nodes_view = {}
        self._operators_index = {}
        self._selector_diagnostics = []
        self._cached_world_snapshot = None
        self._cached_world_version = None
        self._initialize_root(step_number=step_number)

    def record_node_inputs(self, node_id: str, inputs: Dict[str, Any]) -> None:
        node = self._ensure_node(node_id)
        node.inputs = dict(inputs or {})
        node.update_inputs_view()

    def record_selector(
        self,
        node_id: str,
        selector_params: Dict[str, Any],
        targets: Optional[List[Any]],
    ) -> None:
        node = self._ensure_node(node_id)
        params_copy = dict(selector_params or {})
        matched_ids: List[str] = []
        if targets:
            for target in targets:
                target_id = getattr(target, "id", None) or getattr(target, "name", None)
                if target_id is None and hasattr(target, "archetype"):
                    target_id = getattr(target, "archetype")
                if target_id is None:
                    target_id = target.__class__.__name__
                matched_ids.append(str(target_id))

        snapshot = SelectorSnapshot(
            params=params_copy,
            match_count=len(targets or []),
            matched_ids=matched_ids,
            diagnostics={},
        )
        if snapshot.match_count == 0:
            snapshot.diagnostics["warnings"] = ["selector_returned_empty"]
        node.selector = snapshot
        node.update_selector_view()
        diagnostic_entry = {"node_id": node_id, **dict(snapshot.as_view())}
        self._selector_diagnostics.append(diagnostic_entry)

    def record_operator_result(
        self,
        *,
        node_id: str,
        operator_id: str,
        operator_type: str,
        description: Optional[str],
        execution: Dict[str, Any],
    ) -> None:
        node = self._ensure_node(node_id)
        snapshot = node.ensure_operator(operator_id, operator_type, description)
        exec_inputs = execution.get("inputs") if isinstance(execution.get("inputs"), dict) else None
        exec_name = execution.get("name")

        if snapshot.name is None and isinstance(exec_name, str):
            snapshot.name = exec_name
            snapshot._view["name"] = exec_name

        record = OperatorExecutionRecord(
            agent_id=str(execution.get("agent_id", "unknown")),
            status=str(execution.get("status", "unknown")),
            output=execution.get("output"),
            result=execution.get("result"),
            value=execution.get("value"),
            structured_output=execution.get("structured_output"),
            error_message=execution.get("error_message"),
            metadata=execution.get("metadata") or {},
            execution_time=execution.get("execution_time"),
            extras={
                key: execution[key]
                for key in execution
                if key not in {
                    "agent_id",
                    "status",
                    "output",
                    "result",
                    "value",
                    "structured_output",
                    "error_message",
                    "metadata",
                    "execution_time",
                    "inputs",
                    "name",
                }
            },
            inputs=exec_inputs,
        )
        snapshot.add_execution(record)
        self._operators_index[operator_id] = snapshot.to_dict()

    def record_converter_output(self, node_id: str, output: Dict[str, Any]) -> None:
        node = self._ensure_node(node_id)
        node.converter_output = output
        node.update_converter_view()

    def _get_world_snapshot(self, world: Any) -> Tuple[MappingProxyType, Optional[int]]:
        """根据世界状态版本缓存 world 快照。"""
        version_getter = getattr(world, "get_state_version", None)
        version = version_getter() if callable(version_getter) else None
        if version is not None:
            if (
                self._cached_world_snapshot is None
                or self._cached_world_version != version
            ):
                self._cached_world_snapshot = self._build_world_snapshot(world)
                self._cached_world_version = version
            return self._cached_world_snapshot, version
        snapshot = self._build_world_snapshot(world)
        return snapshot, None

    def build_root(
        self,
        *,
        world: Any,
        step_context: Dict[str, Any],
    ) -> Dict[str, Any]:
        world_snapshot, world_version = self._get_world_snapshot(world)

        self._root_data["step"]["number"] = self._step_number
        self._root_data["world"] = world_snapshot
        self._root_data["world_version"] = world_version
        self._root_data["step_context"] = step_context
        self._root_data["debug"]["selectors"] = self._selector_diagnostics
        return self._root_view

    def _ensure_node(self, node_id: str) -> NodeSnapshot:
        if node_id not in self._nodes:
            self._nodes[node_id] = NodeSnapshot(id=node_id)
            self._nodes_view[node_id] = self._nodes[node_id].to_dict()
            self._root_data["nodes"] = self._nodes_view
        return self._nodes[node_id]

    def _build_world_snapshot(self, world: Any) -> MappingProxyType:
        agents_data = getattr(world, "agents_data", {})
        environment_data = getattr(world, "environment_data", {})
        snapshot = {
            "step": getattr(world, "step", None),
            "agents_data": agents_data,
            "environment_data": environment_data,
            "agents": agents_data,
        }
        return MappingProxyType(snapshot)

    def _initialize_root(self, step_number: int) -> None:
        self._root_data = {
            "step": {"number": step_number},
            "nodes": self._nodes_view,
            "operators": self._operators_index,
            "world": MappingProxyType({}),
            "world_version": None,
            "step_context": {},
            "debug": {"selectors": self._selector_diagnostics},
        }
        self._root_view = MappingProxyType(self._root_data)
