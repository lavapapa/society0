"""
Multi-stage ActionLoop engine for flexible LLM Agent cognition.

This module implements a configurable, robust multi-stage thinking engine
that can adapt to different cognitive architectures through stage configuration.
Actions replace the previous tool system with enhanced metadata support.
"""

from typing import List, Dict, Any, Callable, Awaitable, Optional, Union
import asyncio
from dataclasses import dataclass, field
import copy
import contextvars
import json
import re
import logging
import json_repair
import time
from collections import Counter
from collections.abc import Mapping

from ..function_registry import validate_strict_function_parameters
from ..resource_managers import redact_credentials
from ..state_proxy import DictProxy

logger = logging.getLogger(__name__)

_CURRENT_ACTION_CALL_ID: contextvars.ContextVar[Optional[str]] = (
    contextvars.ContextVar("society0_action_call_id", default=None)
)


def current_action_call_id() -> Optional[str]:
    """Return the provider tool-call id for the action currently executing."""

    return _CURRENT_ACTION_CALL_ID.get()


def _debug_print(*values: Any, sep: str = " ", end: str = "\n", file: Any = None, flush: bool = False) -> None:
    """Route legacy debug prints through logging without writing to stdout."""
    if file is not None:
        import builtins

        builtins.print(*values, sep=sep, end=end, file=file, flush=flush)
        return

    message = sep.join(str(value) for value in values)
    if end and end != "\n":
        message += end
    logger.debug(message)


print = _debug_print


_EXPLICIT_MISSING_ENTITY = re.compile(
    r"^\s*(?:post|entity|item|object|record|resource|agent|company|contract|"
    r"product|supplier|message|comment|reply)\b"
    r"[^\n]{0,120}?\b(?:not found|does not exist)\b"
    r"(?:\s+(?:in|within|on)\s+(?:the\s+)?(?:state|world|database|registry)"
    r"|\s*[:;,-].*|[.!?。！？]*$)",
    re.IGNORECASE,
)

# Default reasoning stages configuration
DEFAULT_REASONING_STAGES = [
    {"name": "思考", "desc": "思考当前情况，分析信息"},
    {"name": "回答", "desc": "给出回答或执行行动"}
]

# Default act prompt template. Keep this concise: it is sent to every LLM agent
# interaction that exposes ordinary actions.
DEFAULT_AGENT_ACT_PROMPT = """按任务需要简要思考并行动。可参考阶段：
{stages}

- 如果需要使用工具，直接调用工具，不要只描述工具调用。
- 调用标记为 strict 的工具时，必须提交 schema 列出的全部字段；没有值的可空字段使用 JSON null，布尔值使用 true/false，不能写成字符串或省略字段。
- 任务完成后停止；不要重复调用已经完成的工具。"""

SUBMIT_RESULT_ONLY_PROMPT = "直接调用 submit_result 工具提交最终结构化结果；不要输出阶段标记、解释或额外文本。"


def normalize_reasoning_stages(
    stages: List[Union[str, Dict[str, Any]]]
) -> List[Dict[str, Any]]:
    """
    规范化推理阶段配置，支持向后兼容。

    将简单的字符串列表或混合格式转换为统一的字典格式。

    Args:
        stages: 推理阶段列表，可以是字符串或字典

    Returns:
        规范化后的推理阶段列表，每个元素都是包含 name 和 desc 的字典

    Raises:
        ValueError: 如果输入格式无效
    """
    if not stages:
        return DEFAULT_REASONING_STAGES

    normalized = []
    for stage in stages:
        if isinstance(stage, str):
            # 向后兼容：字符串转为字典
            normalized.append({"name": stage.strip(), "desc": ""})
        elif isinstance(stage, dict):
            # 验证必需字段
            if "name" not in stage:
                raise ValueError(f"Stage dict must have 'name' field: {stage}")
            normalized.append({
                "name": stage["name"],
                "desc": stage.get("desc", ""),
                # 保留其他字段，但不处理
            })
        else:
            raise ValueError(f"Invalid stage type: {type(stage)}, expected str or dict")

    return normalized


def format_stages_for_prompt(stages: List[Dict[str, Any]]) -> str:
    """
    将规范化的推理阶段列表格式化为适合注入 prompt 的文本。

    Args:
        stages: 规范化的推理阶段列表

    Returns:
        格式化的阶段描述文本
    """
    lines = []
    for stage in stages:
        name = stage["name"]
        desc = stage.get("desc", "")
        if desc:
            lines.append(f"- {name}: {desc}")
        else:
            lines.append(f"- {name}")
    return "\n".join(lines)


def _normalize_name_set(values: Optional[List[str]]) -> set[str]:
    return {str(value).strip().lower() for value in (values or []) if str(value).strip()}

@dataclass
class ActionCall:
    """Represents an action call with its result."""
    call_id: str
    action_name: str
    arguments: Dict[str, Any]
    result: Any = None
    status: str = "success"
    error: Optional[str] = None
    duration_sec: Optional[float] = None


def _semantic_action_status(result: Any) -> tuple[str, Optional[str]]:
    """Classify an action result into execution status for loop control.

    Environment actions often return user-facing strings instead of raising.
    Treat clear failure strings as action errors so completion tags do not end
    the round after a failed write such as "Post post_x not found".
    """
    if isinstance(result, (dict, DictProxy)):
        if result.get("accepted") is False:
            return "error", str(
                result.get("error")
                or result.get("message")
                or result.get("reason")
                or "action rejected"
            )
        explicit_ok = result.get("ok")
        if explicit_ok is False:
            return "error", str(result.get("error") or result.get("message") or "action failed")
        explicit_status = str(result.get("status") or "").strip().lower()
        if explicit_status in {"error", "failed", "failure", "blocked"}:
            return "error", str(result.get("error") or result.get("message") or explicit_status)
        return "success", None

    text = str(result or "").strip()
    lowered = text.lower()
    # Only treat an explicit error/failure prefix (or a complete not-found
    # clause) as a failed action.  Domain text such as ``配种失败率`` contains
    # the characters ``失败`` but describes a metric, not an execution error.
    explicit_failure_prefix = re.compile(
        r"^(?:error|错误)(?!\s*(?:rate|ratio|percentage|probability))"
        r"(?:\s*[:：-]|\s|$)|"
        r"^(?:invalid|cannot|can't|未找到)"
        r"(?:\s*[:：-]|\s|$)|"
        r"^(?:失败|failure|failed)(?:\s*[:：-]|$)",
        re.IGNORECASE,
    )
    explicit_failure_token = re.compile(
        r"(?:错误|失败)(?=$|[\s,，;；:：.!！?？])"
    )
    explicit_failed_token = re.compile(
        r"\bfailed(?=$|[\s,;:.!?])",
        re.IGNORECASE,
    )
    explicit_not_found_cn = re.search(
        r"(?:不存在|未找到)(?=$|[\s,，;；:：.!！?？])",
        text,
    )
    if (
        lowered.startswith("❌")
        or explicit_failure_prefix.search(text) is not None
        or explicit_failure_token.search(text) is not None
        or explicit_failed_token.search(text) is not None
        or _EXPLICIT_MISSING_ENTITY.search(text) is not None
        or explicit_not_found_cn is not None
    ):
        return "error", text
    return "success", None


def _read_outcome_signature(result: Any, rendered: str) -> str:
    """识别参数不同但决策结论未变的只读结果。"""

    explicit_signature = getattr(result, "read_outcome_signature", None)
    if explicit_signature is not None:
        return str(explicit_signature)

    if isinstance(result, (dict, DictProxy)):
        records = result.get("records")
        returned_count = result.get("returned_count", result.get("count"))
        if records == [] and returned_count == 0:
            return json.dumps(
                {
                    "outcome": "empty_records",
                    "scope": result.get("scope"),
                    "type": result.get("type"),
                },
                ensure_ascii=False,
                sort_keys=True,
            )
    return rendered


def _read_fact_keys(result: Any) -> frozenset[str]:
    """读取 Action 声明的、可跨工具复用的稳定事实键。"""

    raw_fact_keys = getattr(result, "read_fact_keys", None)
    if raw_fact_keys is None or isinstance(raw_fact_keys, str):
        return frozenset()
    try:
        fact_keys = frozenset(raw_fact_keys)
    except TypeError:
        return frozenset()
    if not fact_keys or any(
        not isinstance(fact_key, str) or not fact_key
        for fact_key in fact_keys
    ):
        return frozenset()
    return fact_keys


def _repeated_read_recall(rendered: str, *, limit: int = 1600) -> str:
    """把已读结果的开头结论重新放到重复读取后的近端上下文。

    完整结果仍保留在 Thread 中。这里仅在模型已经原样重试时，重放其开头的
    决策摘要，避免长工具结果离当前回合过远而只剩一条抽象的“请复用”提醒。
    """

    text = str(rendered).strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "\n……（完整结果仍在上文）"


def _is_tool_schema_error(error: BaseException | str) -> bool:
    """识别本地 Action 参数校验错误，避免把它升级为 step 失败。"""

    text = str(error).casefold()
    error_type = (
        type(error).__name__.casefold()
        if isinstance(error, BaseException)
        else ""
    )
    direct_marker = any(
        marker in text
        for marker in (
            "tool schema error",
            "tool_schema_error",
            "invalid action arguments",
            "invalid arguments for action",
        )
    )
    schema_phrase = any(
        marker in text for marker in ("additional properties", "required property")
    ) and any(marker in text for marker in ("tool", "action"))
    return direct_marker or schema_phrase or error_type in {
        "validationerror",
        "jsonschemaexception",
    }


_STRICT_NULL_STRING_SENTINELS = {"null", "none"}


def _schema_types(schema: Any) -> set[str]:
    if not isinstance(schema, dict):
        return set()
    schema_type = schema.get("type")
    if isinstance(schema_type, str):
        return {schema_type}
    if isinstance(schema_type, list):
        return {value for value in schema_type if isinstance(value, str)}
    return set()


def _schema_allows_null(schema: Any) -> bool:
    """Only honor the explicit nullable forms used by strict Action schemas."""

    if not isinstance(schema, dict):
        return False
    enum = schema.get("enum")
    return "null" in _schema_types(schema) or (
        isinstance(enum, list) and None in enum
    )


def _schema_accepts_string(schema: Any, value: str) -> bool:
    if not isinstance(schema, dict) or "string" not in _schema_types(schema):
        return False
    enum = schema.get("enum")
    if isinstance(enum, list) and value not in enum:
        return False
    min_length = schema.get("minLength", 0)
    return not isinstance(min_length, bool) and len(value) >= int(min_length)


def _normalize_strict_action_value(
    value: Any,
    schema: Any,
) -> Any:
    """Normalize provider string nulls using the action's strict schema only."""

    if not isinstance(schema, dict):
        return value

    if isinstance(value, dict):
        properties = schema.get("properties", {})
        if not isinstance(properties, dict):
            return value
        return {
            key: _normalize_strict_action_value(
                item,
                properties.get(key),
            )
            if key in properties
            else item
            for key, item in value.items()
        }
    if isinstance(value, list):
        item_schema = schema.get("items")
        return [
            _normalize_strict_action_value(item, item_schema)
            if isinstance(item, (dict, list))
            else item
            for item in value
        ]
    if isinstance(value, str) and _schema_allows_null(schema):
        stripped = value.strip()
        enum = schema.get("enum")
        if (
            stripped.casefold() in _STRICT_NULL_STRING_SENTINELS
            and not (isinstance(enum, list) and value in enum)
        ):
            return None
        if (
            not stripped
            and not _schema_accepts_string(schema, value)
        ):
            return None
    return value


def _normalize_strict_action_arguments(
    arguments: Dict[str, Any],
    schema: Dict[str, Any],
) -> Dict[str, Any]:
    return _normalize_strict_action_value(arguments, schema)


_PROVIDER_TRANSPORT_MESSAGE_MARKERS = (
    "timeout",
    "timed out",
    "connection reset",
    "connection refused",
    "connection aborted",
    "temporarily unavailable",
    "service unavailable",
    "transport error",
    "network error",
    "http 429",
    "http 502",
    "http 503",
    "http 504",
)


def _provider_transport_failure_class(error: BaseException) -> str | None:
    """Return a fine-grained provider failure class for one physical request."""

    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, TimeoutError):
            return "provider_timeout"
        if isinstance(current, ConnectionError):
            return "provider_transport_error"
        lowered = str(current).casefold()
        if any(marker in lowered for marker in _PROVIDER_TRANSPORT_MESSAGE_MARKERS):
            return (
                "provider_timeout"
                if "timeout" in lowered or "timed out" in lowered
                else "provider_transport_error"
            )
        cause = current.__cause__
        if isinstance(cause, BaseException):
            current = cause
            continue
        context = current.__context__
        current = context if isinstance(context, BaseException) else None
    return None


def _format_provider_error(error: BaseException) -> str:
    """Format exhausted provider diagnostics without exposing request secrets."""

    detail = str(error).strip() or repr(error)
    message = f"{type(error).__name__}: {detail}"
    context: list[str] = []
    request = getattr(error, "request", None)
    if request is not None:
        if isinstance(request, dict):
            method = request.get("method")
            url = request.get("url")
        else:
            method = getattr(request, "method", None)
            url = getattr(request, "url", None)
        if method is not None or url is not None:
            context.append(
                "request="
                + " ".join(str(part) for part in (method, url) if part is not None)
            )
    timeout = getattr(error, "timeout", None)
    if timeout is not None:
        context.append(f"timeout={timeout!r}")
    if context:
        message += f" ({', '.join(context)})"
    return message


@dataclass
class ActionSet:
    """Container for available actions that can be called by the LLM."""
    actions: Dict[str, Dict[str, Any]] = field(default_factory=dict)

    def add_action(
        self,
        name: str,
        func: Callable[..., Any],
        description: str,
        parameters: Dict[str, Any],
        tags: List[str] = None,
        strict: bool = False,
    ):
        """
        Add an action to the actionset with proper OpenAI function calling schema.

        Args:
            name: Action name
            func: Callable function
            description: Action description
            parameters: OpenAI-style parameters schema with type, properties, required
            tags: List of tags for action categorization and filtering
            strict: Request provider-side strict JSON Schema enforcement for this function tool
        """
        # Commented out debug prints
        # print(f"--- [DEBUG] Adding action to ActionSet: '{name}' ---")
        # print(f"  Description: {description}")
        # print(f"  Tags: {tags or []}")

        if strict:
            validate_strict_function_parameters(parameters)
        action_info = {
            "function": func,
            "description": description,
            "parameters": parameters,
            "tags": tags or [],
        }
        if strict:
            action_info["strict"] = True
            from jsonschema import Draft202012Validator

            action_info["argument_validator"] = Draft202012Validator(parameters)
        self.actions[name] = action_info

    async def call_action(
        self,
        action_name: str,
        context_provider: Optional[Callable] = None,
        *,
        _society0_call_id: Optional[str] = None,
        **kwargs,
    ) -> Any:
        """Call an action by name with arguments, handling both sync and async functions and context management.

        Args:
            action_name: Name of the action to call
            context_provider: Function that returns current ContextStack and provides context update capability
            **kwargs: Arguments to pass to the action function
        """
        if action_name not in self.actions:
            raise ValueError(f"Action '{action_name}' not found in actionset")

        action_info = self.actions[action_name]
        argument_validator = action_info.get("argument_validator")
        if argument_validator is not None:
            kwargs = _normalize_strict_action_arguments(
                kwargs,
                action_info["parameters"],
            )
            argument_validator.validate(kwargs)

        call_id_token = _CURRENT_ACTION_CALL_ID.set(_society0_call_id)
        try:
            action_func = action_info["function"]

            # If context_provider is available, use context management
            if context_provider:
                provided = context_provider()
                current_stack, update_context = provided[0], provided[1]
                record_action = provided[2] if len(provided) > 2 else None

                # Import context management utilities
                from ..context_stack import action_context

                # Execute action within proper context
                with action_context(current_stack, action_name, params=kwargs) as new_stack:
                    # Update the world's context stack for state change tracking
                    update_context(new_stack)

                    try:
                        try:
                            # Execute the action function
                            result = action_func(**kwargs)

                            # Handle async functions properly
                            import asyncio
                            if asyncio.iscoroutine(result):
                                result = await result

                            if record_action is not None:
                                status, _ = _semantic_action_status(result)
                                record_action(action_name, kwargs, result, status)
                            return result
                        except Exception as exc:
                            if record_action is not None:
                                record_action(action_name, kwargs, str(exc), "error")
                            raise
                    finally:
                        # Always restore the original context stack after action execution
                        update_context(current_stack)
            else:
                # Fallback to original behavior if no context provider
                result = action_func(**kwargs)

                # Handle async functions properly
                import asyncio
                if asyncio.iscoroutine(result):
                    return await result
                else:
                    return result

        except Exception as e:
            logger.error(f"Error calling action '{action_name}': {e}")
            raise
        finally:
            _CURRENT_ACTION_CALL_ID.reset(call_id_token)

    def filter_by_tags(
        self,
        action_tags: Optional[List[str]] = None,
        exclude_tags: Optional[List[str]] = None
    ) -> 'ActionSet':
        """
        Filter actions by tags using OR logic for inclusion and exclusion.

        Args:
            action_tags: If provided, include actions that have ANY of these tags
            exclude_tags: If provided, exclude actions that have ANY of these tags

        Returns:
            New ActionSet with filtered actions
        """
        filtered_actionset = ActionSet()
        action_name_aliases = set()
        for registered_name in self.actions:
            action_name_aliases.add(registered_name)
            if "." in registered_name:
                action_name_aliases.add(registered_name.split(".")[-1])

        for action_name, action_info in self.actions.items():
            action_action_tags = action_info.get("tags", [])
            implicit_tags = {action_name}
            if "." in action_name:
                implicit_tags.add(action_name.split(".")[-1])
            # 合并显式标签与隐式别名
            merged_tags = set(action_action_tags) | implicit_tags

            # Check exclusion first
            if exclude_tags:
                if any(tag in merged_tags for tag in exclude_tags):
                    continue  # Skip this action

            # Check inclusion. None means "no filtering"; an explicit empty
            # list means "expose no ordinary actions" and is used by interview.
            if action_tags is not None:
                matched = False
                for tag in action_tags:
                    if tag in action_name_aliases:
                        if tag in implicit_tags:
                            matched = True
                            break
                    elif tag in merged_tags:
                        matched = True
                        break
                if not matched:
                    continue  # Skip this action

            # Add action to filtered set
            filtered_actionset.actions[action_name] = action_info.copy()  # Make a copy to avoid reference issues

        return filtered_actionset

    def get_openai_actions_schema(self) -> List[Dict[str, Any]]:
        """Get OpenAI-compatible actions schema."""
        actions_schema = []
        for action_name, action_info in self.actions.items():
            function_schema = {
                "name": action_name,
                "description": action_info["description"],
                "parameters": action_info["parameters"],
            }
            if action_info.get("strict"):
                function_schema["strict"] = True
            actions_schema.append({
                "type": "function",
                "function": function_schema,
            })
        return actions_schema

@dataclass
class LoopResult:
    """Result of execute_action_loop execution."""
    status: str  # "success", "partial_success", "error"
    phases: Dict[str, Union[str, List[Dict[str, Any]]]] = field(default_factory=dict)
    phases_unknown: Dict[str, Union[str, List[Dict[str, Any]]]] = field(default_factory=dict)
    full_history: List[Dict[str, Any]] = field(default_factory=list)
    conversation_messages: List[Dict[str, Any]] = field(default_factory=list)
    parsing_errors: List[str] = field(default_factory=list)
    total_turns: int = 0
    default_stage_name: str = "default"
    action_calls: List[Dict[str, Any]] = field(default_factory=list)
    termination_reason: Optional[str] = None
    termination_action: Optional[str] = None
    error: Optional[str] = None
    failure_class: Optional[str] = None
    retry_scope: Optional[str] = None
    retry_attempts: Optional[int] = None
    # 激活是否已由模型完成。工具调用成功不等于本轮经营判断已经完成：
    # 行动预算耗尽、回合耗尽和输出截断都保留已写入的业务事实，
    # 但交由调用方决定如何延期该次激活。
    activation_status: str = "completed"

    # 新增字段 - 支持OpenAI推理模型
    reasoning_content: Optional[str] = None  # 原始推理内容
    thinking_process: List[Dict[str, Any]] = field(default_factory=list)  # 结构化的思考步骤
    has_reasoning: bool = False  # 是否包含推理内容
    model_type: Optional[str] = None  # 模型类型（reasoning/standard）

    # 记忆提取相关（默认占位，便于向后兼容）
    memory_extraction_enabled: bool = False
    extracted_memories: List[Dict[str, Any]] = field(default_factory=list)
    memory_extraction_success: bool = False
    memory_extraction_error: Optional[str] = None


def build_assistant_turn_trace(full_history: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Build an audit trace from provider-visible assistant output.

    The trace intentionally excludes ``reasoning_content`` and request
    messages. It contains only public assistant ``content`` and the tool calls
    returned alongside that content.
    """

    turns: List[Dict[str, Any]] = []
    for index, history_item in enumerate(full_history or [], start=1):
        if not isinstance(history_item, dict):
            continue
        response = history_item.get("response")
        if not isinstance(response, dict):
            response = {}

        if "content" not in response:
            content_state = "missing"
            assistant_text = ""
        elif response.get("content") is None:
            content_state = "null"
            assistant_text = ""
        elif isinstance(response.get("content"), str):
            assistant_text = response["content"]
            content_state = "present" if assistant_text.strip() else "empty"
        else:
            content_state = "non_text"
            assistant_text = str(response.get("content"))

        action_results = {
            str(item.get("call_id")): item
            for item in (history_item.get("action_results") or [])
            if isinstance(item, dict) and item.get("call_id") is not None
        }
        tool_calls: List[Dict[str, Any]] = []
        for tool_call in response.get("tool_calls") or []:
            if not isinstance(tool_call, dict):
                continue
            function = tool_call.get("function")
            if not isinstance(function, dict):
                function = {}
            call_id = str(tool_call.get("id") or "")
            execution = action_results.get(call_id, {})
            trace_item: Dict[str, Any] = {
                "call_id": call_id,
                "action_name": str(function.get("name") or ""),
                "arguments": function.get("arguments"),
                "status": str(execution.get("status") or "not_executed"),
            }
            if execution.get("error"):
                trace_item["error"] = str(execution["error"])
            tool_calls.append(trace_item)

        turns.append(
            {
                "turn": int(history_item.get("turn") or index),
                "assistant_text": assistant_text,
                "assistant_text_state": content_state,
                "has_visible_text": bool(assistant_text.strip()),
                "tool_calls": tool_calls,
            }
        )
    return turns


def _normalize_stage_name(stage_name: str, defined_stages: List[str]) -> Optional[str]:
    """
    Normalize stage name with fuzzy matching for common variations.
    Returns the matched stage name or None if no match found.
    """
    # Clean the input stage name
    clean_stage = stage_name.strip().replace("-", "_").replace(" ", "_")

    # Create normalized versions of defined stages for comparison
    stage_map = {}
    for stage in defined_stages:
        normalized = stage.lower().replace("-", "_").replace(" ", "_")
        stage_map[normalized] = stage

    # Try exact match first (case insensitive)
    if clean_stage.lower() in stage_map:
        return stage_map[clean_stage.lower()]

    # Try partial matches
    clean_lower = clean_stage.lower()
    for normalized_stage, original_stage in stage_map.items():
        if clean_lower in normalized_stage or normalized_stage in clean_lower:
            return original_stage

    return None

def _extract_reasoning_content(response: Dict[str, Any]) -> tuple:
    """
    从 OpenAI LLM 响应中提取推理内容和最终答案

    Args:
        response: OpenAI API 响应

    Returns:
        (reasoning_content, final_content, metadata)
    """
    reasoning_content = None
    final_content = response.get("content")
    model_type = "standard"
    has_reasoning = False

    # 检查 OpenAI 推理模型格式 (o1系列等)
    if "reasoning_content" in response:
        reasoning_content = response.get("reasoning_content")
        model_type = "reasoning"
        has_reasoning = True

    metadata = {
        "model_type": model_type,
        "format": "openai",
        "has_reasoning": has_reasoning
    }

    return reasoning_content, final_content, metadata


def _conversation_response(response: Dict[str, Any]) -> Dict[str, Any]:
    """移除 provider 结束原因，避免把非 message 字段发回接口。"""

    return {
        key: value
        for key, value in response.items()
        if key != "finish_reason"
    }

def _parse_stages(content: str, defined_stages: List[str], default_stage_name: str = "default") -> tuple:
    """
    Parse LLM output into stages using robust stage marking.

    Returns:
        (phases_dict, phases_unknown_dict, parsing_errors)
    """
    phases = {}
    phases_unknown = {}
    parsing_errors = []

    # Enhanced regex pattern that's more tolerant
    stage_pattern = r'->\\s*stage_begin\\s*:\\s*(\\w+(?:[_-]\\w+)*)'

    # Find all stage markers
    matches = list(re.finditer(stage_pattern, content, re.IGNORECASE))

    if not matches:
        # No stage markers found, everything goes to default stage
        phases[default_stage_name] = content.strip()
        return phases, phases_unknown, parsing_errors

    # Handle content before first stage marker (goes to default stage)
    first_match = matches[0]
    pre_stage_content = content[:first_match.start()].strip()
    if pre_stage_content:
        phases[default_stage_name] = pre_stage_content

    # Process each stage
    for i, match in enumerate(matches):
        raw_stage_name = match.group(1)
        stage_start = match.end()

        # Determine stage content end
        if i + 1 < len(matches):
            stage_end = matches[i + 1].start()
        else:
            stage_end = len(content)

        stage_content = content[stage_start:stage_end].strip()

        # Normalize and match stage name
        normalized_stage = _normalize_stage_name(raw_stage_name, defined_stages)

        if normalized_stage:
            # Known stage - add to phases
            if normalized_stage in phases:
                # Stage appears multiple times, append content
                if isinstance(phases[normalized_stage], str):
                    phases[normalized_stage] = phases[normalized_stage] + "\\n\\n" + stage_content
                else:
                    # This shouldn't happen in normal parsing, but handle gracefully
                    phases[normalized_stage].append({"type": "text", "content": stage_content})
            else:
                phases[normalized_stage] = stage_content
        else:
            # Unknown stage - add to phases_unknown
            parsing_errors.append(f"Unknown stage '{raw_stage_name}' found")
            phases_unknown[raw_stage_name] = stage_content

    return phases, phases_unknown, parsing_errors

async def execute_action_loop(
    instruction: str,
    action_set: ActionSet,
    system_prompt: str,
    stages: List[Union[str, Dict[str, Any]]],
    llm_call: Callable[[List[Dict]], Awaitable[Any]],
    act_prompt: str = DEFAULT_AGENT_ACT_PROMPT,
    max_turns: int = 4,
    default_stage_name: str = "default",
    context_provider: Optional[Callable] = None,
    *,
    terminal_action_names: Optional[List[str]] = None,
    completion_action_tags: Optional[List[str]] = None,
    required_action_names: Optional[List[str]] = None,
    required_action_tags: Optional[List[str]] = None,
    turn_remain_hint: bool = True,
    hint_on_remain_turn: int = 1,
    max_action_calls: Optional[int] = None,
    max_request_messages: Optional[int] = None,
    action_call_limits: Optional[Dict[str, int]] = None,
    llm_request_options: Optional[Dict[str, Any]] = None,
    prior_messages: Optional[List[Dict[str, Any]]] = None,
    thread_message_recorder: Optional[Callable[[Dict[str, Any]], None]] = None,
    thread_event_recorder: Optional[Callable[[str, Dict[str, Any]], None]] = None,
) -> LoopResult:
    """
    Execute a configurable multi-stage action loop.

    Args:
        instruction: The main instruction for the LLM
        action_set: Available actions the LLM can call
        system_prompt: System prompt for the LLM
        stages: List of stage definitions (can be strings or dicts with name/desc)
        llm_call: Callable to make LLM API calls
        act_prompt: Template for stage guidance (will be formatted with stages)
        max_turns: Maximum number of loop iterations
        default_stage_name: Name for content that appears before any stage markers
        context_provider: Function that returns (current_context_stack, update_function) for action context tracking
        terminal_action_names: Action names that should terminate loop once called
        completion_action_tags: Action tags that mark a successful action as
            completing this instruction. This is useful for workflows such as
            "read tools may continue, but a social_write action ends the round".
        required_action_names: Action names that must succeed before the loop
            can be considered complete. If the model stops early and turns
            remain, the loop asks it to correct the missing action.
        required_action_tags: Action tags that must succeed before the loop
            can be considered complete.

    Returns:
        LoopResult containing parsed stages, action call results, and execution history
    """

    # Normalize stages to unified dict format
    normalized_stages = normalize_reasoning_stages(stages)
    if max_request_messages is not None:
        if isinstance(max_request_messages, bool) or not isinstance(
            max_request_messages,
            int,
        ):
            raise ValueError("max_request_messages must be an integer")
        if max_request_messages < 4:
            raise ValueError("max_request_messages must be at least 4")
    raw_llm_request_options = dict(llm_request_options or {})
    try:
        provider_request_retry_max = int(
            raw_llm_request_options.pop("provider_request_retry_max", 1) or 0
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("provider_request_retry_max must be an integer") from exc
    if provider_request_retry_max < 0:
        raise ValueError("provider_request_retry_max must be non-negative")
    try:
        empty_response_retry_max = int(
            raw_llm_request_options.pop("empty_response_retry_max", 0) or 0
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("empty_response_retry_max must be an integer") from exc
    if empty_response_retry_max < 0:
        raise ValueError("empty_response_retry_max must be non-negative")
    raw_temperature_delta = raw_llm_request_options.pop(
        "empty_response_retry_temperature_delta",
        None,
    )
    if raw_temperature_delta is None:
        empty_response_retry_temperature_delta = None
    else:
        try:
            empty_response_retry_temperature_delta = float(raw_temperature_delta)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "empty_response_retry_temperature_delta must be numeric"
            ) from exc
        if empty_response_retry_temperature_delta <= 0:
            raise ValueError(
                "empty_response_retry_temperature_delta must be positive"
            )
    raw_temperature_cap = raw_llm_request_options.pop(
        "empty_response_retry_temperature_max",
        1.0,
    )
    try:
        empty_response_retry_temperature_max = float(raw_temperature_cap)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            "empty_response_retry_temperature_max must be numeric"
        ) from exc
    if empty_response_retry_temperature_max <= 0:
        raise ValueError(
            "empty_response_retry_temperature_max must be positive"
        )
    raw_repeated_read_temperature_delta = raw_llm_request_options.pop(
        "repeated_read_temperature_delta",
        None,
    )
    if raw_repeated_read_temperature_delta is None:
        repeated_read_temperature_delta = None
    else:
        try:
            repeated_read_temperature_delta = float(
                raw_repeated_read_temperature_delta
            )
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "repeated_read_temperature_delta must be numeric"
            ) from exc
        if repeated_read_temperature_delta <= 0:
            raise ValueError(
                "repeated_read_temperature_delta must be positive"
            )
    raw_repeated_read_temperature_cap = raw_llm_request_options.pop(
        "repeated_read_temperature_max",
        1.0,
    )
    try:
        repeated_read_temperature_max = float(
            raw_repeated_read_temperature_cap
        )
    except (TypeError, ValueError) as exc:
        raise ValueError(
            "repeated_read_temperature_max must be numeric"
        ) from exc
    if repeated_read_temperature_max <= 0:
        raise ValueError(
            "repeated_read_temperature_max must be positive"
        )

    safe_llm_request_options = {
        str(key): value
        for key, value in raw_llm_request_options.items()
        if key not in {"messages", "tools", "tool_choice", "metadata", "agent_id", "model"}
        and value is not None
    }

    # Extract stage names for parsing
    stage_names = [stage["name"] for stage in normalized_stages]

    # Get actions schema for OpenAI format
    actions_schema = action_set.get_openai_actions_schema() if action_set.actions else []
    submit_result_only = list(action_set.actions.keys()) == ["submit_result"]

    # Initialize conversation history
    if submit_result_only:
        flow_section = f"[输出流程]\n{SUBMIT_RESULT_ONLY_PROMPT}"
    else:
        stages_text = format_stages_for_prompt(normalized_stages)
        flow_section = f"[输出流程]\n{act_prompt.format(stages=stages_text)}"
    if safe_llm_request_options.get("parallel_tool_calls") is False:
        flow_section += (
            "\n\n[工具调用协议]\n"
            "每个 assistant 响应最多提交一个 tool call；"
            "收到该工具结果后再决定下一步。"
        )
    system_message = f"{system_prompt}\n\n{flow_section}"
    if prior_messages:
        messages = copy.deepcopy(prior_messages)
        if (
            not isinstance(messages[0], dict)
            or messages[0].get("role") != "system"
        ):
            raise ValueError("prior_messages must start with a system message")
        messages[0] = {
            **messages[0],
            "role": "system",
            "content": system_message,
        }
        messages.append({"role": "user", "content": instruction})
    else:
        messages = [
            {
                "role": "system",
                "content": system_message
            },
            {"role": "user", "content": instruction}
        ]

    if thread_message_recorder is not None:
        new_messages = messages[-1:] if prior_messages else messages
        for message in new_messages:
            thread_message_recorder(copy.deepcopy(message))

    def _append_runtime_message(message: Dict[str, Any]) -> None:
        messages.append(message)
        if thread_message_recorder is not None:
            thread_message_recorder(copy.deepcopy(message))

    def _request_messages() -> List[Dict[str, Any]]:
        """保留完整 Thread，只压缩发给模型的本次请求视图。"""

        if max_request_messages is None or len(messages) <= max_request_messages:
            return copy.deepcopy(messages)
        recent_count = max_request_messages - 2
        start = len(messages) - recent_count
        while start > 2 and messages[start].get("role") == "tool":
            start -= 1
        projected = [*messages[:2], *messages[start:]]
        _append_thread_event(
            "request_history_compacted",
            {
                "full_message_count": len(messages),
                "request_message_count": len(projected),
                "omitted_message_count": len(messages) - len(projected),
            },
        )
        return copy.deepcopy(projected)

    def _append_thread_event(event_type: str, payload: Dict[str, Any]) -> None:
        """Persist a structured Thread event without converting failures to actions.

        The recorder is part of the durable transaction boundary.  If it
        cannot append, surface that infrastructure error to the caller; the
        action loop must not turn a successfully committed domain action into
        a synthetic action failure.
        """

        if thread_event_recorder is None:
            return
        try:
            thread_event_recorder(
                event_type,
                redact_credentials(copy.deepcopy(payload)),
            )
        except Exception as exc:
            raise RuntimeError(
                f"failed to persist Agent Thread event {event_type}"
            ) from exc

    def _append_thread_event_best_effort(
        event_type: str,
        payload: Dict[str, Any],
    ) -> None:
        """Best-effort event path used while preserving an active cancellation."""

        try:
            _append_thread_event(event_type, payload)
        except BaseException as exc:
            logger.warning(
                "failed to persist Agent Thread event %s: %s",
                event_type,
                exc,
            )

    full_history = []
    total_turns = 0
    all_action_calls = []  # Track all action calls across turns
    final_content = ""

    # Initialize loop result for reasoning tracking
    loop_result = LoopResult(
        status="",
        phases={},
        phases_unknown={},
        full_history=[],
        parsing_errors=[],
        total_turns=0,
        default_stage_name=default_stage_name,
        reasoning_content=None,
        thinking_process=[],
        has_reasoning=False,
        model_type=None,
        termination_reason=None,
        termination_action=None,
    )

    terminal_action_name_set = _normalize_name_set(terminal_action_names)
    completion_action_tag_set = _normalize_name_set(completion_action_tags)
    required_action_name_set = _normalize_name_set(required_action_names)
    required_action_tag_set = _normalize_name_set(required_action_tags)
    if submit_result_only:
        terminal_action_name_set.add("submit_result")
    if max_action_calls is not None:
        max_action_calls = int(max_action_calls)
        if max_action_calls < 0:
            raise ValueError("max_action_calls must be non-negative")
    normalized_action_limits = {
        str(name).strip().lower(): int(limit)
        for name, limit in (action_call_limits or {}).items()
        if str(name).strip()
    }
    for action_name, limit in normalized_action_limits.items():
        if limit < 0:
            raise ValueError(f"action_call_limits[{action_name}] must be non-negative")
    action_attempt_counts: Counter[str] = Counter()
    action_call_counts: Counter[str] = Counter()
    action_payload_counts: Counter[tuple[str, str]] = Counter()
    successful_read_results: Dict[tuple[str, str], tuple[str, str]] = {}
    successful_read_outcomes: Dict[tuple[str, str], tuple[str, str]] = {}
    successful_read_action_keys: set[str] = set()
    failed_read_results: Dict[tuple[str, str], str] = {}
    presented_read_fact_keys: set[str] = set()
    decision_prompted_outcomes: set[tuple[str, str]] = set()
    repeated_read_streak = 0
    oversized_batch_rejections = 0
    parallel_tool_call_contract_rejections = 0
    empty_response_count = 0
    schema_error_count = 0

    def _is_system_action(action_name: str, action_info: Dict[str, Any]) -> bool:
        tags = {str(tag).lower() for tag in (action_info.get("tags", []) or [])}
        return action_name == "submit_result" or "system" in tags

    def _action_aliases(action_name: str) -> set[str]:
        aliases = {str(action_name).lower()}
        if "." in action_name:
            aliases.add(action_name.rsplit(".", maxsplit=1)[-1].lower())
        return aliases

    def _action_limit_for(action_name: str) -> Optional[int]:
        for alias in _action_aliases(action_name):
            if alias in normalized_action_limits:
                return normalized_action_limits[alias]
        return None

    def _default_tool_choice() -> Any:
        if not actions_schema:
            return None
        if list(action_set.actions.keys()) == ["submit_result"]:
            return {"type": "function", "function": {"name": "submit_result"}}
        successful_required_names = set()
        for action_call in all_action_calls:
            if getattr(action_call, "status", "success") == "success":
                successful_required_names.update(_action_aliases(action_call.action_name))
        remaining_required_names = required_action_name_set - successful_required_names
        required_candidates = [
            action_name
            for action_name in action_set.actions
            if _action_aliases(action_name) & remaining_required_names
        ]
        if len(required_candidates) == 1:
            return {
                "type": "function",
                "function": {"name": required_candidates[0]},
            }

        return "auto"

    def _all_available_action_budgets_exhausted() -> bool:
        """Return true when another LLM turn cannot execute any non-system action."""
        if (
            max_action_calls is not None
            and sum(action_attempt_counts.values()) >= max_action_calls
        ):
            return True

        limited_action_seen = False
        for action_name, action_info in action_set.actions.items():
            if _is_system_action(action_name, action_info):
                continue

            action_key = action_name.lower()
            limit = _action_limit_for(action_name)
            if limit is None:
                return False

            limited_action_seen = True
            if action_attempt_counts[action_key] < limit:
                return False

        return limited_action_seen

    def _action_matches_completion_tags(action_name: str) -> bool:
        if not completion_action_tag_set:
            return False
        action_info = action_set.actions.get(action_name) or {}
        explicit_tags = action_info.get("tags", []) or []
        merged_tags = {str(action_name).lower()}
        if "." in action_name:
            merged_tags.add(action_name.rsplit(".", maxsplit=1)[-1].lower())
        merged_tags.update(str(tag).lower() for tag in explicit_tags)
        return any(tag in merged_tags for tag in completion_action_tag_set)

    def _action_trace_tags(action_name: str) -> List[str]:
        action_info = action_set.actions.get(action_name) or {}
        explicit_tags = [str(tag) for tag in (action_info.get("tags", []) or [])]
        merged_tags = list(dict.fromkeys([str(action_name), *explicit_tags]))
        if "." in action_name:
            merged_tags.insert(1, action_name.rsplit(".", maxsplit=1)[-1])
        return list(dict.fromkeys(merged_tags))

    def _repeated_action_hint(
        action_name: str,
        *,
        occurrence: int,
        failed: bool,
    ) -> str:
        """提醒模型收束完全相同的调用，但不替它终止当前 Thread。"""

        if occurrence <= 1:
            return ""
        if failed:
            return (
                f"\n\n重复调用提示：这是本次激活中第 {occurrence} 次以完全相同的"
                "参数提交这个失败调用。再次原样提交会得到相同错误；"
                "请按上述报错修改具体字段或选择合法的另一种表达。"
            )
        tags = {tag.lower() for tag in _action_trace_tags(action_name)}
        if "industry_query" not in tags and not action_name.lower().startswith("query"):
            return ""
        return (
            f"\n\n重复读取提示：这是本次激活中第 {occurrence} 次以完全相同的"
            "参数读取该信息。若结果未变，继续原样查询不会增加新信息；"
            "请复用已有结果，改用能回答剩余问题的不同筛选，或据此作出经营判断。"
        )

    def _successful_action_aliases_and_tags() -> tuple[set[str], set[str]]:
        names: set[str] = set()
        tags: set[str] = set()
        for action_call in all_action_calls:
            if getattr(action_call, "status", "success") != "success":
                continue
            aliases = _action_aliases(action_call.action_name)
            names.update(aliases)
            tags.update(aliases)
            tags.update(str(tag).strip().lower() for tag in _action_trace_tags(action_call.action_name))
        return names, {tag for tag in tags if tag}

    def _missing_loop_requirements() -> tuple[List[str], List[str]]:
        successful_names, successful_tags = _successful_action_aliases_and_tags()
        missing_names = sorted(required_action_name_set - successful_names)
        missing_tags = sorted(required_action_tag_set - successful_tags)
        return missing_names, missing_tags

    def _required_action_reminder(missing_names: List[str], missing_tags: List[str]) -> str:
        parts = ["You have not completed the required action for this task."]
        if missing_names:
            parts.append(f"Required action name(s): {', '.join(missing_names)}.")
        if missing_tags:
            parts.append(f"Required action tag(s): {', '.join(missing_tags)}.")
        parts.append("Call the required tool now if the environment state allows it; do not just describe the action.")
        return " ".join(parts)

    def _empty_response_reminder() -> str:
        return (
            "Your previous response was empty: it contained neither visible text "
            "nor a tool call. Respond with a concrete decision in text, call an "
            "available tool, or explicitly state that no change is needed."
        )

    def _parallel_tool_call_batch_error(raw_action_calls: Any) -> Optional[str]:
        """Validate the assistant envelope before constructing tool receipts."""

        if not isinstance(raw_action_calls, list):
            return "tool_calls must be a list"

        seen_call_ids: set[str] = set()
        for index, raw_action_call in enumerate(raw_action_calls):
            if not isinstance(raw_action_call, dict):
                return f"tool call at index {index} must be an object"
            call_id = raw_action_call.get("id")
            if not isinstance(call_id, str) or not call_id.strip():
                return f"tool call at index {index} must have a non-empty string id"
            normalized_call_id = call_id.strip()
            if normalized_call_id in seen_call_ids:
                return f"duplicate tool call id: {call_id}"
            function = raw_action_call.get("function")
            if not isinstance(function, dict):
                return f"tool call at index {index} must have a function object"
            function_name = function.get("name")
            if not isinstance(function_name, str) or not function_name.strip():
                return f"tool call at index {index} must have a non-empty function name"
            seen_call_ids.add(normalized_call_id)
        return None


    for turn in range(max_turns):
        total_turns = turn + 1
        logger.debug("Action loop turn %s/%s", total_turns, max_turns)

        # Call LLM with current message history and actions
        turn_request_options = dict(safe_llm_request_options)
        if (
            empty_response_count > 0
            and empty_response_retry_temperature_delta is not None
            and empty_response_count <= empty_response_retry_max
        ):
            previous_temperature = turn_request_options.get("temperature")
            if previous_temperature is None:
                previous_temperature = 0.0
            try:
                previous_temperature = float(previous_temperature)
            except (TypeError, ValueError) as exc:
                raise ValueError("temperature must be numeric") from exc
            next_temperature = round(
                min(
                    previous_temperature + empty_response_retry_temperature_delta,
                    empty_response_retry_temperature_max,
                ),
                12,
            )
            turn_request_options["temperature"] = next_temperature
            _append_thread_event(
                "provider_empty_response_retry",
                {
                    "attempt": empty_response_count,
                    "temperature_before": previous_temperature,
                    "temperature_after": next_temperature,
                    "retry_scope": "agent_activation",
                    "reason": "empty_model_response",
                },
            )
        if repeated_read_streak > 0 and repeated_read_temperature_delta is not None:
            previous_temperature = turn_request_options.get("temperature")
            if previous_temperature is None:
                previous_temperature = 0.0
            try:
                previous_temperature = float(previous_temperature)
            except (TypeError, ValueError) as exc:
                raise ValueError("temperature must be numeric") from exc
            next_temperature = round(
                min(
                    previous_temperature
                    + repeated_read_streak * repeated_read_temperature_delta,
                    repeated_read_temperature_max,
                ),
                12,
            )
            turn_request_options["temperature"] = next_temperature
            _append_thread_event(
                "provider_repeated_read_diversification",
                {
                    "streak": repeated_read_streak,
                    "temperature_before": previous_temperature,
                    "temperature_after": next_temperature,
                    "reason": "unchanged_read_result",
                },
            )
        llm_payload = {
            "messages": _request_messages(),
            "tools": copy.deepcopy(actions_schema) if actions_schema else None,
            "tool_choice": _default_tool_choice()
        }
        llm_payload.update(turn_request_options)

        # --- 调试点：注释掉旧的调试信息 ---
        # print(f"--- [DEBUG] LLM Payload for Turn {turn + 1} ---")
        # import json
        # print(json.dumps(llm_payload, indent=2, ensure_ascii=False))
        # --- 结束 ---

        provider_attempt = 1
        provider_error: BaseException | None = None
        provider_failure_class: str | None = None
        while True:
            try:
                # Each attempt uses the same logical payload and happens before
                # any tool call from this turn, so a transport retry cannot
                # replay a successful domain action.
                response = await llm_call(llm_payload)
                break
            except Exception as exc:
                failure_class = _provider_transport_failure_class(exc)
                if (
                    failure_class is None
                    or provider_attempt > provider_request_retry_max
                ):
                    _append_thread_event(
                        "provider_request_failed",
                        {
                            "attempt": provider_attempt,
                            "max_retries": provider_request_retry_max,
                            "failure_class": failure_class or "provider_request_error",
                            "retry_scope": "agent_activation",
                            "exhausted": failure_class is not None,
                            "error_type": type(exc).__name__,
                            "error": str(exc) or repr(exc),
                        },
                    )
                    if failure_class is None:
                        raise
                    try:
                        setattr(exc, "failure_class", failure_class)
                        setattr(exc, "retry_scope", "agent_activation")
                        setattr(exc, "retry_attempts", provider_attempt)
                    except Exception:
                        pass
                    provider_error = exc
                    provider_failure_class = failure_class
                    break
                _append_thread_event(
                    "provider_request_retry",
                    {
                        "attempt": provider_attempt,
                        "next_attempt": provider_attempt + 1,
                        "max_retries": provider_request_retry_max,
                        "failure_class": failure_class,
                        "retry_scope": "agent_activation",
                        "error_type": type(exc).__name__,
                        "error": str(exc) or repr(exc),
                    },
                )
                provider_attempt += 1
        if provider_error is not None:
            loop_result.termination_reason = "provider_request_exhausted"
            loop_result.error = _format_provider_error(provider_error)
            loop_result.failure_class = provider_failure_class
            loop_result.retry_scope = "agent_activation"
            loop_result.retry_attempts = provider_attempt
            full_history.append(
                {
                    "turn": total_turns,
                    "request": llm_payload,
                    "response": None,
                    "error": loop_result.error,
                    "failure_class": provider_failure_class,
                    "retry_scope": "agent_activation",
                    "provider_request_attempts": provider_attempt,
                }
            )
            break
        # print(f"[DEBUG] {response}")  # 注释掉详细响应调试
        full_history.append(
            {
                "turn": total_turns,
                "request": llm_payload,
                "response": response,
                "provider_request_attempts": provider_attempt,
            }
        )

        finish_reason = str(response.get("finish_reason") or "").lower()
        response_message = _conversation_response(response)
        if finish_reason == "length":
            raw_tool_calls = response_message.get("tool_calls")
            # 输出截断优先于工具调用：即使 provider 同时返回一个完整的
            # tool call，也不能把未完成的响应当作可执行行动。
            response_message = dict(response_message)
            response_message["content"] = ""
            response_message.pop("reasoning_content", None)
            _append_runtime_message(response_message)
            if isinstance(raw_tool_calls, list) and raw_tool_calls:
                _append_thread_event(
                    "provider_output_truncated_with_tool_calls",
                    {"tool_call_count": len(raw_tool_calls)},
                )
            loop_result.termination_reason = "output_token_limit"
            loop_result.error = "model output reached the configured token limit"
            loop_result.failure_class = "provider_output_truncated"
            loop_result.retry_scope = "agent_activation"
            break

        response = response_message
        action_calls = response.get("tool_calls", [])
        is_parallel_disabled = (
            safe_llm_request_options.get("parallel_tool_calls") is False
        )
        is_multi_action_batch = (
            isinstance(action_calls, list) and len(action_calls) > 1
        )
        if (
            is_parallel_disabled
            and action_calls
        ):
            parallel_batch_error = _parallel_tool_call_batch_error(action_calls)
            if parallel_batch_error is not None:
                parallel_tool_call_contract_rejections += 1
                # 结构不合法时不把 assistant/tool 消息放入对话，避免留下
                # provider 无法继续消费的工具历史；本次激活直接未完成。
                full_history[-1]["batch_termination_reason"] = (
                    "parallel_tool_call_contract_violation"
                )
                full_history[-1]["rejected_tool_call_count"] = (
                    len(action_calls) if isinstance(action_calls, list) else 0
                )
                full_history[-1]["contract_error"] = parallel_batch_error
                _append_thread_event(
                    "parallel_tool_call_contract_rejected",
                    {
                        "tool_call_count": (
                            len(action_calls) if isinstance(action_calls, list) else 0
                        ),
                        "rejection_count": parallel_tool_call_contract_rejections,
                        "reason": "invalid_tool_call_batch",
                        "error": parallel_batch_error,
                    },
                )
                loop_result.termination_reason = (
                    "parallel_tool_call_contract_violation"
                )
                loop_result.error = (
                    "model returned an invalid tool-call batch while "
                    f"parallel_tool_calls=false: {parallel_batch_error}"
                )
                loop_result.failure_class = (
                    "parallel_tool_call_contract_violation"
                )
                loop_result.retry_scope = "agent_activation"
                loop_result.retry_attempts = parallel_tool_call_contract_rejections
                break

            if not is_multi_action_batch:
                # 合法的单个 tool call 继续走下面的普通 Action 执行路径。
                pass
            else:
                parallel_tool_call_contract_rejections += 1
                # Provider 端即使接受了 parallel_tool_calls=false，也可能在
                # 一个 assistant 消息中返回多个调用。必须先整批拒绝，避免
                # 主体在看到第一个行动结果之前改变环境多次。
                _append_runtime_message(response)
                rejection_content = (
                    "本轮返回了多个工具调用，整批已在执行前拒绝；"
                    "本轮没有执行任何 Action。每次只能提交一个最小必要的 "
                    "Action，看到该 Action 的工具结果后再判断下一步。"
                )
                turn_tool_messages = []
                for raw_action_call in action_calls:
                    call_id = str(raw_action_call.get("id") or "")
                    tool_message = {
                        "role": "tool",
                        "content": rejection_content,
                        "tool_call_id": call_id,
                    }
                    _append_runtime_message(tool_message)
                    turn_tool_messages.append(dict(tool_message))
                full_history[-1]["tool_messages"] = turn_tool_messages
                full_history[-1]["batch_termination_reason"] = (
                    "parallel_tool_call_contract_violation"
                )
                full_history[-1]["rejected_tool_call_count"] = len(action_calls)
                _append_thread_event(
                    "parallel_tool_call_contract_rejected",
                    {
                        "tool_call_count": len(action_calls),
                        "rejection_count": parallel_tool_call_contract_rejections,
                        "reason": "parallel_tool_calls_false",
                    },
                )
                if (
                    parallel_tool_call_contract_rejections == 1
                    and turn + 1 < max_turns
                ):
                    continue
                loop_result.termination_reason = (
                    "parallel_tool_call_contract_violation"
                )
                loop_result.error = (
                    "model returned multiple tool calls while "
                    "parallel_tool_calls=false"
                )
                loop_result.failure_class = (
                    "parallel_tool_call_contract_violation"
                )
                loop_result.retry_scope = "agent_activation"
                loop_result.retry_attempts = parallel_tool_call_contract_rejections
                break

        # Extract reasoning content, final content and action calls using new function
        reasoning_content, final_content_part, response_metadata = (
            _extract_reasoning_content(response)
        )

        # Handle reasoning content accumulation (independent handling)
        if reasoning_content:
            # 累积推理内容
            if loop_result.reasoning_content is None:
                loop_result.reasoning_content = reasoning_content
            else:
                loop_result.reasoning_content += "\n\n" + reasoning_content

            # 累积思考过程（如果有推理内容就记录）
            loop_result.thinking_process.append({
                "turn": total_turns,
                "content": reasoning_content,
                "metadata": response_metadata
            })

            # 设置推理标志
            loop_result.has_reasoning = True

            # 设置模型类型（只在首次检测到时设置，避免被后续标准响应覆盖）
            if loop_result.model_type is None:
                loop_result.model_type = response_metadata["model_type"]

            # 推理模型监控输出
            logger.debug(
                "Reasoning content captured for turn %s: %s",
                total_turns,
                reasoning_content[:100],
            )
        else:
            # 即使没有推理内容，也要设置模型类型（确保所有响应都有类型）
            if loop_result.model_type is None:
                loop_result.model_type = response_metadata["model_type"]

        # Handle final content accumulation
        if final_content_part:
            if final_content is None:
                final_content = final_content_part
            else:
                final_content += "\n\n" + final_content_part

        # Add assistant message to history
        _append_runtime_message(response)

        # Execute action calls if present
        if not action_calls:
            if not str(final_content_part or "").strip():
                empty_response_count += 1
                if turn + 1 < max_turns:
                    _append_thread_event(
                        "provider_empty_response",
                        {
                            "attempt": empty_response_count,
                            "retry_scope": "agent_activation",
                            "configured_temperature_retry": (
                                empty_response_retry_temperature_delta is not None
                                and empty_response_count <= empty_response_retry_max
                            ),
                        },
                    )
                    _append_runtime_message(
                        {"role": "user", "content": _empty_response_reminder()}
                    )
                    continue
                loop_result.termination_reason = "empty_model_response"
                loop_result.failure_class = "provider_empty_response"
                loop_result.retry_scope = "agent_activation"
                loop_result.retry_attempts = empty_response_count
                break
            missing_names, missing_tags = _missing_loop_requirements()
            if (missing_names or missing_tags) and turn + 1 < max_turns:
                _append_runtime_message({"role": "user", "content": _required_action_reminder(missing_names, missing_tags)})
                continue
            if missing_names:
                loop_result.termination_reason = "missing_required_action"
            elif missing_tags:
                loop_result.termination_reason = "missing_required_action_tag"
            else:
                loop_result.termination_reason = "no_action_calls"
            break

        # print(f"[ActionCalls] {len(action_calls)} action calls")
        # print(action_calls)
        parsed_action_calls: List[ActionCall] = []
        for raw_action_call in action_calls:
            if not isinstance(raw_action_call, dict):
                action_call = ActionCall(
                    call_id="",
                    action_name="",
                    arguments={},
                )
                action_call.status = "error"
                action_call.error = (
                    "Tool schema error: action call must be an object"
                )
                schema_error_count += 1
                parsed_action_calls.append(action_call)
                continue
            function = raw_action_call.get("function")
            if not isinstance(function, dict):
                function = {}
            raw_arguments = function.get("arguments")
            action_call = ActionCall(
                call_id=str(raw_action_call.get("id") or ""),
                action_name=str(function.get("name") or ""),
                arguments={},
            )
            try:
                parsed_arguments = json_repair.loads(
                    raw_arguments if str(raw_arguments or "").strip() else {}
                )
                if not isinstance(parsed_arguments, dict):
                    raise TypeError("tool arguments must be a JSON object")
                action_call.arguments = parsed_arguments
                action_info = action_set.actions.get(action_call.action_name)
                if action_info and action_info.get("argument_validator") is not None:
                    action_call.arguments = _normalize_strict_action_arguments(
                        action_call.arguments,
                        action_info["parameters"],
                    )
            except Exception as exc:
                action_call.status = "error"
                action_call.error = (
                    f"Tool schema error for {action_call.action_name}: "
                    f"invalid arguments ({exc})"
                )
                schema_error_count += 1
            parsed_action_calls.append(action_call)
        action_calls = parsed_action_calls
        unique_action_calls = []
        duplicate_call_ids: set[str] = set()
        seen_action_payloads: set[tuple[str, str]] = set()
        for action_call in action_calls:
            payload_key = (
                action_call.action_name.lower(),
                json.dumps(
                    action_call.arguments,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
            )
            if payload_key in seen_action_payloads:
                duplicate_call_ids.add(action_call.call_id)
                continue
            seen_action_payloads.add(payload_key)
            unique_action_calls.append(action_call)

        turn_tool_messages = []  # 本轮的工具结果消息，写入 full_history 便于事后重建
        executed_action_calls = []
        terminate_loop = False
        remaining_turns = max_turns - (turn + 1)
        should_hint = turn_remain_hint and remaining_turns <= hint_on_remain_turn

        batch_limit_error: Optional[str] = None
        batch_termination_reason: Optional[str] = None
        if max_action_calls is not None:
            remaining_global_budget = max(
                max_action_calls - sum(action_attempt_counts.values()),
                0,
            )
            if len(unique_action_calls) > remaining_global_budget:
                batch_limit_error = (
                    "Action batch exceeds remaining budget: "
                    f"requested={len(unique_action_calls)}, "
                    f"remaining={remaining_global_budget}, "
                    f"max_action_calls={max_action_calls}"
                )
                batch_termination_reason = "action_batch_exceeds_budget"

        if batch_limit_error is None and normalized_action_limits:
            batch_action_counts = Counter(
                action_call.action_name.lower()
                for action_call in unique_action_calls
            )
            for action_name, requested_count in batch_action_counts.items():
                per_action_limit = _action_limit_for(action_name)
                if per_action_limit is None:
                    continue
                remaining_action_budget = max(
                    per_action_limit - action_attempt_counts[action_name],
                    0,
                )
                if requested_count > remaining_action_budget:
                    batch_limit_error = (
                        f"Action batch exceeds remaining limit for {action_name}: "
                        f"requested={requested_count}, "
                        f"remaining={remaining_action_budget}, "
                        f"limit={per_action_limit}"
                    )
                    batch_termination_reason = (
                        "action_batch_exceeds_action_limit"
                    )
                    break

        if batch_limit_error is not None:
            base_content = (
                f"Error: {batch_limit_error}. "
                "The action batch was rejected before execution."
            )
            for action_call in action_calls:
                tool_message = {
                    "role": "tool",
                    "content": base_content,
                    "tool_call_id": action_call.call_id,
                }
                _append_runtime_message(tool_message)
                turn_tool_messages.append(dict(tool_message))
                action_call.result = batch_limit_error
                action_call.status = "blocked"
                action_call.error = batch_limit_error
                action_call.duration_sec = 0.0
                executed_action_calls.append(action_call)
                if context_provider is not None:
                    try:
                        provided = context_provider()
                        record_action = provided[2] if len(provided) > 2 else None
                        if record_action is not None:
                            record_action(
                                action_call.action_name,
                                action_call.arguments,
                                batch_limit_error,
                                "blocked",
                            )
                    except Exception:
                        logger.debug(
                            "Failed to record batch-blocked action %s",
                            action_call.action_name,
                            exc_info=True,
                        )

            all_action_calls.extend(executed_action_calls)
            if full_history:
                full_history[-1]["tool_messages"] = turn_tool_messages
                full_history[-1]["batch_termination_reason"] = batch_termination_reason
                full_history[-1]["action_results"] = [
                    {
                        "call_id": action_call.call_id,
                        "action_name": action_call.action_name,
                        "arguments": action_call.arguments,
                        "result": action_call.result,
                        "status": action_call.status,
                        "duration_sec": action_call.duration_sec,
                        "error": action_call.error,
                    }
                    for action_call in executed_action_calls
                ]
            remaining_global_budget = (
                max(max_action_calls - sum(action_attempt_counts.values()), 0)
                if max_action_calls is not None
                else None
            )
            can_retry_smaller_batch = (
                oversized_batch_rejections == 0
                and turn + 1 < max_turns
                and (remaining_global_budget is None or remaining_global_budget > 0)
            )
            if can_retry_smaller_batch:
                oversized_batch_rejections += 1
                _append_runtime_message(
                    {
                        "role": "user",
                        "content": (
                            "The entire previous action batch was rejected without "
                            "execution. Submit only the minimum necessary action calls "
                            "and stay within the remaining action limits."
                        ),
                    }
                )
                continue

            if batch_termination_reason == "action_batch_exceeds_budget":
                loop_result.termination_reason = (
                    "action_budget_exhausted"
                    if remaining_global_budget == 0
                    else batch_termination_reason
                )
            else:
                loop_result.termination_reason = batch_termination_reason
            break

        prompt_decision_after_tools = False
        first_decision_prompt_this_turn = False
        for idx, action_call in enumerate(action_calls):
            # Action执行监控 - 增强版
            logger.debug("Executing action: %s", action_call.action_name)
            if action_call.arguments:
                logger.debug("Action parameters: %s", action_call.arguments)
            is_last_action_in_turn = idx == len(action_calls) - 1
            action_key = action_call.action_name.lower()
            payload_key = (
                action_key,
                json.dumps(
                    action_call.arguments,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
            )
            action_payload_counts[payload_key] += 1
            payload_occurrence = action_payload_counts[payload_key]
            trace_tags = {
                tag.lower() for tag in _action_trace_tags(action_call.action_name)
            }
            is_read_action = (
                "industry_query" in trace_tags
                or action_call.action_name.lower().startswith("query")
            )
            action_succeeded = False
            if action_call.call_id in duplicate_call_ids:
                duplicate_message = (
                    "Duplicate action call in the same assistant turn was "
                    "suppressed without execution."
                )
                tool_message = {
                    "role": "tool",
                    "content": duplicate_message,
                    "tool_call_id": action_call.call_id,
                }
                _append_runtime_message(tool_message)
                turn_tool_messages.append(dict(tool_message))
                action_call.result = duplicate_message
                action_call.status = "blocked"
                action_call.error = duplicate_message
                action_call.duration_sec = 0.0
                executed_action_calls.append(action_call)
                continue
            limit_error: Optional[str] = None
            global_budget_exhausted = (
                max_action_calls is not None
                and sum(action_attempt_counts.values()) >= max_action_calls
            )
            if global_budget_exhausted:
                limit_error = f"Action budget exhausted: max_action_calls={max_action_calls}"
            per_action_limit = _action_limit_for(action_call.action_name)
            if (
                limit_error is None
                and per_action_limit is not None
                and action_attempt_counts[action_key] >= per_action_limit
            ):
                limit_error = (
                    f"Action budget exhausted for {action_call.action_name}: "
                    f"limit={per_action_limit}"
                )

            if limit_error is not None:
                base_content = f"Error: {limit_error}. The action was not executed."
                display_content = base_content
                if should_hint and is_last_action_in_turn:
                    display_content = (
                        base_content
                        + "\n\n⚠️ 提示：这是你最后一次行动机会，请在本轮完成必要的工具调用或提交结果。"
                    )
                tool_message = {
                    "role": "tool",
                    "content": display_content,
                    "tool_call_id": action_call.call_id
                }
                _append_runtime_message(tool_message)
                turn_tool_messages.append({
                    "role": "tool",
                    "content": base_content,
                    "tool_call_id": action_call.call_id
                })
                action_call.result = limit_error
                action_call.status = "blocked"
                action_call.error = limit_error
                action_call.duration_sec = 0.0
                executed_action_calls.append(action_call)
                if context_provider is not None:
                    try:
                        provided = context_provider()
                        record_action = provided[2] if len(provided) > 2 else None
                        if record_action is not None:
                            record_action(action_call.action_name, action_call.arguments, limit_error, "blocked")
                    except Exception:
                        logger.debug("Failed to record blocked action %s", action_call.action_name, exc_info=True)
                if global_budget_exhausted:
                    discarded = len(action_calls) - idx - 1
                    if discarded > 0 and full_history:
                        full_history[-1]["discarded_action_call_count"] = discarded
                    break
                continue

            # Malformed JSON arguments never reach the environment. Return a
            # structured tool error through the same conversation so the Agent
            # can correct the call on its next turn.
            if action_call.error is not None and action_call.status == "error":
                action_attempt_counts[action_key] += 1
                error_msg = action_call.error
                _append_thread_event(
                    "tool_execution_failed",
                    {
                        "call_id": action_call.call_id,
                        "action_name": action_call.action_name,
                        "arguments": copy.deepcopy(action_call.arguments),
                        "error": error_msg,
                        "failure_class": "tool_schema_error",
                        "retry_scope": "agent_activation",
                        "status": "error",
                        "duration_sec": 0.0,
                    },
                )
                if (
                    is_read_action
                    and failed_read_results.get(payload_key) == error_msg
                ):
                    display_error = (
                        f"这是本次激活中第 {payload_occurrence} 次以完全相同参数"
                        f"调用 {action_call.action_name}，并得到完全相同的错误。"
                        "上一次错误和修正信息已在上文保留；请按错误提示修正参数，"
                        "改用不同查询，或基于已有事实形成经营判断。不要原样重试。"
                    )
                    prompt_decision_after_tools = True
                    outcome_key = (f"failed:{action_key}", error_msg)
                    if outcome_key not in decision_prompted_outcomes:
                        decision_prompted_outcomes.add(outcome_key)
                        first_decision_prompt_this_turn = True
                else:
                    display_error = error_msg + _repeated_action_hint(
                        action_call.action_name,
                        occurrence=payload_occurrence,
                        failed=True,
                    )
                if is_read_action:
                    failed_read_results[payload_key] = error_msg
                tool_message = {
                    "role": "tool",
                    "content": display_error,
                    "tool_call_id": action_call.call_id,
                }
                _append_runtime_message(tool_message)
                turn_tool_messages.append(dict(tool_message))
                action_call.result = error_msg
                action_call.duration_sec = 0.0
                executed_action_calls.append(action_call)
                continue

            action_attempt_counts[action_key] += 1
            action_started = time.perf_counter()
            _append_thread_event_best_effort(
                "tool_execution_started",
                {
                    "call_id": action_call.call_id,
                    "action_name": action_call.action_name,
                    "arguments": copy.deepcopy(action_call.arguments),
                },
            )
            action_exception: Optional[Exception] = None
            action_result: Any = None
            try:
                action_result = await action_set.call_action(
                    action_call.action_name,
                    context_provider=context_provider,
                    _society0_call_id=action_call.call_id,
                    **action_call.arguments
                )
            except asyncio.CancelledError as exc:
                action_call.duration_sec = round(
                    max(time.perf_counter() - action_started, 0.0),
                    6,
                )
                action_call.status = "cancelled"
                action_call.error = str(exc) or repr(exc)
                try:
                    cancelled_arguments = copy.deepcopy(action_call.arguments)
                except BaseException as trace_exc:
                    cancelled_arguments = {
                        "__trace_error__": str(trace_exc) or repr(trace_exc),
                    }
                _append_thread_event_best_effort(
                    "tool_execution_cancelled",
                    {
                        "call_id": action_call.call_id,
                        "action_name": action_call.action_name,
                        "arguments": cancelled_arguments,
                        "error": action_call.error,
                        "status": "cancelled",
                        "duration_sec": action_call.duration_sec,
                    },
                )
                raise
            except Exception as exc:
                action_exception = exc
            finally:
                action_call.duration_sec = round(
                    max(time.perf_counter() - action_started, 0.0),
                    6,
                )

            if action_exception is not None:
                schema_failure = _is_tool_schema_error(action_exception)
                if schema_failure:
                    schema_error_count += 1
                    error_msg = (
                        f"Tool schema error for {action_call.action_name}: "
                        f"{action_exception}"
                    )
                else:
                    error_msg = (
                        f"Error executing action {action_call.action_name}: "
                        f"{action_exception}"
                    )
                _append_thread_event(
                    "tool_execution_failed",
                    {
                        "call_id": action_call.call_id,
                        "action_name": action_call.action_name,
                        "arguments": copy.deepcopy(action_call.arguments),
                        "error": error_msg,
                        **(
                            {
                                "failure_class": "tool_schema_error",
                                "retry_scope": "agent_activation",
                            }
                            if schema_failure
                            else {}
                        ),
                        "status": "error",
                        "duration_sec": action_call.duration_sec,
                    },
                )
                logger.debug("Action error: %s", error_msg)
                if (
                    is_read_action
                    and failed_read_results.get(payload_key) == error_msg
                ):
                    base_content = (
                        f"这是本次激活中第 {payload_occurrence} 次以完全相同参数"
                        f"调用 {action_call.action_name}，并得到完全相同的错误。"
                        "上一次错误和修正信息已在上文保留；请按错误提示修正参数，"
                        "改用不同查询，或基于已有事实形成经营判断。不要原样重试。"
                    )
                    prompt_decision_after_tools = True
                    outcome_key = (f"failed:{action_key}", error_msg)
                    if outcome_key not in decision_prompted_outcomes:
                        decision_prompted_outcomes.add(outcome_key)
                        first_decision_prompt_this_turn = True
                else:
                    base_content = f"Error: {error_msg}" + _repeated_action_hint(
                        action_call.action_name,
                        occurrence=payload_occurrence,
                        failed=True,
                    )
                if is_read_action:
                    failed_read_results[payload_key] = error_msg
                display_content = base_content
                if should_hint and is_last_action_in_turn:
                    display_content = (
                        base_content
                        + "\n\n⚠️ 提示：这是你最后一次行动机会，请在本轮完成必要的工具调用或提交结果。"
                    )
                tool_message = {
                    "role": "tool",
                    "content": display_content,
                    "tool_call_id": action_call.call_id
                }
                _append_runtime_message(tool_message)
                turn_tool_messages.append({
                    "role": "tool",
                    "content": base_content,
                    "tool_call_id": action_call.call_id
                })
                action_call.result = error_msg
                action_call.status = "error"
                action_call.error = error_msg
                executed_action_calls.append(action_call)
            else:
                action_call.result = action_result
                action_call.status, action_call.error = _semantic_action_status(action_result)
                event_type = (
                    "tool_execution_completed"
                    if action_call.status == "success"
                    else "tool_execution_failed"
                )
                event_payload: Dict[str, Any] = {
                    "call_id": action_call.call_id,
                    "action_name": action_call.action_name,
                    "arguments": copy.deepcopy(action_call.arguments),
                    "result": copy.deepcopy(action_result),
                    "status": action_call.status,
                    "duration_sec": action_call.duration_sec,
                }
                if action_call.error:
                    event_payload["error"] = action_call.error
                _append_thread_event(event_type, event_payload)

                if (
                    action_call.status == "success"
                    and not is_read_action
                    and not (
                        isinstance(action_result, Mapping)
                        and action_result.get("change_applied") is False
                    )
                ):
                    presented_read_fact_keys.clear()
                    successful_read_results.clear()
                    successful_read_outcomes.clear()
                    successful_read_action_keys.clear()
                    failed_read_results.clear()

                result_content = str(action_result)
                result_signature = _read_outcome_signature(
                    action_result,
                    result_content,
                )
                read_fact_keys = (
                    _read_fact_keys(action_result)
                    if action_call.status == "success" and is_read_action
                    else frozenset()
                )
                new_read_fact_keys = read_fact_keys - presented_read_fact_keys
                if new_read_fact_keys:
                    presented_read_fact_keys.update(new_read_fact_keys)
                same_action_read_seen = action_key in successful_read_action_keys
                previous_read = successful_read_results.get(payload_key)
                previous_outcome = successful_read_outcomes.get(
                    (action_key, result_signature)
                )
                previous_failed_read = failed_read_results.get(payload_key)
                if (
                    action_call.status != "success"
                    and is_read_action
                    and previous_failed_read == result_content
                ):
                    base_content = (
                        f"这是本次激活中第 {payload_occurrence} 次以完全相同参数"
                        f"调用 {action_call.action_name}，并得到完全相同的错误。"
                        "上一次错误和修正信息已在上文保留；请按错误提示修正参数，"
                        "改用不同查询，或基于已有事实形成经营判断。不要原样重试。"
                    )
                    prompt_decision_after_tools = True
                    outcome_key = (f"failed:{action_key}", result_signature)
                    if outcome_key not in decision_prompted_outcomes:
                        decision_prompted_outcomes.add(outcome_key)
                        first_decision_prompt_this_turn = True
                elif (
                    action_call.status == "success"
                    and is_read_action
                    and read_fact_keys
                    and new_read_fact_keys
                ):
                    base_content = result_content + _repeated_action_hint(
                        action_call.action_name,
                        occurrence=payload_occurrence,
                        failed=False,
                    )
                    successful_read_results[payload_key] = (
                        result_content,
                        action_call.call_id,
                    )
                    successful_read_outcomes.setdefault(
                        (action_key, result_signature),
                        (payload_key[1], action_call.call_id),
                    )
                elif (
                    action_call.status == "success"
                    and is_read_action
                    and read_fact_keys
                    and not new_read_fact_keys
                    and not same_action_read_seen
                ):
                    base_content = (
                        "这次读取涉及的事实已经在此前董事会/查询答案中完整呈现；"
                        "为避免重复发送完整结果，本次只保留执行记录。请回到当前"
                        "经营判断；若仍有缺口，请提出会改变判断的具体查询。"
                    )
                    successful_read_results[payload_key] = (
                        result_content,
                        action_call.call_id,
                    )
                    successful_read_outcomes.setdefault(
                        (action_key, result_signature),
                        (payload_key[1], action_call.call_id),
                    )
                    prompt_decision_after_tools = True
                    outcome_key = (f"covered:{action_key}", result_signature)
                    if outcome_key not in decision_prompted_outcomes:
                        decision_prompted_outcomes.add(outcome_key)
                        first_decision_prompt_this_turn = True
                elif (
                    action_call.status == "success"
                    and is_read_action
                    and previous_read is not None
                    and previous_read[0] == result_content
                ):
                    recalled_content = _repeated_read_recall(result_content)
                    base_content = (
                        f"这是本次激活中第 {payload_occurrence} 次以完全相同参数"
                        f"调用 {action_call.action_name}；该读取与先前结果完全一致，"
                        "当前事实没有变化。为便于据已有事实判断，下面重现该查询"
                        "开头的决策摘要（不是新增事实）：\n"
                        f"{recalled_content}\n\n"
                        "完整结果已在上文保留；请直接复用，"
                        "不要再以相同参数读取。若已有事实足以判断，现在形成经营决定；"
                        "只有缺少会改变判断的具体事实时，才改用不同筛选继续查询。"
                    )
                    prompt_decision_after_tools = True
                    outcome_key = (action_key, result_signature)
                    if outcome_key not in decision_prompted_outcomes:
                        decision_prompted_outcomes.add(outcome_key)
                        first_decision_prompt_this_turn = True
                elif (
                    action_call.status == "success"
                    and is_read_action
                    and previous_outcome is not None
                ):
                    base_content = (
                        "这次虽然使用了不同筛选参数，但返回结果与本次激活内"
                        "先前同一查询工具的结果完全一致，没有增加新事实。"
                        "完整结果已在上文保留；请复用已有结果并回到当前"
                        "经营问题，不要通过更换关键词继续探测同一空结果或"
                        "不变结果。"
                    )
                    successful_read_results[payload_key] = (
                        result_content,
                        action_call.call_id,
                    )
                    prompt_decision_after_tools = True
                    outcome_key = (action_key, result_signature)
                    if outcome_key not in decision_prompted_outcomes:
                        decision_prompted_outcomes.add(outcome_key)
                        first_decision_prompt_this_turn = True
                else:
                    base_content = result_content + _repeated_action_hint(
                        action_call.action_name,
                        occurrence=payload_occurrence,
                        failed=action_call.status != "success",
                    )
                    if action_call.status == "success" and is_read_action:
                        successful_read_results[payload_key] = (
                            result_content,
                            action_call.call_id,
                        )
                        successful_read_outcomes.setdefault(
                            (action_key, result_signature),
                            (payload_key[1], action_call.call_id),
                        )
                if action_call.status == "success" and is_read_action:
                    successful_read_action_keys.add(action_key)
                if action_call.status != "success" and is_read_action:
                    failed_read_results[payload_key] = result_content
                display_content = base_content
                if should_hint and is_last_action_in_turn:
                    display_content = (
                        base_content
                        + "\n\n⚠️ 提示：这是你最后一次行动机会，请在本轮完成必要的工具调用或提交结果。"
                    )
                tool_message = {
                    "role": "tool",
                    "content": display_content,
                    "tool_call_id": action_call.call_id
                }
                _append_runtime_message(tool_message)
                turn_tool_messages.append({
                    "role": "tool",
                    "content": base_content,
                    "tool_call_id": action_call.call_id
                })
                executed_action_calls.append(action_call)
                if action_call.status == "success":
                    action_call_counts[action_key] += 1
                    action_succeeded = True

                logger.debug("Action result: %s", str(action_result)[:100])

            if action_succeeded and action_call.action_name.lower() in terminal_action_name_set:
                terminate_loop = True
                loop_result.termination_reason = "terminal_action"
                loop_result.termination_action = action_call.action_name
                logger.debug(
                    "Terminal action hit: %s, ending loop early",
                    action_call.action_name,
                )
                break
            if action_succeeded and _action_matches_completion_tags(action_call.action_name):
                terminate_loop = True
                loop_result.termination_reason = "completion_action_tag"
                loop_result.termination_action = action_call.action_name
                logger.debug(
                    "Completion action tag hit: %s, ending loop early",
                    action_call.action_name,
                )
                break

        all_action_calls.extend(executed_action_calls)

        # 将本轮的工具结果写入 full_history，便于后续重建对话
        if full_history:
            full_history[-1]["tool_messages"] = turn_tool_messages
            full_history[-1]["action_results"] = [
                {
                    "call_id": ac.call_id,
                    "action_name": ac.action_name,
                    "arguments": ac.arguments,
                    "result": ac.result,
                    "status": ac.status,
                    **({"duration_sec": ac.duration_sec} if ac.duration_sec is not None else {}),
                    **({"error": ac.error} if ac.error else {}),
                }
                for ac in executed_action_calls
            ]

        if (
            prompt_decision_after_tools
            and not terminate_loop
            and (turn + 1 < max_turns or first_decision_prompt_this_turn)
        ):
            _append_runtime_message(
                {
                    "role": "user",
                    "content": (
                        "调查进度提醒：你刚才的读取没有增加新事实；可能是"
                        "相同参数重复读取，也可能是更换筛选后仍得到完全一致的"
                        "结果。这条提醒不会结束当前任务，你仍然拥有全部"
                        "工具。下一步请直接作出经营判断、执行必要的经营行动，"
                        "或改用能补足某个具体关键缺口的不同查询。不要再原样"
                        "提交这个读取，也不要只更换关键词继续探测同一结果。"
                    ),
                }
            )

        if prompt_decision_after_tools:
            repeated_read_streak += 1
        else:
            repeated_read_streak = 0

        if terminate_loop:
            break
        if _all_available_action_budgets_exhausted():
            loop_result.termination_reason = "action_budget_exhausted"
            break
        continue
    else:
        if loop_result.termination_reason is None:
            missing_names, missing_tags = _missing_loop_requirements()
            if missing_names:
                loop_result.termination_reason = "missing_required_action"
            elif missing_tags:
                loop_result.termination_reason = "missing_required_action_tag"
            elif schema_error_count:
                loop_result.termination_reason = "tool_schema_error_exhausted"
                loop_result.failure_class = "tool_schema_error"
                loop_result.retry_scope = "agent_activation"
                loop_result.retry_attempts = schema_error_count
            else:
                loop_result.termination_reason = "max_turns"

    # Parse the final response content into stages
    phases, phases_unknown, parsing_errors = _parse_stages(
        final_content, stage_names, default_stage_name
    )

    action_call_entries = [
        {
            "type": "action_call",
            "action_name": action_call.action_name,
            "arguments": action_call.arguments,
            "call_id": action_call.call_id,
            "result": action_call.result,
            "status": action_call.status,
            "tags": _action_trace_tags(action_call.action_name),
            **({"duration_sec": action_call.duration_sec} if action_call.duration_sec is not None else {}),
            **({"error": action_call.error} if action_call.error else {}),
            **(
                {
                    "failure_class": "tool_schema_error",
                    "retry_scope": "agent_activation",
                }
                if action_call.error and _is_tool_schema_error(action_call.error)
                else {}
            ),
        }
        for action_call in all_action_calls
    ]

    # Process phases and append action calls to a target stage without binding to a fixed stage name.
    processed_phases = dict(phases)
    if action_call_entries:
        target_stage_name = next(
            (name for name in processed_phases if name.lower() == "actions"),
            None,
        )
        if target_stage_name is None:
            non_default = [name for name in processed_phases if name != default_stage_name]
            target_stage_name = non_default[-1] if non_default else default_stage_name
        if target_stage_name not in processed_phases:
            processed_phases[target_stage_name] = ""

        stage_content = processed_phases.get(target_stage_name)
        if isinstance(stage_content, list):
            stage_list = list(stage_content)
        else:
            stage_list = []
            if stage_content:
                stage_list.append({"type": "text", "content": stage_content})
        stage_list.extend(action_call_entries)
        processed_phases[target_stage_name] = stage_list

    # Determine final status
    error_termination_reasons = {
        "empty_model_response",
        "action_batch_exceeds_action_limit",
        "action_budget_exhausted",
        "tool_schema_error_exhausted",
        "provider_request_exhausted",
        "output_token_limit",
        "parallel_tool_call_contract_violation",
        "max_turns",
    }
    status = (
        "error"
        if loop_result.termination_reason in error_termination_reasons
        else "success"
    )
    if parsing_errors and status != "error":
        status = "partial_success" if processed_phases else "error"

    incomplete_termination_reasons = {
        "action_budget_exhausted",
        "max_turns",
        "output_token_limit",
        "parallel_tool_call_contract_violation",
    }
    return LoopResult(
        status=status,
        phases=processed_phases,
        phases_unknown=phases_unknown,
        full_history=full_history,
        conversation_messages=copy.deepcopy(messages),
        parsing_errors=parsing_errors,
        total_turns=total_turns,
        default_stage_name=default_stage_name,
        action_calls=action_call_entries,
        termination_reason=loop_result.termination_reason,
        termination_action=loop_result.termination_action,
        error=loop_result.error,
        failure_class=loop_result.failure_class,
        retry_scope=loop_result.retry_scope,
        retry_attempts=loop_result.retry_attempts,
        activation_status=(
            "incomplete"
            if loop_result.termination_reason in incomplete_termination_reasons
            else "completed"
        ),
        reasoning_content=loop_result.reasoning_content,
        thinking_process=loop_result.thinking_process,
        has_reasoning=loop_result.has_reasoning,
        model_type=loop_result.model_type
    )

# Backward compatibility aliases
# ToolCall = ActionCall
# ToolSet = ActionSet
# execute_tool_call_loop = execute_action_loop
