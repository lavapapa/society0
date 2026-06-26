"""
Multi-stage ActionLoop engine for flexible LLM Agent cognition.

This module implements a configurable, robust multi-stage thinking engine
that can adapt to different cognitive architectures through stage configuration.
Actions replace the previous tool system with enhanced metadata support.
"""

from typing import List, Dict, Any, Callable, Awaitable, Optional, Union
from dataclasses import dataclass, field
import re
import logging
import json_repair
import time
from collections import Counter

logger = logging.getLogger(__name__)


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
    if isinstance(result, dict):
        explicit_ok = result.get("ok")
        if explicit_ok is False:
            return "error", str(result.get("error") or result.get("message") or "action failed")
        explicit_status = str(result.get("status") or "").strip().lower()
        if explicit_status in {"error", "failed", "failure", "blocked"}:
            return "error", str(result.get("error") or result.get("message") or explicit_status)
        return "success", None

    text = str(result or "").strip()
    lowered = text.lower()
    failure_markers = (
        "error:",
        "错误",
        "失败",
        "不存在",
        "not found",
        "does not exist",
        "cannot ",
        "can't ",
        "invalid ",
        "未找到",
    )
    if lowered.startswith("❌") or any(marker in lowered for marker in failure_markers):
        return "error", text
    return "success", None

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
        tags: List[str] = None
    ):
        """
        Add an action to the actionset with proper OpenAI function calling schema.

        Args:
            name: Action name
            func: Callable function
            description: Action description
            parameters: OpenAI-style parameters schema with type, properties, required
            tags: List of tags for action categorization and filtering
        """
        # Commented out debug prints
        # print(f"--- [DEBUG] Adding action to ActionSet: '{name}' ---")
        # print(f"  Description: {description}")
        # print(f"  Tags: {tags or []}")

        self.actions[name] = {
            "function": func,
            "description": description,
            "parameters": parameters,
            "tags": tags or []
        }

    async def call_action(self, action_name: str, context_provider: Optional[Callable] = None, **kwargs) -> Any:
        """Call an action by name with arguments, handling both sync and async functions and context management.

        Args:
            action_name: Name of the action to call
            context_provider: Function that returns current ContextStack and provides context update capability
            **kwargs: Arguments to pass to the action function
        """
        if action_name not in self.actions:
            raise ValueError(f"Action '{action_name}' not found in actionset")

        try:
            action_func = self.actions[action_name]["function"]

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
            actions_schema.append({
                "type": "function",
                "function": {
                    "name": action_name,
                    "description": action_info["description"],
                    "parameters": action_info["parameters"]
                }
            })
        return actions_schema

@dataclass
class LoopResult:
    """Result of execute_action_loop execution."""
    status: str  # "success", "partial_success", "error"
    phases: Dict[str, Union[str, List[Dict[str, Any]]]] = field(default_factory=dict)
    phases_unknown: Dict[str, Union[str, List[Dict[str, Any]]]] = field(default_factory=dict)
    full_history: List[Dict[str, Any]] = field(default_factory=list)
    parsing_errors: List[str] = field(default_factory=list)
    total_turns: int = 0
    default_stage_name: str = "default"
    action_calls: List[Dict[str, Any]] = field(default_factory=list)
    termination_reason: Optional[str] = None
    termination_action: Optional[str] = None

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
    action_call_limits: Optional[Dict[str, int]] = None,
    llm_request_options: Optional[Dict[str, Any]] = None,
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
    safe_llm_request_options = {
        str(key): value
        for key, value in dict(llm_request_options or {}).items()
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
    system_message = f"{system_prompt}\n\n{flow_section}"
    messages = [
        {
            "role": "system",
            "content": system_message
        },
        {"role": "user", "content": instruction}
    ]

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
    action_call_counts: Counter[str] = Counter()

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

        return "auto"

    def _all_available_action_budgets_exhausted() -> bool:
        """Return true when another LLM turn cannot execute any non-system action."""
        if max_action_calls is not None and sum(action_call_counts.values()) >= max_action_calls:
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
            if action_call_counts[action_key] < limit:
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


    for turn in range(max_turns):
        total_turns = turn + 1
        logger.debug("Action loop turn %s/%s", total_turns, max_turns)

        # Call LLM with current message history and actions
        llm_payload = {
            "messages": messages,
            "tools": actions_schema if actions_schema else None,
            "tool_choice": _default_tool_choice()
        }
        llm_payload.update(safe_llm_request_options)

        # --- 调试点：注释掉旧的调试信息 ---
        # print(f"--- [DEBUG] LLM Payload for Turn {turn + 1} ---")
        # import json
        # print(json.dumps(llm_payload, indent=2, ensure_ascii=False))
        # --- 结束 ---

        response = await llm_call(llm_payload)
        # print(f"[DEBUG] {response}")  # 注释掉详细响应调试
        full_history.append({"turn": total_turns, "request": llm_payload, "response": response})

        # Extract reasoning content, final content and action calls using new function
        reasoning_content, final_content_part, response_metadata = _extract_reasoning_content(response)
        action_calls = response.get("tool_calls", [])

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
        messages.append(response)

        # Execute action calls if present
        if not action_calls:
            missing_names, missing_tags = _missing_loop_requirements()
            if (missing_names or missing_tags) and turn + 1 < max_turns:
                messages.append({"role": "user", "content": _required_action_reminder(missing_names, missing_tags)})
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
        action_calls = [ActionCall(
            call_id=action_call['id'],
            action_name=action_call["function"]["name"],
            arguments=json_repair.loads(action_call["function"]["arguments"] if str(action_call["function"]["arguments"]).strip() else {})
        ) for action_call in action_calls]

        turn_tool_messages = []  # 本轮的工具结果消息，写入 full_history 便于事后重建
        executed_action_calls = []
        terminate_loop = False
        remaining_turns = max_turns - (turn + 1)
        should_hint = turn_remain_hint and remaining_turns <= hint_on_remain_turn

        for idx, action_call in enumerate(action_calls):
            # Action执行监控 - 增强版
            logger.debug("Executing action: %s", action_call.action_name)
            if action_call.arguments:
                logger.debug("Action parameters: %s", action_call.arguments)
            is_last_action_in_turn = idx == len(action_calls) - 1
            action_key = action_call.action_name.lower()
            action_succeeded = False
            limit_error: Optional[str] = None
            if max_action_calls is not None and sum(action_call_counts.values()) >= max_action_calls:
                limit_error = f"Action budget exhausted: max_action_calls={max_action_calls}"
            per_action_limit = _action_limit_for(action_call.action_name)
            if limit_error is None and per_action_limit is not None and action_call_counts[action_key] >= per_action_limit:
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
                messages.append(tool_message)
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
                continue

            try:
                # Execute the action call with context management
                action_started = time.perf_counter()
                try:
                    action_result = await action_set.call_action(
                        action_call.action_name,
                        context_provider=context_provider,
                        **action_call.arguments
                    )
                finally:
                    action_call.duration_sec = round(
                        max(time.perf_counter() - action_started, 0.0),
                        6,
                    )

                # Add action result message to conversation
                base_content = str(action_result)
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
                messages.append(tool_message)
                turn_tool_messages.append({
                    "role": "tool",
                    "content": base_content,
                    "tool_call_id": action_call.call_id
                })
                action_call.result = action_result
                action_call.status, action_call.error = _semantic_action_status(action_result)
                executed_action_calls.append(action_call)
                if action_call.status == "success":
                    action_call_counts[action_key] += 1
                    action_succeeded = True

                logger.debug("Action result: %s", str(action_result)[:100])

            except Exception as e:
                if action_call.duration_sec is None:
                    action_call.duration_sec = 0.0
                error_msg = f"Error executing action {action_call.action_name}: {str(e)}"
                logger.debug("Action error: %s", error_msg)

                base_content = f"Error: {error_msg}"
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
                messages.append(tool_message)
                turn_tool_messages.append({
                    "role": "tool",
                    "content": base_content,
                    "tool_call_id": action_call.call_id
                })
                action_call.result = error_msg
                action_call.status = "error"
                action_call.error = error_msg
                executed_action_calls.append(action_call)

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
    status = "success"
    if parsing_errors:
        status = "partial_success" if processed_phases else "error"

    return LoopResult(
        status=status,
        phases=processed_phases,
        phases_unknown=phases_unknown,
        full_history=full_history,
        parsing_errors=parsing_errors,
        total_turns=total_turns,
        default_stage_name=default_stage_name,
        action_calls=action_call_entries,
        termination_reason=loop_result.termination_reason,
        termination_action=loop_result.termination_action,
        reasoning_content=loop_result.reasoning_content,
        thinking_process=loop_result.thinking_process,
        has_reasoning=loop_result.has_reasoning,
        model_type=loop_result.model_type
    )

# Backward compatibility aliases
# ToolCall = ActionCall
# ToolSet = ActionSet
# execute_tool_call_loop = execute_action_loop
