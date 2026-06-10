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

logger = logging.getLogger(__name__)

# Default reasoning stages configuration
DEFAULT_REASONING_STAGES = [
    {"name": "思考", "desc": "思考当前情况，分析信息"},
    {"name": "回答", "desc": "给出回答或执行行动"}
]

# Default act prompt template
DEFAULT_AGENT_ACT_PROMPT = """你的决策过程必须遵循一个由"阶段标记"驱动的线性流程。阶段标记的格式为 `-> STAGE_BEGIN: StageName`。

本次任务的阶段顺序是:
{stages}

- 你的回应必须从第一个阶段开始。在每个阶段标记下，完成该阶段的任务。
- 你可以自行决定何时从一个阶段切换到下一个阶段。
- 你的整个回应应该是一个包含这些标记的、连贯的文本块。
- 你不能重复、跳跃或返回到之前的阶段，必须完全依据给定的阶段顺序。
- 阶段顺序不能被工具调用打断而重置。工具调用属于一个阶段的输出的一部分。"""


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

@dataclass
class ActionCall:
    """Represents an action call with its result."""
    call_id: str
    action_name: str
    arguments: Dict[str, Any]
    result: Any = None

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
                current_stack, update_context = context_provider()

                # Import context management utilities
                from ..context_stack import action_context

                # Execute action within proper context
                with action_context(current_stack, action_name, params=kwargs) as new_stack:
                    # Update the world's context stack for state change tracking
                    update_context(new_stack)

                    try:
                        # Execute the action function
                        result = action_func(**kwargs)

                        # Handle async functions properly
                        import asyncio
                        if asyncio.iscoroutine(result):
                            result = await result

                        return result

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

            # Check inclusion
            if action_tags:
                # If action_tags is provided, only include actions that have at least one matching tag
                if not any(tag in merged_tags for tag in action_tags):
                    continue  # Skip this action
            # If action_tags is None or empty, include all actions (that aren't excluded)

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
    turn_remain_hint: bool = True,
    hint_on_remain_turn: int = 1
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

    Returns:
        LoopResult containing parsed stages, action call results, and execution history
    """

    # Normalize stages to unified dict format
    normalized_stages = normalize_reasoning_stages(stages)

    # Extract stage names for parsing
    stage_names = [stage["name"] for stage in normalized_stages]

    # Format stages for prompt injection
    stages_text = format_stages_for_prompt(normalized_stages)

    # Initialize conversation history
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
        model_type=None
    )

    # Get actions schema for OpenAI format
    actions_schema = action_set.get_openai_actions_schema() if action_set.actions else []
    terminal_action_name_set = {
        str(name).strip().lower()
        for name in (terminal_action_names or [])
        if str(name).strip()
    }


    for turn in range(max_turns):
        total_turns = turn + 1
        print(f"Action loop turn {total_turns}/{max_turns}")

        # Call LLM with current message history and actions
        llm_payload = {
            "messages": messages,
            "tools": actions_schema if actions_schema else None,
            "tool_choice": "auto" if actions_schema else None  # 确保设置了 tool_choice
        }

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
            print(f"🧠 Reasoning Content (Turn {total_turns}): {reasoning_content[:100]}...")
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
            print(f"🔧 Executing Action: {action_call.action_name}")
            if action_call.arguments:
                print(f"   Parameters: {action_call.arguments}")
            is_last_action_in_turn = idx == len(action_calls) - 1

            try:
                # Execute the action call with context management
                action_result = await action_set.call_action(
                    action_call.action_name,
                    context_provider=context_provider,
                    **action_call.arguments
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
                executed_action_calls.append(action_call)

                print(f"✅ Action Result: {str(action_result)[:100]}")

            except Exception as e:
                error_msg = f"Error executing action {action_call.action_name}: {str(e)}"
                print(f"❌ Action Error: {error_msg}")

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
                executed_action_calls.append(action_call)

            if action_call.action_name.lower() in terminal_action_name_set:
                terminate_loop = True
                print(f"🛑 Terminal Action Hit: {action_call.action_name}, ending loop early")
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
                }
                for ac in executed_action_calls
            ]

        if terminate_loop:
            break
        continue

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
        reasoning_content=loop_result.reasoning_content,
        thinking_process=loop_result.thinking_process,
        has_reasoning=loop_result.has_reasoning,
        model_type=loop_result.model_type
    )

# Backward compatibility aliases
# ToolCall = ActionCall
# ToolSet = ActionSet
# execute_tool_call_loop = execute_action_loop
