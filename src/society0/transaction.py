"""
Transaction System: Node级事务管理系统

这个模块实现了 Node 级别的事务机制，确保状态变更的原子性和一致性。
事务的边界是单个 Node 的执行过程，失败时不回滚内存状态，但会记录失败事件。
"""

from typing import List, Any, Callable, Optional, Dict, Union, TextIO, Iterable, Sequence
import logging
import json
import threading
from datetime import datetime
from contextlib import contextmanager
import uuid
import traceback

from .events import BaseEvent, StateChangeEvent, MemoryChangeEvent, NodeExecutionEvent
from .event_pipeline import EventBatch, EventBatchListener
from .context_stack import ContextStack
from .logging import ExperimentLogContext, LogField, SystemEvent

logger = logging.getLogger(__name__)


class EventLogger:
    """
    事件日志记录器
    
    负责将事件持久化到 events.jsonl 文件中。
    支持线程安全的批量写入。
    """
    
    def __init__(
        self,
        log_file_path: str,
        listeners: Optional[Iterable[EventBatchListener]] = None
    ):
        """
        初始化事件日志记录器
        
        Args:
            log_file_path: 事件日志文件路径（.jsonl 格式）
            listeners: 事件批次监听器集合
        """
        self.log_file_path = log_file_path
        self._lock = threading.Lock()
        self._file_handle: Optional[TextIO] = None
        self._listeners: List[EventBatchListener] = list(listeners or [])
        
    def open(self):
        """打开日志文件"""
        if self._file_handle is None:
            self._file_handle = open(self.log_file_path, 'a', encoding='utf-8')
    
    def close(self):
        """关闭日志文件"""
        if self._file_handle:
            self._file_handle.close()
            self._file_handle = None
    
    def add_listener(self, listener: EventBatchListener) -> None:
        """注册新的事件监听器"""
        self._listeners.append(listener)

    def write_event(
        self,
        event: BaseEvent,
        *,
        step_id: Optional[str] = None,
        node_id: Optional[str] = None
    ):
        """
        写入单个事件
        
        Args:
            event: 要写入的事件
        """
        self.write_events([event], step_id=step_id, node_id=node_id)
    
    def write_events(
        self,
        events: Sequence[BaseEvent],
        *,
        step_id: Optional[str] = None,
        node_id: Optional[str] = None
    ):
        """
        批量写入事件（线程安全）

        Args:
            events: 要写入的事件列表
        """
        if not events:
            return

        events = list(events)

        step_id, node_id = self._derive_context(events, step_id, node_id)

        batch: Optional[EventBatch] = None

        with self._lock:
            try:
                # 确保文件已打开
                if self._file_handle is None:
                    self.open()

                batch_offsets: List[int] = []

                # 写入每个事件为一行 JSON
                for event in events:
                    batch_offsets.append(self._file_handle.tell())
                    event_dict = event.to_dict()
                    json_line = json.dumps(event_dict, ensure_ascii=False, default=str)
                    self._file_handle.write(json_line + '\n')

                # 强制刷新到磁盘
                self._file_handle.flush()

                batch = EventBatch(
                    log_file_path=self.log_file_path,
                    events=tuple(events),
                    step_id=step_id,
                    node_id=node_id,
                    start_offset=batch_offsets[0],
                    end_offset=self._file_handle.tell(),
                    event_offsets=tuple(batch_offsets),
                )

                logger.debug(f"Successfully wrote {len(events)} events to {self.log_file_path}")

            except Exception as e:
                logger.error(f"Failed to write events to log: {e}")
                raise

        if batch is not None:
            self._notify_listeners(batch)

    def log(self, event_type: str, source: str, data: Dict[str, Any]) -> None:
        """
        Compatibility method for simple event logging (matches event_logger.py interface)

        Args:
            event_type: Type of event
            source: Source of the event
            data: Event-specific data
        """
        from .events import BaseEvent
        from datetime import datetime

        # Create a simple event that implements the BaseEvent interface
        class SimpleEvent(BaseEvent):
            def __init__(self, event_type: str, source: str, data: Dict[str, Any]):
                super().__init__(event_type=event_type, context_stack=[])
                self.source = source
                self.event_data = data
                self.timestamp = datetime.now()

            def to_dict(self) -> Dict[str, Any]:
                result = super().to_dict()
                result.update({
                    'source': self.source,
                    'event_data': self.event_data,
                    'timestamp': self.timestamp.isoformat()
                })
                return result

        event = SimpleEvent(event_type, source, data)
        self.write_event(event)

    def set_context(self, step: int, node_id: Optional[str] = None) -> None:
        """
        Compatibility method for setting execution context (matches event_logger.py interface)

        Args:
            step: Current simulation step
            node_id: Current node being executed (if applicable)
        """
        # The transaction-based EventLogger doesn't need to track context globally
        # since each event includes its own context stack. This is a no-op for compatibility.
        pass
    
    def _notify_listeners(self, batch: EventBatch) -> None:
        """向已注册的监听器广播写入结果"""
        for listener in self._listeners:
            try:
                listener.handle(batch)
            except Exception as exc:
                logger.warning("Event listener %s raised error: %s", listener.__class__.__name__, exc)

    @staticmethod
    def _derive_context(
        events: Sequence[BaseEvent],
        step_id: Optional[str],
        node_id: Optional[str]
    ) -> tuple[Optional[str], Optional[str]]:
        resolved_step = step_id
        resolved_node = node_id

        if resolved_step is None or resolved_node is None:
            for event in events:
                if resolved_step is None:
                    resolved_step = event.get_current_step()
                if resolved_node is None:
                    resolved_node = event.get_current_node()
                if resolved_step is not None and resolved_node is not None:
                    break

        return resolved_step, resolved_node
    
    def __enter__(self):
        """支持 with 语句"""
        self.open()
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """支持 with 语句"""
        self.close()


class NodeTransaction:
    """
    Node 级事务
    
    管理单个 Node 执行过程中的所有状态变更事件，
    提供原子性的事件提交机制。
    """
    
    def __init__(
        self,
        step_id: str,
        node_id: str,
        context_stack: ContextStack,
        *,
        log_context: Optional[ExperimentLogContext] = None,
    ):
        """
        初始化 Node 事务
        
        Args:
            step_id: 当前步骤ID
            node_id: 当前节点ID  
            context_stack: 当前上下文栈
        """
        self.transaction_id = f"{step_id}:{node_id}:{uuid.uuid4().hex[:8]}"
        self.step_id = step_id
        self.node_id = node_id
        self.context_stack = context_stack
        self.start_time = datetime.now()
        self.log_context = log_context
        
        # 事务状态
        self.is_active = True
        self.is_committed = False
        
        # 待提交的事件列表
        self.pending_events: List[BaseEvent] = []
        
        # 事务结果
        self.status = "in_progress"  # "in_progress", "completed", "failed"
        self.error_message: Optional[str] = None
        self.result_summary: Dict[str, Any] = {}
    
    def record_event(self, event: BaseEvent):
        """
        记录事件到事务中
        
        Args:
            event: 要记录的事件
            
        Raises:
            RuntimeError: 如果事务已经不活跃
        """
        if not self.is_active:
            raise RuntimeError(f"Transaction for node {self.node_id} is not active")
        
        # 确保事件包含正确的上下文栈
        if not event.context_stack:
            event.context_stack = self.context_stack.to_list()
        
        self.pending_events.append(event)
        logger.debug(f"Recorded {event.event_type} event in transaction {self.node_id}")
    
    def record_state_change(self, target_type: str, target_id: str, path: List[str], 
                           operation: str, value: Any, old_value: Any = None):
        """
        便捷方法：记录状态变更事件
        """
        event = StateChangeEvent(
            target_type=target_type,
            target_id=target_id,
            path=path,
            operation=operation,
            value=value,
            old_value=old_value,
            context_stack=self.context_stack.to_list()
        )
        self.record_event(event)
    
    def record_memory_change(self, agent_id: str, operation: str, memory_id: Optional[str] = None,
                           memory_type: str = "", content: Any = None):
        """
        便捷方法：记录记忆变更事件
        """
        event = MemoryChangeEvent(
            agent_id=agent_id,
            operation=operation,
            memory_id=memory_id,
            memory_type=memory_type,
            content=content,
            context_stack=self.context_stack.to_list()
        )
        self.record_event(event)

    def _log_system(self, level: str, event: str, **payload: Any) -> None:
        if not self.log_context:
            return
        self.log_context.log_system(level, event, **payload)
    
    def mark_completed(self, result_summary: Optional[Dict[str, Any]] = None):
        """
        标记事务为完成状态
        
        Args:
            result_summary: 执行结果摘要
        """
        if not self.is_active:
            return
            
        self.status = "completed"
        self.result_summary = result_summary or {}
        
        # 记录节点执行完成事件
        execution_time = (datetime.now() - self.start_time).total_seconds()
        completion_event = NodeExecutionEvent(
            step_id=self.step_id,
            node_id=self.node_id,
            status="completed",
            execution_time=execution_time,
            result_summary=self.result_summary,
            context_stack=self.context_stack.to_list()
        )
        self.record_event(completion_event)
    
    def mark_failed(self, error: Exception, partial_result: Optional[Dict[str, Any]] = None):
        """
        标记事务为失败状态
        
        Args:
            error: 导致失败的异常
            partial_result: 部分执行结果（如果有）
        """
        if not self.is_active:
            return
            
        self.status = "failed"
        self.error_message = str(error)
        self.result_summary = partial_result or {}
        
        # 记录节点执行失败事件
        execution_time = (datetime.now() - self.start_time).total_seconds()
        failure_event = NodeExecutionEvent(
            step_id=self.step_id,
            node_id=self.node_id,
            status="failed",
            execution_time=execution_time,
            error_message=self.error_message,
            result_summary=self.result_summary,
            context_stack=self.context_stack.to_list()
        )
        self.record_event(failure_event)
    
    def commit(self, event_logger: EventLogger):
        """
        提交事务，将所有待提交事件原子性地写入日志
        
        Args:
            event_logger: 事件日志记录器
            
        Raises:
            RuntimeError: 如果事务已经提交或不活跃
        """
        if self.is_committed:
            raise RuntimeError(f"Transaction for node {self.node_id} already committed")
        
        if not self.is_active:
            raise RuntimeError(f"Transaction for node {self.node_id} is not active")
        
        try:
            # 原子性写入所有事件
            if self.pending_events:
                event_logger.write_events(
                    self.pending_events,
                    step_id=self.step_id,
                    node_id=self.node_id
                )
            
            # 标记为已提交
            self.is_committed = True
            self.is_active = False
            
            logger.info(f"Transaction committed for node {self.node_id}: "
                       f"{len(self.pending_events)} events, status={self.status}")
            execution_time = (datetime.now() - self.start_time).total_seconds()
            payload = {
                LogField.TRANSACTION_ID.value: self.transaction_id,
                LogField.NODE_ID.value: self.node_id,
                LogField.STEP.value: self.step_id,
                LogField.DURATION_SEC.value: execution_time,
                LogField.SUCCESS_COUNT.value: len(self.pending_events) if self.status != "failed" else 0,
                LogField.ERROR_COUNT.value: 1 if self.status == "failed" else 0,
            }
            level = "INFO" if self.status != "failed" else "WARNING"
            self._log_system(level, SystemEvent.TRANSACTION_COMMITTED.value, **payload)
            
        except Exception as e:
            logger.error(f"Failed to commit transaction for node {self.node_id}: {e}")
            self._log_system(
                "ERROR",
                SystemEvent.TRANSACTION_COMMITTED.value,
                **{
                    LogField.TRANSACTION_ID.value: self.transaction_id,
                    LogField.NODE_ID.value: self.node_id,
                    LogField.STEP.value: self.step_id,
                    LogField.ERROR.value: str(e),
                    LogField.TRACEBACK.value: traceback.format_exc(),
                },
            )
            raise

    def rollback(self):
        """
        回滚事务（实际上只是丢弃待提交事件，不恢复内存状态）
        
        注意：按照设计，我们不回滚内存状态，只是不提交事件日志
        """
        if self.is_committed:
            logger.warning(f"Cannot rollback already committed transaction {self.node_id}")
            return
        
        self.is_active = False
        discarded_count = len(self.pending_events)
        self.pending_events.clear()
        
        logger.info(f"Transaction rolled back for node {self.node_id}: "
                   f"discarded {discarded_count} events")
        payload = {
            LogField.TRANSACTION_ID.value: self.transaction_id,
            LogField.NODE_ID.value: self.node_id,
            LogField.STEP.value: self.step_id,
            LogField.ERROR.value: "rolled_back",
            LogField.ERROR_COUNT.value: discarded_count,
            LogField.DURATION_SEC.value: (datetime.now() - self.start_time).total_seconds(),
        }
        self._log_system("WARNING", SystemEvent.TRANSACTION_ROLLED_BACK.value, **payload)
    
    def get_event_count(self) -> int:
        """获取待提交事件数量"""
        return len(self.pending_events)
    
    def get_execution_time(self) -> float:
        """获取当前执行时间（秒）"""
        return (datetime.now() - self.start_time).total_seconds()
    
    def __repr__(self) -> str:
        return (f"NodeTransaction(step={self.step_id}, node={self.node_id}, "
                f"status={self.status}, events={len(self.pending_events)})")


class TransactionManager:
    """
    事务管理器
    
    管理 NodeTransaction 的生命周期，提供便捷的事务操作接口。
    """
    
    def __init__(self, event_logger: EventLogger, *, log_context: Optional[ExperimentLogContext] = None):
        """
        初始化事务管理器
        
        Args:
            event_logger: 事件日志记录器
        """
        self.event_logger = event_logger
        self._current_transaction: Optional[NodeTransaction] = None
        self._transaction_stack: List[NodeTransaction] = []
        self._log_context: Optional[ExperimentLogContext] = log_context
    
    def get_current_transaction(self) -> Optional[NodeTransaction]:
        """获取当前活跃的事务"""
        return self._current_transaction
    
    def has_active_transaction(self) -> bool:
        """检查是否有活跃的事务"""
        return self._current_transaction is not None and self._current_transaction.is_active

    def set_log_context(self, log_context: Optional[ExperimentLogContext]) -> None:
        """更新日志上下文，传播到活跃事务。"""
        self._log_context = log_context
        if self._current_transaction:
            self._current_transaction.log_context = log_context
        for tx in self._transaction_stack:
            tx.log_context = log_context
    
    @contextmanager
    def transaction(self, step_id: str, node_id: str, context_stack: ContextStack):
        """
        事务上下文管理器
        
        使用方式：
        ```python
        with transaction_manager.transaction("step_1", "node_a", context_stack) as tx:
            # 在这个作用域内进行的所有状态变更都会被记录到事务中
            tx.record_state_change(...)
        # 退出时自动提交事务
        ```
        
        Args:
            step_id: 步骤ID
            node_id: 节点ID
            context_stack: 上下文栈
            
        Yields:
            NodeTransaction: 事务对象
        """
        # 创建新事务
        transaction = NodeTransaction(step_id, node_id, context_stack, log_context=self._log_context)
        
        # 保存当前事务并设置新事务
        previous_transaction = self._current_transaction
        self._current_transaction = transaction
        self._transaction_stack.append(transaction)
        
        # 记录节点开始执行事件
        start_event = NodeExecutionEvent(
            step_id=step_id,
            node_id=node_id,
            status="started",
            context_stack=context_stack.to_list()
        )
        transaction.record_event(start_event)
        
        try:
            logger.debug(f"Starting transaction for node {node_id}")
            yield transaction
            
            # 正常完成
            if transaction.is_active and transaction.status == "in_progress":
                transaction.mark_completed()
            
        except Exception as e:
            # 发生异常，标记失败
            if transaction.is_active:
                transaction.mark_failed(e)
            
            # 重新抛出异常
            raise
            
        finally:
            # 无论成功还是失败，都提交事务
            try:
                if transaction.is_active:
                    transaction.commit(self.event_logger)
                    
            except Exception as commit_error:
                logger.error(f"Failed to commit transaction for node {node_id}: {commit_error}")
                # 不重新抛出提交错误，避免掩盖原始异常
            
            # 恢复之前的事务
            self._transaction_stack.pop()
            self._current_transaction = previous_transaction

    def record_event(self, event: BaseEvent):
        """
        在当前事务中记录事件
        
        Args:
            event: 要记录的事件
            
        Raises:
            RuntimeError: 如果没有活跃的事务
        """
        if not self.has_active_transaction():
            raise RuntimeError("No active transaction to record event")
        
        self._current_transaction.record_event(event)
    
    def record_state_change(self, target_type: str, target_id: str, path: List[str], 
                           operation: str, value: Any, old_value: Any = None):
        """
        在当前事务中记录状态变更
        """
        if not self.has_active_transaction():
            raise RuntimeError("No active transaction to record state change")
        
        self._current_transaction.record_state_change(
            target_type, target_id, path, operation, value, old_value
        )
    
    def record_memory_change(self, agent_id: str, operation: str, memory_id: Optional[str] = None,
                           memory_type: str = "", content: Any = None):
        """
        在当前事务中记录记忆变更
        """
        if not self.has_active_transaction():
            raise RuntimeError("No active transaction to record memory change")
        
        self._current_transaction.record_memory_change(
            agent_id, operation, memory_id, memory_type, content
        )
