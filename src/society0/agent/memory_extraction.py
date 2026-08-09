"""Thread-native episodic memory extraction.

Memory extraction is one additional Agent turn appended to the Agent's own
conversation.  The runtime never substitutes a caller-prepared summary for
that conversation and never manufactures fallback memory when extraction
fails or the Agent deliberately returns an empty list.
"""

from __future__ import annotations

import copy
import math
from typing import Any, Awaitable, Callable, Dict, List, Optional

import json_repair


_MAX_EXTRACTION_OUTPUT_TOKENS = 4_096
_MAX_EXTRACTION_RETRY_OUTPUT_TOKENS = 4_096


EXTRACT_MEMORIES_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "memories": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "content": {"type": "string"},
                    "importance": {"type": "number", "minimum": 0, "maximum": 5},
                },
                "required": ["content", "importance"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["memories"],
    "additionalProperties": False,
}


def _extraction_tool() -> Dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "extract_memories",
            "description": (
                "从自己刚刚的完整经历中选择值得长期保留的记忆；"
                "没有值得保留的内容时返回空 memories 数组。"
            ),
            "parameters": EXTRACT_MEMORIES_SCHEMA,
            "strict": True,
        },
    }


def _extraction_prompt() -> str:
    return (
        "请回顾这条 Agent Thread 中你刚刚亲自经历的完整过程，"
        "由你自己判断哪些经验会影响今后的决策。"
        "只调用 extract_memories 工具。每条记忆用第一人称表达，"
        "importance 取 0 到 5。如果没有值得形成长期记忆的内容，"
        "返回 {\"memories\": []}。"
    )


def _parse_memories_from_response(
    response: Dict[str, Any],
) -> tuple[Optional[List[Dict[str, Any]]], Optional[str], str]:
    tool_calls = response.get("tool_calls") or []
    if not isinstance(tool_calls, list) or not tool_calls:
        return None, None, "no_tool_call"
    if len(tool_calls) != 1:
        return None, None, "multiple_tool_calls"

    tool_call = tool_calls[0]
    if not isinstance(tool_call, dict):
        return None, None, "invalid_tool_call"
    function = tool_call.get("function")
    if not isinstance(function, dict) or function.get("name") != "extract_memories":
        return None, None, "unexpected_tool_call"

    try:
        arguments = json_repair.loads(function.get("arguments") or "{}")
    except Exception:
        return None, None, "invalid_tool_arguments"
    if not isinstance(arguments, dict) or set(arguments) != {"memories"}:
        return None, None, "invalid_tool_arguments"
    memories = arguments.get("memories")
    if not isinstance(memories, list):
        return None, None, "invalid_tool_arguments"

    cleaned: List[Dict[str, Any]] = []
    for item in memories:
        if not isinstance(item, dict) or set(item) != {"content", "importance"}:
            return None, None, "invalid_memory_item"
        content = item.get("content")
        importance = item.get("importance")
        if not isinstance(content, str) or not content.strip():
            return None, None, "invalid_memory_content"
        if (
            isinstance(importance, bool)
            or not isinstance(importance, (int, float))
            or not math.isfinite(float(importance))
            or not 0 <= float(importance) <= 5
        ):
            return None, None, "invalid_memory_importance"
        cleaned.append(
            {
                "content": content.strip(),
                "importance": float(importance),
            }
        )
    return cleaned, str(tool_call.get("id") or ""), ""


async def extract_memories_from_thread(
    *,
    conversation_messages: List[Dict[str, Any]],
    llm_call: Callable[[Dict[str, Any]], Awaitable[Dict[str, Any]]],
    thread_id: str,
    metadata: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """Append one memory turn to an existing Agent conversation.

    One protocol-recovery turn is allowed.  That recovery continues the same
    message sequence; it does not replace the Agent persona or original task
    with a separate extractor context.
    """

    if not isinstance(conversation_messages, list) or not conversation_messages:
        raise ValueError("conversation_messages must be a non-empty list")
    if not isinstance(conversation_messages[0], dict) or conversation_messages[0].get(
        "role"
    ) != "system":
        raise ValueError("conversation_messages must start with a system message")
    normalized_thread_id = str(thread_id or "").strip()
    if not normalized_thread_id:
        raise ValueError("thread_id must be a non-empty string")
    if not callable(llm_call):
        raise TypeError("llm_call must be callable")

    messages = copy.deepcopy(conversation_messages)
    messages.append({"role": "user", "content": _extraction_prompt()})
    tool = _extraction_tool()
    history: List[Dict[str, Any]] = []
    base_metadata = dict(metadata or {})
    base_metadata.update(
        {
            "thread_id": normalized_thread_id,
            "interaction_type": "memory_extract",
        }
    )

    last_error = "no_tool_call"
    for attempt in range(2):
        payload = {
            "messages": copy.deepcopy(messages),
            "tools": [copy.deepcopy(tool)],
            "tool_choice": {
                "type": "function",
                "function": {"name": "extract_memories"},
            },
            "max_tokens": (
                _MAX_EXTRACTION_OUTPUT_TOKENS
                if attempt == 0
                else _MAX_EXTRACTION_RETRY_OUTPUT_TOKENS
            ),
            "metadata": {
                **base_metadata,
                "interaction_name": (
                    "memory_extract" if attempt == 0 else "memory_extract_retry"
                ),
                "memory_extraction_attempt": attempt + 1,
            },
        }
        try:
            response = await llm_call(payload)
        except Exception as exc:
            history.append(
                {
                    "turn": attempt + 1,
                    "request": payload,
                    "response": None,
                    "error": str(exc),
                    "interaction_type": "memory_extract",
                }
            )
            last_error = str(exc)
        else:
            if not isinstance(response, dict):
                response = {"role": "assistant", "content": str(response)}
            history.append(
                {
                    "turn": attempt + 1,
                    "request": payload,
                    "response": copy.deepcopy(response),
                    "interaction_type": "memory_extract",
                }
            )
            messages.append(copy.deepcopy(response))
            parsed, tool_call_id, parse_error = _parse_memories_from_response(
                response
            )
            if parsed is not None:
                return {
                    "success": True,
                    "memories": parsed,
                    "error": None,
                    "tool_call_id": tool_call_id,
                    "conversation_messages": messages,
                    "full_history": history,
                    "thread_id": normalized_thread_id,
                }
            last_error = parse_error

        if attempt == 0:
            messages.append(
                {
                    "role": "user",
                    "content": (
                        "你刚才没有完成记忆提炼。现在只调用 "
                        "extract_memories 工具；没有值得保留的记忆时，"
                        "返回空 memories 数组。"
                    ),
                }
            )

    return {
        "success": False,
        "memories": [],
        "error": last_error,
        "tool_call_id": None,
        "conversation_messages": messages,
        "full_history": history,
        "thread_id": normalized_thread_id,
    }
