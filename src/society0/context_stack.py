"""
Context Stack System: 不可变上下文栈系统

这个模块实现了不可变的上下文栈，用于在整个执行过程中追踪和传递
调用路径信息。栈的设计是不可变的，确保并发安全性。
"""

from typing import List, Dict, Any, Optional, Tuple
from dataclasses import dataclass, field
import copy


@dataclass(frozen=True)
class ContextFrame:
    """
    上下文帧，表示执行栈中的一层
    
    每个帧包含当前执行层级的信息，例如：
    - step: 当前步骤信息
    - node: 当前节点信息  
    - operator: 当前操作器信息
    - action/behavior: 当前动作/行为信息
    """
    frame_type: str  # "step", "node", "operator", "action", "behavior"
    frame_id: str    # 具体的标识符
    params: Dict[str, Any] = field(default_factory=dict)  # 相关参数
    metadata: Dict[str, Any] = field(default_factory=dict)  # 元数据
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典格式，用于事件序列化"""
        return {
            "type": self.frame_type,
            "id": self.frame_id,
            "params": dict(self.params),
            "metadata": dict(self.metadata)
        }


class ContextStack:
    """
    不可变上下文栈
    
    设计原则：
    1. 不可变性：每次 push 操作都返回新的实例
    2. 并发安全：由于不可变，天然支持多线程安全
    3. 轻量级：内部使用 tuple 存储，高效且不可变
    4. 链式调用：支持方便的链式操作
    """
    
    def __init__(self, frames: Tuple[ContextFrame, ...] = ()):
        """
        初始化上下文栈
        
        Args:
            frames: 上下文帧的元组，从底部到顶部排列
        """
        self._frames = frames
    
    def push(self, frame: ContextFrame) -> 'ContextStack':
        """
        压入新的上下文帧，返回新的栈实例
        
        Args:
            frame: 要压入的上下文帧
            
        Returns:
            包含新帧的新栈实例
        """
        return ContextStack(self._frames + (frame,))
    
    def push_step(self, step_id: str, **kwargs) -> 'ContextStack':
        """便捷方法：压入步骤帧"""
        frame = ContextFrame(
            frame_type="step",
            frame_id=step_id,
            metadata=kwargs
        )
        return self.push(frame)
    
    def push_node(self, node_id: str, **kwargs) -> 'ContextStack':
        """便捷方法：压入节点帧"""
        frame = ContextFrame(
            frame_type="node", 
            frame_id=node_id,
            metadata=kwargs
        )
        return self.push(frame)
    
    def push_operator(self, operator_name: str, params: Optional[Dict[str, Any]] = None, **kwargs) -> 'ContextStack':
        """便捷方法：压入操作器帧"""
        frame = ContextFrame(
            frame_type="operator",
            frame_id=operator_name,
            params=params or {},
            metadata=kwargs
        )
        return self.push(frame)
    
    def push_action(self, action_name: str, params: Optional[Dict[str, Any]] = None, **kwargs) -> 'ContextStack':
        """便捷方法：压入动作帧"""
        frame = ContextFrame(
            frame_type="action",
            frame_id=action_name,
            params=params or {},
            metadata=kwargs
        )
        return self.push(frame)
    
    def push_behavior(self, behavior_name: str, params: Optional[Dict[str, Any]] = None, **kwargs) -> 'ContextStack':
        """便捷方法：压入行为帧"""
        frame = ContextFrame(
            frame_type="behavior",
            frame_id=behavior_name,
            params=params or {},
            metadata=kwargs
        )
        return self.push(frame)
    
    def pop(self) -> Tuple['ContextStack', Optional[ContextFrame]]:
        """
        弹出顶部帧，返回新栈和被弹出的帧
        
        Returns:
            (新栈实例, 被弹出的帧)，如果栈为空则帧为 None
        """
        if not self._frames:
            return self, None
        
        return ContextStack(self._frames[:-1]), self._frames[-1]
    
    def peek(self) -> Optional[ContextFrame]:
        """
        查看顶部帧，不修改栈
        
        Returns:
            顶部帧，如果栈为空则返回 None
        """
        return self._frames[-1] if self._frames else None
    
    def is_empty(self) -> bool:
        """检查栈是否为空"""
        return len(self._frames) == 0
    
    def size(self) -> int:
        """获取栈的大小"""
        return len(self._frames)
    
    def get_frames(self) -> Tuple[ContextFrame, ...]:
        """获取所有帧的只读副本"""
        return self._frames
    
    def find_frame_by_type(self, frame_type: str) -> Optional[ContextFrame]:
        """
        按类型查找帧（从顶部开始查找第一个匹配的）
        
        Args:
            frame_type: 要查找的帧类型
            
        Returns:
            找到的帧，如果没有则返回 None
        """
        for frame in reversed(self._frames):
            if frame.frame_type == frame_type:
                return frame
        return None
    
    def get_current_step(self) -> Optional[str]:
        """获取当前步骤 ID"""
        step_frame = self.find_frame_by_type("step")
        return step_frame.frame_id if step_frame else None
    
    def get_current_node(self) -> Optional[str]:
        """获取当前节点 ID"""
        node_frame = self.find_frame_by_type("node")
        return node_frame.frame_id if node_frame else None
    
    def get_current_operator(self) -> Optional[str]:
        """获取当前操作器名称"""
        operator_frame = self.find_frame_by_type("operator")
        return operator_frame.frame_id if operator_frame else None
    
    def to_list(self) -> List[Dict[str, Any]]:
        """
        转换为列表格式，用于事件序列化

        Returns:
            包含所有帧字典表示的列表
        """
        return [frame.to_dict() for frame in self._frames]

    def to_dict(self) -> Dict[str, Any]:
        """
        转换为字典格式，用于V2上下文合并

        Returns:
            包含所有帧信息的合并字典，每个帧类型作为键
        """
        result = {}

        # 将每个帧的信息添加到结果字典中
        for frame in self._frames:
            # 使用帧类型和ID作为键，支持同类型多帧
            frame_key = f"{frame.frame_type}_{frame.frame_id}"
            result[frame_key] = frame.to_dict()

            # 同时添加只用类型作为键的最新帧（便于查询）
            result[frame.frame_type] = frame.to_dict()

        # 添加一些便捷的查询字段
        result["current_step"] = self.get_current_step()
        result["current_node"] = self.get_current_node()
        result["current_operator"] = self.get_current_operator()
        result["frames"] = self.to_list()

        return result
    
    def __len__(self) -> int:
        """支持 len() 函数"""
        return len(self._frames)
    
    def __bool__(self) -> bool:
        """支持布尔判断（空栈为 False）"""
        return len(self._frames) > 0
    
    def __iter__(self):
        """支持迭代（从底部到顶部）"""
        return iter(self._frames)
    
    def __getitem__(self, index: int) -> ContextFrame:
        """支持索引访问"""
        return self._frames[index]
    
    def __repr__(self) -> str:
        """字符串表示"""
        frames_repr = " -> ".join([
            f"{frame.frame_type}:{frame.frame_id}" for frame in self._frames
        ])
        return f"ContextStack([{frames_repr}])"
    
    def __str__(self) -> str:
        """简洁的字符串表示"""
        if not self._frames:
            return "ContextStack(empty)"
        
        frames_str = " -> ".join([
            f"{frame.frame_type}:{frame.frame_id}" for frame in self._frames
        ])
        return f"ContextStack({frames_str})"


class ContextManager:
    """
    上下文管理器，提供便捷的 with 语句支持
    
    使用方式：
    ```python
    context_stack = ContextStack()
    
    with ContextManager(context_stack, "step", "step_1") as new_stack:
        # 在这个作用域内，new_stack 包含了 step_1 帧
        with ContextManager(new_stack, "node", "node_a") as node_stack:
            # 现在栈中有 step_1 -> node_a
            pass
    ```
    """
    
    def __init__(self, stack: ContextStack, frame_type: str, frame_id: str, 
                 params: Optional[Dict[str, Any]] = None, **kwargs):
        """
        初始化上下文管理器
        
        Args:
            stack: 当前的上下文栈
            frame_type: 要压入的帧类型
            frame_id: 帧标识符
            params: 帧参数
            **kwargs: 额外的元数据
        """
        self.original_stack = stack
        self.frame = ContextFrame(
            frame_type=frame_type,
            frame_id=frame_id,
            params=params or {},
            metadata=kwargs
        )
        self.new_stack = None
    
    def __enter__(self) -> ContextStack:
        """进入 with 块，返回包含新帧的栈"""
        self.new_stack = self.original_stack.push(self.frame)
        return self.new_stack
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """退出 with 块，清理资源"""
        # 不可变栈不需要清理，原始栈保持不变
        pass


# 便捷的工厂函数
def create_empty_context() -> ContextStack:
    """创建空的上下文栈"""
    return ContextStack()


def create_step_context(step_id: str, **kwargs) -> ContextStack:
    """创建包含单个步骤帧的上下文栈"""
    return ContextStack().push_step(step_id, **kwargs)


def step_context(stack: ContextStack, step_id: str, **kwargs) -> ContextManager:
    """便捷的步骤上下文管理器"""
    return ContextManager(stack, "step", step_id, **kwargs)


def node_context(stack: ContextStack, node_id: str, **kwargs) -> ContextManager:
    """便捷的节点上下文管理器"""
    return ContextManager(stack, "node", node_id, **kwargs)


def operator_context(stack: ContextStack, operator_name: str, 
                     params: Optional[Dict[str, Any]] = None, **kwargs) -> ContextManager:
    """便捷的操作器上下文管理器"""
    return ContextManager(stack, "operator", operator_name, params, **kwargs)


def action_context(stack: ContextStack, action_name: str, 
                   params: Optional[Dict[str, Any]] = None, **kwargs) -> ContextManager:
    """便捷的动作上下文管理器"""
    return ContextManager(stack, "action", action_name, params, **kwargs)


def behavior_context(stack: ContextStack, behavior_name: str, 
                     params: Optional[Dict[str, Any]] = None, **kwargs) -> ContextManager:
    """便捷的行为上下文管理器"""
    return ContextManager(stack, "behavior", behavior_name, params, **kwargs)