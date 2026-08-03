"""
结构化日志的事件与字段规范。

该模块集中定义日志频道、事件名称以及推荐的字段集合，便于在不同
模块之间复用常量并保持语义一致性。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, unique
from typing import Dict, Mapping, Optional, Tuple


@unique
class LogChannel(str, Enum):
    """日志写入频道（与目录结构一一对应）。"""

    RUNTIME = "runtime"
    SCHEDULE = "schedule"
    ENVIRONMENT = "env"
    AGENT = "agents"
    SYSTEM = "system"
    RESOURCE_LLM = "resources/llm"
    RESOURCE_EMBEDDING = "resources/embedding"

    def __str__(self) -> str:
        return self.value


# `agents/<id>.jsonl` 的前缀，具体 agent_id 由协作者传入。
AGENT_CHANNEL_PREFIX = f"{LogChannel.AGENT.value}/"


@unique
class LogField(str, Enum):
    """常用的日志字段 Key，按领域划分组织。"""

    STEP = "step"
    WORLD_STEP = "world_step"
    TOTAL_STEPS = "total_steps"
    STEPS_COMPLETED = "steps_completed"
    TOTAL_DURATION_SEC = "total_duration_sec"
    DURATION_SEC = "duration_sec"
    AGENT_COUNT = "agent_count"
    PHASE = "phase"
    ERROR = "error"
    TRACEBACK = "traceback"
    TOTAL_NODES = "total_nodes"
    NODE_ID = "node_id"
    SELECTOR_TYPE = "selector_type"
    CONVERTER_TYPE = "converter_type"
    CONVERTER_PARAMS = "converter_params"
    CONVERTER_DURATION_SEC = "converter_duration_sec"
    OUTPUT_KEYS = "output_keys"
    DEPENDENCIES = "dependencies"
    CONCURRENCY_LIMIT = "concurrency_limit"
    EXECUTION_MODE = "execution_mode"
    TARGETS_COUNT = "targets_count"
    TARGET_IDS_SAMPLE = "target_ids_sample"
    SELECTOR_PARAMS = "selector_params"
    SELECTOR_PARAM_KEYS = "selector_param_keys"
    SELECTOR_DURATION_SEC = "selector_duration_sec"
    OPERATORS_EXECUTED = "operators_executed"
    OPERATOR_IDS = "operator_ids"
    OPERATOR_ID = "operator_id"
    OPERATOR_TYPES = "operator_types"
    OPERATOR_PARAM_KEYS = "operator_param_keys"
    OPERATOR_DURATION_SEC = "operator_duration_sec"
    RENDERED_PARAM_KEYS = "rendered_param_keys"
    RESULT_COUNT = "result_count"
    SUCCESS_COUNT = "success_count"
    ERROR_COUNT = "error_count"
    AGENT_ID = "agent_id"
    ACTION = "action"
    ACTION_PARAMS = "action_params"
    ACTION_RESULT = "action_result"
    TARGET_ID = "target_id"
    POST_ID = "post_id"
    AUTHOR_ID = "author_id"
    CONTENT = "content"
    CONTENT_PREVIEW = "content_preview"
    CONTENT_LENGTH = "content_length"
    DECISION_PREVIEW = "decision_preview"
    DECISION_LENGTH = "decision_length"
    DECISION_FULL = "decision_full"
    ASSISTANT_TURN_TRACE = "assistant_turn_trace"
    TERMINATION_REASON = "termination_reason"
    TERMINATION_ACTION = "termination_action"
    ACTIONS_PREVIEW = "actions_preview"
    STRUCTURED_OUTPUT_KEYS = "structured_output_keys"
    STAGES_EXECUTED = "stages_executed"
    REASONING_STAGES_COUNT = "reasoning_stages_count"
    TAGS = "tags"
    REPLY_TO = "reply_to"
    TOTAL_LIKES = "total_likes"
    FOLLOWER_ID = "follower_id"
    FOLLOWEE_ID = "followee_id"
    INSTRUCTION = "instruction"
    INSTRUCTION_LENGTH = "instruction_length"
    FOVS = "fovs"
    FOV_NAME = "fov_name"
    FOV_RESULT_PREVIEW = "fov_result_preview"
    FOV_RESULT_LENGTH = "fov_result_length"
    FOV_RESULT = "fov_result"
    ACTION_TAGS = "action_tags"
    OPTIONS_KEYS = "options_keys"
    STATUS = "status"
    INTERACTION_TYPE = "interaction_type"
    ACTIONS_TAKEN = "actions_taken"
    LLM_CALLS = "llm_calls"
    TOTAL_TOKENS = "total_tokens"
    RAW_OUTPUT = "raw_output"
    STRUCTURED_OUTPUT = "structured_output"
    PROMPT_TOKENS = "prompt_tokens"
    COMPLETION_TOKENS = "completion_tokens"
    REQUEST_ID = "request_id"
    ENDPOINT_ID = "endpoint_id"
    MODEL = "model"
    MESSAGES_COUNT = "messages_count"
    TEXTS_COUNT = "texts_count"
    INPUT_CHARACTERS = "input_characters"
    DIMENSIONS = "dimensions"
    VECTORS_RETURNED = "vectors_returned"
    RETRY_COUNT = "retry_count"
    CACHE_HIT = "cache_hit"
    COST_USD = "cost_usd"
    NODE_BATCH = "node_batch"
    AGENT_BATCH = "agent_batch"
    FILE_PATH = "file_path"
    CHECKPOINT_STEP = "checkpoint_step"
    CHECKPOINT_SIZE_BYTES = "checkpoint_size_bytes"
    CHECKPOINT_PATH = "checkpoint_path"
    BACKUP_PATH = "backup_path"
    BACKUP_DURATION_SEC = "backup_duration_sec"
    TRANSACTION_ID = "transaction_id"
    MEMORY_QUERY = "memory_query"
    MEMORY_RESULTS_COUNT = "memory_results_count"
    MEMORY_RESULT_PREVIEW = "memory_result_preview"
    MEMORY_RESULT_FULL = "memory_result_full"
    MEMORY_ID = "memory_id"
    MEMORY_CONTENT_PREVIEW = "memory_content_preview"
    MEMORY_CONTENT_LENGTH = "memory_content_length"
    MEMORY_CONTENT_FULL = "memory_content_full"

    def __str__(self) -> str:
        return self.value


@unique
class RuntimeEvent(str, Enum):
    """运行态日志事件。"""

    EXPERIMENT_STARTED = "experiment_started"
    EXPERIMENT_COMPLETED = "experiment_completed"
    EXPERIMENT_FAILED = "experiment_failed"
    STEP_STARTED = "step_started"
    STEP_COMPLETED = "step_completed"
    STEP_FAILED = "step_failed"

    def __str__(self) -> str:
        return self.value


@unique
class ScheduleEvent(str, Enum):
    """调度域事件。"""

    STEP_FLOW_STARTED = "step_flow_started"
    STEP_FLOW_COMPLETED = "step_flow_completed"
    NODE_STARTED = "node_started"
    NODE_COMPLETED = "node_completed"
    NODE_FAILED = "node_failed"
    NODE_EXECUTION_MODE_SELECTED = "node_execution_mode_selected"
    NODE_SELECTOR_STARTED = "node_selector_started"
    NODE_SELECTOR_COMPLETED = "node_selector_completed"
    NODE_SELECTOR_FAILED = "node_selector_failed"
    OPERATOR_STARTED = "operator_started"
    OPERATOR_COMPLETED = "operator_completed"
    OPERATOR_FAILED = "operator_failed"
    OPERATOR_SKIPPED = "operator_skipped"
    CONVERTER_STARTED = "converter_started"
    CONVERTER_COMPLETED = "converter_completed"
    CONVERTER_FAILED = "converter_failed"
    CONVERTER_SKIPPED = "converter_skipped"

    def __str__(self) -> str:
        return self.value


@unique
class AgentEvent(str, Enum):
    """Agent 日志事件。"""

    FOV_EXECUTED = "fov_executed"
    FOV_FULL_RESULT = "fov_full_result"
    FOV_FAILED = "fov_failed"
    AGENT_INSTRUCTED = "agent_instructed"
    AGENT_TURN_COMPLETED = "agent_turn_completed"
    AGENT_DECISION = "agent_decision"
    ACTION_EXECUTED = "action_executed"
    ACTION_FAILED = "action_failed"
    MEMORY_READ = "memory_read"
    MEMORY_WRITTEN = "memory_written"

    def __str__(self) -> str:
        return self.value


@unique
class EnvironmentEvent(str, Enum):
    """环境域事件。"""

    ACTION_FAILED = "action_failed"
    POST_CREATED = "post_created"
    POST_LIKED = "post_liked"
    POST_REPLIED = "post_replied"
    POST_REPOSTED = "post_reposted"
    AGENT_FOLLOWED = "agent_followed"
    AGENT_UNFOLLOWED = "agent_unfollowed"
    RULE_EXECUTED = "rule_executed"
    TIMED_TASK_TRIGGERED = "timed_task_triggered"

    def __str__(self) -> str:
        return self.value


@unique
class ResourceEvent(str, Enum):
    """资源管理器事件。"""

    LLM_REQUEST_STARTED = "llm_request_started"
    LLM_REQUEST_COMPLETED = "llm_request_completed"
    LLM_REQUEST_FAILED = "llm_request_failed"
    EMBEDDING_REQUEST_STARTED = "embedding_request_started"
    EMBEDDING_REQUEST_COMPLETED = "embedding_request_completed"
    EMBEDDING_REQUEST_FAILED = "embedding_request_failed"

    def __str__(self) -> str:
        return self.value


@unique
class SystemEvent(str, Enum):
    """系统/持久化层事件。"""

    SUMMARY_SAVED = "summary_saved"
    CHECKPOINT_SAVED = "checkpoint_saved"
    CHECKPOINT_LOADED = "checkpoint_loaded"
    TRANSACTION_COMMITTED = "transaction_committed"
    TRANSACTION_ROLLED_BACK = "transaction_rolled_back"

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True)
class EventSpecification:
    """事件规格，包含建议字段和描述。"""

    channel: LogChannel
    event: str
    description: str = ""
    required_fields: Tuple[str, ...] = ()
    optional_fields: Tuple[str, ...] = ()


def _fields(*fields: LogField) -> Tuple[str, ...]:
    return tuple(str(field) for field in fields)


RUNTIME_EVENT_SPECS: Mapping[RuntimeEvent, EventSpecification] = {
    RuntimeEvent.EXPERIMENT_STARTED: EventSpecification(
        channel=LogChannel.RUNTIME,
        event=str(RuntimeEvent.EXPERIMENT_STARTED),
        description="仿真实验启动。",
        required_fields=_fields(LogField.TOTAL_STEPS, LogField.AGENT_COUNT),
    ),
    RuntimeEvent.EXPERIMENT_COMPLETED: EventSpecification(
        channel=LogChannel.RUNTIME,
        event=str(RuntimeEvent.EXPERIMENT_COMPLETED),
        description="仿真实验顺利结束。",
        required_fields=_fields(LogField.TOTAL_STEPS, LogField.TOTAL_DURATION_SEC),
        optional_fields=_fields(LogField.STEPS_COMPLETED),
    ),
    RuntimeEvent.EXPERIMENT_FAILED: EventSpecification(
        channel=LogChannel.RUNTIME,
        event=str(RuntimeEvent.EXPERIMENT_FAILED),
        description="仿真实验失败。",
        required_fields=_fields(LogField.PHASE, LogField.ERROR),
        optional_fields=_fields(LogField.TRACEBACK),
    ),
    RuntimeEvent.STEP_STARTED: EventSpecification(
        channel=LogChannel.RUNTIME,
        event=str(RuntimeEvent.STEP_STARTED),
        description="单步执行开始。",
        required_fields=_fields(LogField.STEP),
        optional_fields=_fields(LogField.WORLD_STEP),
    ),
    RuntimeEvent.STEP_COMPLETED: EventSpecification(
        channel=LogChannel.RUNTIME,
        event=str(RuntimeEvent.STEP_COMPLETED),
        description="单步执行完成。",
        required_fields=_fields(LogField.STEP, LogField.DURATION_SEC),
        optional_fields=_fields(LogField.WORLD_STEP, LogField.TARGETS_COUNT, LogField.OPERATORS_EXECUTED),
    ),
    RuntimeEvent.STEP_FAILED: EventSpecification(
        channel=LogChannel.RUNTIME,
        event=str(RuntimeEvent.STEP_FAILED),
        description="单步执行失败。",
        required_fields=_fields(LogField.STEP, LogField.ERROR),
        optional_fields=_fields(LogField.WORLD_STEP, LogField.TRACEBACK),
    ),
}


SCHEDULE_EVENT_SPECS: Mapping[ScheduleEvent, EventSpecification] = {
    ScheduleEvent.STEP_FLOW_STARTED: EventSpecification(
        channel=LogChannel.SCHEDULE,
        event=str(ScheduleEvent.STEP_FLOW_STARTED),
        description="StepFlow 执行开始。",
        optional_fields=_fields(LogField.TOTAL_NODES),
    ),
    ScheduleEvent.STEP_FLOW_COMPLETED: EventSpecification(
        channel=LogChannel.SCHEDULE,
        event=str(ScheduleEvent.STEP_FLOW_COMPLETED),
        description="StepFlow 执行结束。",
        optional_fields=_fields(LogField.TOTAL_NODES, LogField.DURATION_SEC),
    ),
    ScheduleEvent.NODE_STARTED: EventSpecification(
        channel=LogChannel.SCHEDULE,
        event=str(ScheduleEvent.NODE_STARTED),
        description="节点开始执行。",
        required_fields=_fields(LogField.NODE_ID),
        optional_fields=_fields(
            LogField.SELECTOR_TYPE,
            LogField.OPERATOR_TYPES,
            LogField.CONVERTER_TYPE,
            LogField.DEPENDENCIES,
            LogField.CONCURRENCY_LIMIT,
        ),
    ),
    ScheduleEvent.NODE_COMPLETED: EventSpecification(
        channel=LogChannel.SCHEDULE,
        event=str(ScheduleEvent.NODE_COMPLETED),
        description="节点执行完成。",
        required_fields=_fields(LogField.NODE_ID, LogField.DURATION_SEC),
        optional_fields=_fields(
            LogField.SELECTOR_TYPE,
            LogField.OPERATOR_TYPES,
            LogField.CONVERTER_TYPE,
            LogField.TARGETS_COUNT,
            LogField.OPERATORS_EXECUTED,
            LogField.SUCCESS_COUNT,
            LogField.ERROR_COUNT,
        ),
    ),
    ScheduleEvent.NODE_FAILED: EventSpecification(
        channel=LogChannel.SCHEDULE,
        event=str(ScheduleEvent.NODE_FAILED),
        description="节点执行失败。",
        required_fields=_fields(LogField.NODE_ID, LogField.ERROR),
        optional_fields=_fields(
            LogField.SELECTOR_TYPE,
            LogField.OPERATOR_TYPES,
            LogField.CONVERTER_TYPE,
            LogField.DURATION_SEC,
            LogField.TRACEBACK,
        ),
    ),
    ScheduleEvent.NODE_EXECUTION_MODE_SELECTED: EventSpecification(
        channel=LogChannel.SCHEDULE,
        event=str(ScheduleEvent.NODE_EXECUTION_MODE_SELECTED),
        description="节点执行模式已确定。",
        required_fields=_fields(LogField.NODE_ID, LogField.EXECUTION_MODE),
        optional_fields=_fields(LogField.TARGETS_COUNT),
    ),
    ScheduleEvent.NODE_SELECTOR_STARTED: EventSpecification(
        channel=LogChannel.SCHEDULE,
        event=str(ScheduleEvent.NODE_SELECTOR_STARTED),
        description="节点选择器开始执行。",
        required_fields=_fields(LogField.NODE_ID),
        optional_fields=_fields(
            LogField.SELECTOR_TYPE,
            LogField.SELECTOR_PARAM_KEYS,
        ),
    ),
    ScheduleEvent.NODE_SELECTOR_COMPLETED: EventSpecification(
        channel=LogChannel.SCHEDULE,
        event=str(ScheduleEvent.NODE_SELECTOR_COMPLETED),
        description="节点选择器执行完成。",
        required_fields=_fields(LogField.NODE_ID),
        optional_fields=_fields(
            LogField.SELECTOR_TYPE,
            LogField.TARGETS_COUNT,
            LogField.TARGET_IDS_SAMPLE,
            LogField.SELECTOR_DURATION_SEC,
        ),
    ),
    ScheduleEvent.NODE_SELECTOR_FAILED: EventSpecification(
        channel=LogChannel.SCHEDULE,
        event=str(ScheduleEvent.NODE_SELECTOR_FAILED),
        description="节点选择器执行失败。",
        required_fields=_fields(LogField.NODE_ID, LogField.ERROR),
        optional_fields=_fields(
            LogField.SELECTOR_TYPE,
            LogField.TRACEBACK,
        ),
    ),
    ScheduleEvent.OPERATOR_STARTED: EventSpecification(
        channel=LogChannel.SCHEDULE,
        event=str(ScheduleEvent.OPERATOR_STARTED),
        description="节点内算子开始执行。",
        required_fields=_fields(LogField.NODE_ID, LogField.OPERATOR_ID),
        optional_fields=_fields(
            LogField.OPERATOR_TYPES,
            LogField.OPERATOR_PARAM_KEYS,
            LogField.EXECUTION_MODE,
            LogField.TARGETS_COUNT,
            LogField.CONCURRENCY_LIMIT,
        ),
    ),
    ScheduleEvent.OPERATOR_COMPLETED: EventSpecification(
        channel=LogChannel.SCHEDULE,
        event=str(ScheduleEvent.OPERATOR_COMPLETED),
        description="节点内算子执行完成。",
        required_fields=_fields(LogField.NODE_ID, LogField.OPERATOR_ID, LogField.DURATION_SEC),
        optional_fields=_fields(
            LogField.OPERATOR_TYPES,
            LogField.RESULT_COUNT,
            LogField.SUCCESS_COUNT,
            LogField.ERROR_COUNT,
            LogField.RENDERED_PARAM_KEYS,
        ),
    ),
    ScheduleEvent.OPERATOR_FAILED: EventSpecification(
        channel=LogChannel.SCHEDULE,
        event=str(ScheduleEvent.OPERATOR_FAILED),
        description="节点内算子执行失败。",
        required_fields=_fields(LogField.NODE_ID, LogField.OPERATOR_ID, LogField.ERROR),
        optional_fields=_fields(
            LogField.OPERATOR_TYPES,
            LogField.DURATION_SEC,
            LogField.TRACEBACK,
            LogField.AGENT_BATCH,
        ),
    ),
    ScheduleEvent.OPERATOR_SKIPPED: EventSpecification(
        channel=LogChannel.SCHEDULE,
        event=str(ScheduleEvent.OPERATOR_SKIPPED),
        description="节点内算子被跳过。",
        required_fields=_fields(LogField.NODE_ID, LogField.OPERATOR_ID),
        optional_fields=_fields(
            LogField.EXECUTION_MODE,
            LogField.TARGETS_COUNT,
        ),
    ),
    ScheduleEvent.CONVERTER_STARTED: EventSpecification(
        channel=LogChannel.SCHEDULE,
        event=str(ScheduleEvent.CONVERTER_STARTED),
        description="节点转换器开始执行。",
        required_fields=_fields(LogField.NODE_ID),
        optional_fields=_fields(
            LogField.CONVERTER_TYPE,
            LogField.CONVERTER_PARAMS,
        ),
    ),
    ScheduleEvent.CONVERTER_COMPLETED: EventSpecification(
        channel=LogChannel.SCHEDULE,
        event=str(ScheduleEvent.CONVERTER_COMPLETED),
        description="节点转换器执行完成。",
        required_fields=_fields(LogField.NODE_ID, LogField.DURATION_SEC),
        optional_fields=_fields(
            LogField.CONVERTER_TYPE,
            LogField.OUTPUT_KEYS,
        ),
    ),
    ScheduleEvent.CONVERTER_FAILED: EventSpecification(
        channel=LogChannel.SCHEDULE,
        event=str(ScheduleEvent.CONVERTER_FAILED),
        description="节点转换器执行失败。",
        required_fields=_fields(LogField.NODE_ID, LogField.ERROR),
        optional_fields=_fields(
            LogField.CONVERTER_TYPE,
            LogField.TRACEBACK,
            LogField.DURATION_SEC,
        ),
    ),
    ScheduleEvent.CONVERTER_SKIPPED: EventSpecification(
        channel=LogChannel.SCHEDULE,
        event=str(ScheduleEvent.CONVERTER_SKIPPED),
        description="节点未配置转换器。",
        required_fields=_fields(LogField.NODE_ID),
        optional_fields=_fields(
            LogField.CONVERTER_TYPE,
        ),
    ),
}


AGENT_EVENT_SPECS: Mapping[AgentEvent, EventSpecification] = {
    AgentEvent.FOV_EXECUTED: EventSpecification(
        channel=LogChannel.AGENT,
        event=str(AgentEvent.FOV_EXECUTED),
        description="FoV 调用完成（含摘要）。",
        required_fields=_fields(LogField.FOV_NAME, LogField.FOV_RESULT_PREVIEW),
        optional_fields=_fields(LogField.FOV_RESULT_LENGTH),
    ),
    AgentEvent.FOV_FULL_RESULT: EventSpecification(
        channel=LogChannel.AGENT,
        event=str(AgentEvent.FOV_FULL_RESULT),
        description="FoV 全量内容。",
        required_fields=_fields(LogField.FOV_NAME, LogField.FOV_RESULT),
    ),
    AgentEvent.FOV_FAILED: EventSpecification(
        channel=LogChannel.AGENT,
        event=str(AgentEvent.FOV_FAILED),
        description="FoV 调用失败。",
        required_fields=_fields(LogField.FOV_NAME, LogField.ERROR),
    ),
    AgentEvent.AGENT_INSTRUCTED: EventSpecification(
        channel=LogChannel.AGENT,
        event=str(AgentEvent.AGENT_INSTRUCTED),
        description="Agent 收到指令。",
        required_fields=_fields(LogField.INSTRUCTION_LENGTH),
        optional_fields=_fields(LogField.INSTRUCTION, LogField.FOVS, LogField.ACTION_TAGS, LogField.OPTIONS_KEYS, LogField.MODEL),
    ),
    AgentEvent.AGENT_TURN_COMPLETED: EventSpecification(
        channel=LogChannel.AGENT,
        event=str(AgentEvent.AGENT_TURN_COMPLETED),
        description="Agent 回合结束。",
        optional_fields=_fields(LogField.STATUS, LogField.DURATION_SEC, LogField.ACTIONS_TAKEN, LogField.LLM_CALLS, LogField.TOTAL_TOKENS),
    ),
    AgentEvent.AGENT_DECISION: EventSpecification(
        channel=LogChannel.AGENT,
        event=str(AgentEvent.AGENT_DECISION),
        description="Agent 的公开输出、工具调用顺序和终止原因。",
        required_fields=_fields(LogField.DECISION_PREVIEW),
        optional_fields=_fields(
            LogField.DECISION_LENGTH,
            LogField.ACTIONS_PREVIEW,
            LogField.ASSISTANT_TURN_TRACE,
            LogField.TERMINATION_REASON,
            LogField.TERMINATION_ACTION,
            LogField.STRUCTURED_OUTPUT_KEYS,
            LogField.STAGES_EXECUTED,
            LogField.STATUS,
            LogField.TOTAL_TOKENS,
            LogField.LLM_CALLS,
            LogField.ERROR,
            LogField.DECISION_FULL,
        ),
    ),
    AgentEvent.ACTION_EXECUTED: EventSpecification(
        channel=LogChannel.AGENT,
        event=str(AgentEvent.ACTION_EXECUTED),
        description="动作执行成功。",
        required_fields=_fields(LogField.ACTION),
        optional_fields=_fields(LogField.ACTION_PARAMS, LogField.ACTION_RESULT),
    ),
    AgentEvent.ACTION_FAILED: EventSpecification(
        channel=LogChannel.AGENT,
        event=str(AgentEvent.ACTION_FAILED),
        description="动作执行失败。",
        required_fields=_fields(LogField.ACTION, LogField.ERROR),
        optional_fields=_fields(LogField.ACTION_PARAMS),
    ),
    AgentEvent.MEMORY_READ: EventSpecification(
        channel=LogChannel.AGENT,
        event=str(AgentEvent.MEMORY_READ),
        description="记忆读取。",
        required_fields=_fields(LogField.MEMORY_QUERY),
        optional_fields=_fields(
            LogField.MEMORY_RESULTS_COUNT,
            LogField.MEMORY_RESULT_PREVIEW,
            LogField.MEMORY_RESULT_FULL,
            LogField.ERROR,
        ),
    ),
    AgentEvent.MEMORY_WRITTEN: EventSpecification(
        channel=LogChannel.AGENT,
        event=str(AgentEvent.MEMORY_WRITTEN),
        description="记忆写入。",
        required_fields=_fields(LogField.MEMORY_ID, LogField.MEMORY_CONTENT_PREVIEW),
        optional_fields=_fields(
            LogField.MEMORY_CONTENT_LENGTH,
            LogField.MEMORY_CONTENT_FULL,
            LogField.ERROR,
        ),
    ),
}


ENVIRONMENT_EVENT_SPECS: Mapping[EnvironmentEvent, EventSpecification] = {
    EnvironmentEvent.ACTION_FAILED: EventSpecification(
        channel=LogChannel.ENVIRONMENT,
        event=str(EnvironmentEvent.ACTION_FAILED),
        description="环境动作失败。",
        required_fields=_fields(LogField.ACTION, LogField.ERROR),
        optional_fields=_fields(LogField.AGENT_ID, LogField.TARGET_ID, LogField.POST_ID),
    ),
    EnvironmentEvent.POST_CREATED: EventSpecification(
        channel=LogChannel.ENVIRONMENT,
        event=str(EnvironmentEvent.POST_CREATED),
        description="新帖子创建。",
        required_fields=_fields(LogField.POST_ID, LogField.AUTHOR_ID),
        optional_fields=_fields(
            LogField.CONTENT_PREVIEW,
            LogField.CONTENT_LENGTH,
            LogField.TAGS,
            LogField.REPLY_TO,
        ),
    ),
    EnvironmentEvent.POST_LIKED: EventSpecification(
        channel=LogChannel.ENVIRONMENT,
        event=str(EnvironmentEvent.POST_LIKED),
        description="帖子被点赞。",
        required_fields=_fields(LogField.POST_ID, LogField.AGENT_ID),
        optional_fields=_fields(LogField.TOTAL_LIKES),
    ),
    EnvironmentEvent.POST_REPLIED: EventSpecification(
        channel=LogChannel.ENVIRONMENT,
        event=str(EnvironmentEvent.POST_REPLIED),
        description="帖子收到评论。",
        required_fields=_fields(LogField.POST_ID, LogField.AGENT_ID, LogField.ACTION),
        optional_fields=_fields(LogField.CONTENT_PREVIEW, LogField.REPLY_TO),
    ),
    EnvironmentEvent.POST_REPOSTED: EventSpecification(
        channel=LogChannel.ENVIRONMENT,
        event=str(EnvironmentEvent.POST_REPOSTED),
        description="帖子被转发。",
        required_fields=_fields(LogField.POST_ID, LogField.AGENT_ID),
        optional_fields=_fields(LogField.CONTENT_PREVIEW),
    ),
    EnvironmentEvent.AGENT_FOLLOWED: EventSpecification(
        channel=LogChannel.ENVIRONMENT,
        event=str(EnvironmentEvent.AGENT_FOLLOWED),
        description="关注动作成功。",
        required_fields=_fields(LogField.FOLLOWER_ID, LogField.FOLLOWEE_ID),
    ),
    EnvironmentEvent.AGENT_UNFOLLOWED: EventSpecification(
        channel=LogChannel.ENVIRONMENT,
        event=str(EnvironmentEvent.AGENT_UNFOLLOWED),
        description="取消关注成功。",
        required_fields=_fields(LogField.FOLLOWER_ID, LogField.FOLLOWEE_ID),
    ),
    EnvironmentEvent.RULE_EXECUTED: EventSpecification(
        channel=LogChannel.ENVIRONMENT,
        event=str(EnvironmentEvent.RULE_EXECUTED),
        description="规则执行完成。",
    ),
    EnvironmentEvent.TIMED_TASK_TRIGGERED: EventSpecification(
        channel=LogChannel.ENVIRONMENT,
        event=str(EnvironmentEvent.TIMED_TASK_TRIGGERED),
        description="定时任务触发。",
    ),
}


RESOURCE_EVENT_SPECS: Mapping[ResourceEvent, EventSpecification] = {
    ResourceEvent.LLM_REQUEST_STARTED: EventSpecification(
        channel=LogChannel.RESOURCE_LLM,
        event=str(ResourceEvent.LLM_REQUEST_STARTED),
        description="LLM 请求开始。",
        required_fields=_fields(LogField.REQUEST_ID, LogField.ENDPOINT_ID, LogField.MODEL),
        optional_fields=_fields(LogField.AGENT_ID, LogField.MESSAGES_COUNT),
    ),
    ResourceEvent.LLM_REQUEST_COMPLETED: EventSpecification(
        channel=LogChannel.RESOURCE_LLM,
        event=str(ResourceEvent.LLM_REQUEST_COMPLETED),
        description="LLM 请求完成。",
        required_fields=_fields(LogField.REQUEST_ID, LogField.ENDPOINT_ID, LogField.MODEL, LogField.DURATION_SEC),
        optional_fields=_fields(
            LogField.AGENT_ID,
            LogField.PROMPT_TOKENS,
            LogField.COMPLETION_TOKENS,
            LogField.TOTAL_TOKENS,
            LogField.RETRY_COUNT,
            LogField.COST_USD,
            LogField.CACHE_HIT,
        ),
    ),
    ResourceEvent.LLM_REQUEST_FAILED: EventSpecification(
        channel=LogChannel.RESOURCE_LLM,
        event=str(ResourceEvent.LLM_REQUEST_FAILED),
        description="LLM 请求失败。",
        required_fields=_fields(LogField.REQUEST_ID, LogField.ERROR),
        optional_fields=_fields(LogField.ENDPOINT_ID, LogField.MODEL, LogField.DURATION_SEC, LogField.AGENT_ID),
    ),
    ResourceEvent.EMBEDDING_REQUEST_STARTED: EventSpecification(
        channel=LogChannel.RESOURCE_EMBEDDING,
        event=str(ResourceEvent.EMBEDDING_REQUEST_STARTED),
        description="Embedding 请求开始。",
        required_fields=_fields(LogField.REQUEST_ID, LogField.ENDPOINT_ID, LogField.MODEL),
        optional_fields=_fields(LogField.TEXTS_COUNT, LogField.DIMENSIONS, LogField.INPUT_CHARACTERS),
    ),
    ResourceEvent.EMBEDDING_REQUEST_COMPLETED: EventSpecification(
        channel=LogChannel.RESOURCE_EMBEDDING,
        event=str(ResourceEvent.EMBEDDING_REQUEST_COMPLETED),
        description="Embedding 请求完成。",
        required_fields=_fields(LogField.REQUEST_ID, LogField.ENDPOINT_ID, LogField.MODEL, LogField.DURATION_SEC),
        optional_fields=_fields(LogField.TEXTS_COUNT, LogField.DIMENSIONS, LogField.VECTORS_RETURNED, LogField.RETRY_COUNT, LogField.COST_USD, LogField.CACHE_HIT),
    ),
    ResourceEvent.EMBEDDING_REQUEST_FAILED: EventSpecification(
        channel=LogChannel.RESOURCE_EMBEDDING,
        event=str(ResourceEvent.EMBEDDING_REQUEST_FAILED),
        description="Embedding 请求失败。",
        required_fields=_fields(LogField.REQUEST_ID, LogField.ERROR),
        optional_fields=_fields(LogField.ENDPOINT_ID, LogField.MODEL, LogField.DURATION_SEC),
    ),
}


SYSTEM_EVENT_SPECS: Mapping[SystemEvent, EventSpecification] = {
    SystemEvent.SUMMARY_SAVED: EventSpecification(
        channel=LogChannel.SYSTEM,
        event=str(SystemEvent.SUMMARY_SAVED),
        description="实验总结已保存。",
        required_fields=_fields(LogField.STEPS_COMPLETED, LogField.TOTAL_DURATION_SEC),
        optional_fields=_fields(LogField.CHECKPOINT_STEP, LogField.FILE_PATH),
    ),
    SystemEvent.CHECKPOINT_SAVED: EventSpecification(
        channel=LogChannel.SYSTEM,
        event=str(SystemEvent.CHECKPOINT_SAVED),
        description="检查点写入完成。",
        required_fields=_fields(LogField.CHECKPOINT_STEP, LogField.FILE_PATH),
        optional_fields=_fields(LogField.DURATION_SEC, LogField.CHECKPOINT_SIZE_BYTES, LogField.BACKUP_DURATION_SEC, LogField.BACKUP_PATH),
    ),
    SystemEvent.CHECKPOINT_LOADED: EventSpecification(
        channel=LogChannel.SYSTEM,
        event=str(SystemEvent.CHECKPOINT_LOADED),
        description="检查点读取完成。",
        required_fields=_fields(LogField.CHECKPOINT_STEP, LogField.FILE_PATH),
        optional_fields=_fields(LogField.DURATION_SEC, LogField.CHECKPOINT_SIZE_BYTES),
    ),
    SystemEvent.TRANSACTION_COMMITTED: EventSpecification(
        channel=LogChannel.SYSTEM,
        event=str(SystemEvent.TRANSACTION_COMMITTED),
        description="事务提交成功。",
        required_fields=_fields(LogField.TRANSACTION_ID, LogField.DURATION_SEC),
        optional_fields=_fields(LogField.NODE_ID, LogField.SUCCESS_COUNT, LogField.ERROR_COUNT),
    ),
    SystemEvent.TRANSACTION_ROLLED_BACK: EventSpecification(
        channel=LogChannel.SYSTEM,
        event=str(SystemEvent.TRANSACTION_ROLLED_BACK),
        description="事务回滚。",
        required_fields=_fields(LogField.TRANSACTION_ID, LogField.ERROR),
        optional_fields=_fields(LogField.NODE_ID, LogField.TRACEBACK, LogField.ERROR_COUNT),
    ),
}


def get_event_spec(channel: LogChannel, event: str) -> Optional[EventSpecification]:
    """根据频道与事件名称查找对应规格。"""
    catalog: Dict[str, Mapping[str, EventSpecification]] = {
        LogChannel.RUNTIME.value: {spec.event: spec for spec in RUNTIME_EVENT_SPECS.values()},
        LogChannel.SCHEDULE.value: {spec.event: spec for spec in SCHEDULE_EVENT_SPECS.values()},
        LogChannel.AGENT.value: {spec.event: spec for spec in AGENT_EVENT_SPECS.values()},
        LogChannel.ENVIRONMENT.value: {spec.event: spec for spec in ENVIRONMENT_EVENT_SPECS.values()},
        LogChannel.RESOURCE_LLM.value: {spec.event: spec for spec in RESOURCE_EVENT_SPECS.values()},
        LogChannel.RESOURCE_EMBEDDING.value: {spec.event: spec for spec in RESOURCE_EVENT_SPECS.values()},
        LogChannel.SYSTEM.value: {spec.event: spec for spec in SYSTEM_EVENT_SPECS.values()},
    }
    channel_specs = catalog.get(channel.value)
    if channel_specs is None:
        return None
    return channel_specs.get(event)


__all__ = [
    "LogChannel",
    "AGENT_CHANNEL_PREFIX",
    "LogField",
    "RuntimeEvent",
    "ScheduleEvent",
    "AgentEvent",
    "EnvironmentEvent",
    "ResourceEvent",
    "SystemEvent",
    "EventSpecification",
    "RUNTIME_EVENT_SPECS",
    "SCHEDULE_EVENT_SPECS",
    "AGENT_EVENT_SPECS",
    "ENVIRONMENT_EVENT_SPECS",
    "RESOURCE_EVENT_SPECS",
    "SYSTEM_EVENT_SPECS",
    "get_event_spec",
]
