"""
Event System: 统一事件系统

这个模块定义了统一状态架构中的所有事件类型。
事件是系统中状态变更和重要操作的原子记录单位。
"""

from typing import Any, Dict, List, Optional, Union
from dataclasses import dataclass, field
from datetime import datetime
import uuid


@dataclass
class BaseEvent:
    """
    事件基类
    
    所有事件都包含基本的元信息：
    - 事件ID（唯一标识）
    - 事件类型
    - 时间戳
    - 上下文栈（记录事件发生时的完整调用路径）
    """
    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    event_type: str = field(default="UNKNOWN")
    timestamp: datetime = field(default_factory=datetime.now)
    context_stack: List[Dict[str, Any]] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典格式，用于序列化"""
        return {
            "event_id": self.event_id,
            "event_type": self.event_type,
            "timestamp": self.timestamp.isoformat(),
            "context_stack": self.context_stack,
            "metadata": self.metadata
        }
    
    def get_current_step(self) -> Optional[str]:
        """从上下文栈中获取当前步骤"""
        for frame in reversed(self.context_stack):
            if frame.get("type") == "step":
                return frame.get("id")
        return None
    
    def get_current_node(self) -> Optional[str]:
        """从上下文栈中获取当前节点"""
        for frame in reversed(self.context_stack):
            if frame.get("type") == "node":
                return frame.get("id")
        return None
    
    def get_current_operator(self) -> Optional[str]:
        """从上下文栈中获取当前操作器"""
        for frame in reversed(self.context_stack):
            if frame.get("type") == "operator":
                return frame.get("id")
        return None


@dataclass
class StateChangeEvent(BaseEvent):
    """
    状态变更事件
    
    记录对世界状态（agents, environment）的原子修改操作。
    这是最重要的事件类型，所有状态变更都会生成此类事件。
    """
    event_type: str = field(default="STATE_CHANGE", init=False)
    
    # 变更的具体内容
    target_type: str = ""  # "agent" 或 "environment"
    target_id: str = ""    # agent_id 或 "global"（对于 environment）
    path: List[str] = field(default_factory=list)  # 状态路径，如 ["state", "inventory", "food"]
    operation: str = ""    # 操作类型："set", "delete", "append", "extend", etc.
    value: Any = None      # 新值（如果适用）
    old_value: Any = None  # 旧值（如果适用，用于回滚）
    
    def to_dict(self) -> Dict[str, Any]:
        """扩展基类序列化，包含状态变更信息"""
        base_dict = super().to_dict()
        base_dict.update({
            "target_type": self.target_type,
            "target_id": self.target_id,
            "path": self.path,
            "operation": self.operation,
            "value": self.value,
            "old_value": self.old_value
        })
        return base_dict
    
    @classmethod
    def from_proxy_change(cls, change: Dict[str, Any], context_stack: List[Dict[str, Any]], 
                         target_type: str, target_id: str) -> 'StateChangeEvent':
        """
        从代理拦截的变更信息创建事件
        
        Args:
            change: 代理记录的变更信息
            context_stack: 当前上下文栈
            target_type: "agent" 或 "environment"
            target_id: 具体的目标ID
        """
        return cls(
            target_type=target_type,
            target_id=target_id,
            path=list(change.get("path", [])),
            operation=change.get("operation", "unknown"),
            value=change.get("value"),
            old_value=change.get("old_value"),
            context_stack=context_stack
        )
    
    def get_full_path(self) -> str:
        """获取完整的状态路径字符串"""
        if self.target_type == "agent":
            return f"agents.{self.target_id}.{'.'.join(self.path)}"
        elif self.target_type == "environment":
            return f"environment.{'.'.join(self.path)}"
        else:
            return ".".join(self.path)


@dataclass
class MemoryChangeEvent(BaseEvent):
    """
    记忆变更事件
    
    记录对 Agent 记忆系统（Milvus）的原子修改操作。
    与 StateChangeEvent 不同，这种事件涉及向量数据库操作。
    """
    event_type: str = field(default="MEMORY_CHANGE", init=False)
    
    # 记忆变更的具体内容
    agent_id: str = ""           # 哪个 Agent 的记忆
    operation: str = ""          # 操作类型："add", "update", "delete", "search"
    memory_id: Optional[str] = None  # 记忆片段的唯一标识（Milvus ID）
    memory_type: str = ""        # 记忆类型："experience", "observation", "reflection", etc.
    content: Any = None          # 记忆内容
    embedding: Optional[List[float]] = None  # 向量表示（如果适用）
    
    def to_dict(self) -> Dict[str, Any]:
        """扩展基类序列化，包含记忆变更信息"""
        base_dict = super().to_dict()
        base_dict.update({
            "agent_id": self.agent_id,
            "operation": self.operation,
            "memory_id": self.memory_id,
            "memory_type": self.memory_type,
            "content": self.content,
            # 不序列化 embedding，太大了
            "has_embedding": self.embedding is not None
        })
        return base_dict


@dataclass
class NodeExecutionEvent(BaseEvent):
    """
    节点执行事件
    
    记录 Schedule 中节点的执行情况，包括成功和失败。
    """
    event_type: str = field(default="NODE_EXECUTION", init=False)
    
    # 节点执行信息
    step_id: str = ""
    node_id: str = ""
    status: str = ""             # "started", "completed", "failed"
    execution_time: float = 0.0  # 执行耗时（秒）
    error_message: Optional[str] = None  # 错误信息（如果失败）
    result_summary: Dict[str, Any] = field(default_factory=dict)  # 执行结果摘要
    
    def to_dict(self) -> Dict[str, Any]:
        """扩展基类序列化，包含节点执行信息"""
        base_dict = super().to_dict()
        base_dict.update({
            "step_id": self.step_id,
            "node_id": self.node_id,
            "status": self.status,
            "execution_time": self.execution_time,
            "error_message": self.error_message,
            "result_summary": self.result_summary
        })
        return base_dict


@dataclass
class InstructionEvent(BaseEvent):
    """
    指令执行事件
    
    记录 Agent 的 instruct 调用和执行情况。
    """
    event_type: str = field(default="INSTRUCTION", init=False)
    
    # 指令信息
    agent_id: str = ""
    instruction: str = ""
    is_memory: bool = True
    fovs_used: List[str] = field(default_factory=list)
    output_schema: Optional[Dict[str, Any]] = None
    
    # 执行结果
    status: str = ""  # "success", "error", "partial"
    performative_output: str = ""
    structured_output: Optional[Dict[str, Any]] = None
    total_turns: int = 0
    finish_instruction_called: bool = False
    
    def to_dict(self) -> Dict[str, Any]:
        """扩展基类序列化，包含指令执行信息"""
        base_dict = super().to_dict()
        base_dict.update({
            "agent_id": self.agent_id,
            "instruction": self.instruction,
            "is_memory": self.is_memory,
            "fovs_used": self.fovs_used,
            "output_schema": self.output_schema,
            "status": self.status,
            "performative_output": self.performative_output,
            "structured_output": self.structured_output,
            "total_turns": self.total_turns,
            "finish_instruction_called": self.finish_instruction_called
        })
        return base_dict


@dataclass
class StepSummaryEvent(BaseEvent):
    """
    步骤摘要事件 - 记录每个simulation step的完整执行结果

    这个事件提供了步骤级别的完整上下文和结果摘要，包括：
    - 完整的上下文栈信息（step -> nodes -> operators等）
    - 所有节点的执行结果和转换器输出
    - 步骤级别的聚合指标和统计信息
    - 执行时间和性能指标

    设计原则：
    1. 完整性：记录步骤的完整执行路径和上下文
    2. 分析性：提供足够的信息用于后续量化分析
    3. 可追溯性：与其他事件形成完整的执行链条
    """
    event_type: str = field(default="STEP_SUMMARY", init=False)

    # 步骤基本信息
    step_id: Union[str, int] = ""
    step_number: int = 0
    execution_time: float = 0.0
    nodes_executed: int = 0

    # 核心数据：所有节点的转换器输出（这是指标的直接来源）
    step_context: Dict[str, Any] = field(default_factory=dict)

    # 聚合统计信息
    aggregate_metrics: Dict[str, Any] = field(default_factory=dict)

    # 执行概览
    execution_summary: Dict[str, Any] = field(default_factory=dict)

    def add_aggregate_metric(self, metric_name: str, value: Any,
                           calculation_method: str = "direct",
                           source_nodes: Optional[List[str]] = None):
        """
        添加聚合指标

        Args:
            metric_name: 指标名称，如 'average_trust_score'
            value: 指标值
            calculation_method: 计算方法描述
            source_nodes: 用于计算此指标的源节点列表
        """
        self.aggregate_metrics[metric_name] = {
            "value": value,
            "calculation_method": calculation_method,
            "source_nodes": source_nodes or [],
            "timestamp": datetime.now().isoformat()
        }

    def get_node_outputs(self) -> Dict[str, Any]:
        """获取所有节点的输出结果"""
        return self.step_context

    def extract_metrics_by_pattern(self, pattern: str) -> Dict[str, Any]:
        """
        根据模式提取指标

        Args:
            pattern: 要匹配的指标名称模式

        Returns:
            匹配的指标字典
        """
        import re
        matched_metrics = {}

        # 从step_context中提取匹配的指标
        for node_id, node_output in self.step_context.items():
            if isinstance(node_output, dict):
                for key, value in node_output.items():
                    if re.search(pattern, key):
                        matched_metrics[f"{node_id}.{key}"] = value

        return matched_metrics

    def to_dict(self) -> Dict[str, Any]:
        """扩展基类序列化，包含步骤摘要信息"""
        base_dict = super().to_dict()
        base_dict.update({
            "step_id": self.step_id,
            "step_number": self.step_number,
            "execution_time": self.execution_time,
            "nodes_executed": self.nodes_executed,
            "step_context": self.step_context,
            "aggregate_metrics": self.aggregate_metrics,
            "execution_summary": self.execution_summary
        })
        return base_dict


# 事件工厂函数，方便创建常用事件

def create_state_change_event(
    target_type: str,
    target_id: str, 
    path: List[str],
    operation: str,
    value: Any,
    context_stack: List[Dict[str, Any]],
    old_value: Any = None
) -> StateChangeEvent:
    """创建状态变更事件的便捷函数"""
    return StateChangeEvent(
        target_type=target_type,
        target_id=target_id,
        path=path,
        operation=operation,
        value=value,
        old_value=old_value,
        context_stack=context_stack
    )


def create_memory_change_event(
    agent_id: str,
    operation: str,
    context_stack: List[Dict[str, Any]],
    memory_id: Optional[str] = None,
    memory_type: str = "",
    content: Any = None,
    embedding: Optional[List[float]] = None
) -> MemoryChangeEvent:
    """创建记忆变更事件的便捷函数"""
    return MemoryChangeEvent(
        agent_id=agent_id,
        operation=operation,
        memory_id=memory_id,
        memory_type=memory_type,
        content=content,
        embedding=embedding,
        context_stack=context_stack
    )


def create_node_execution_event(
    step_id: str,
    node_id: str,
    status: str,
    context_stack: List[Dict[str, Any]],
    execution_time: float = 0.0,
    error_message: Optional[str] = None,
    result_summary: Optional[Dict[str, Any]] = None
) -> NodeExecutionEvent:
    """创建节点执行事件的便捷函数"""
    return NodeExecutionEvent(
        step_id=step_id,
        node_id=node_id,
        status=status,
        execution_time=execution_time,
        error_message=error_message,
        result_summary=result_summary or {},
        context_stack=context_stack
    )


def create_instruction_event(
    agent_id: str,
    instruction: str,
    context_stack: List[Dict[str, Any]],
    is_memory: bool = True,
    fovs_used: Optional[List[str]] = None,
    output_schema: Optional[Dict[str, Any]] = None,
    status: str = "success",
    performative_output: str = "",
    structured_output: Optional[Dict[str, Any]] = None,
    total_turns: int = 0,
    finish_instruction_called: bool = False
) -> InstructionEvent:
    """创建指令执行事件的便捷函数"""
    return InstructionEvent(
        agent_id=agent_id,
        instruction=instruction,
        is_memory=is_memory,
        fovs_used=fovs_used or [],
        output_schema=output_schema,
        status=status,
        performative_output=performative_output,
        structured_output=structured_output,
        total_turns=total_turns,
        finish_instruction_called=finish_instruction_called,
        context_stack=context_stack
    )


def create_step_summary_event(
    step_id: Union[str, int],
    step_number: int,
    step_context: Dict[str, Any],
    context_stack: List[Dict[str, Any]],
    execution_time: float = 0.0,
    nodes_executed: int = 0,
    aggregate_metrics: Optional[Dict[str, Any]] = None,
    execution_summary: Optional[Dict[str, Any]] = None
) -> StepSummaryEvent:
    """创建步骤摘要事件的便捷函数"""
    return StepSummaryEvent(
        step_id=step_id,
        step_number=step_number,
        execution_time=execution_time,
        nodes_executed=nodes_executed,
        step_context=step_context,
        aggregate_metrics=aggregate_metrics or {},
        execution_summary=execution_summary or {},
        context_stack=context_stack
    )