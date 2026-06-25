"""Role-based memory extraction utilities.

This module implements a focused, single-action memory extraction flow that
reuses the action loop conversation context, forces an `extract_memories`
tool call when needed, and returns a simplified schema suitable for
vectorization: [{"content": str, "importance": int}].

"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Callable, Awaitable
import json_repair


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


def _build_interaction_summary(loop_result) -> str:
    """Construct a concise, ordered description of the full interaction."""
    lines: List[str] = []
    for turn in loop_result.full_history or []:
        turn_no = turn.get("turn")
        resp = turn.get("response", {}) or {}
        content = resp.get("content")
        if content:
            lines.append(f"Turn {turn_no} - 我说：{content}")

        for action_item in turn.get("action_results", []) or []:
            name = action_item.get("action_name")
            args = action_item.get("arguments")
            result = action_item.get("result")
            lines.append(f"Turn {turn_no} - 我调用 {name}，参数: {args}，结果: {result}")

    return "\n".join(lines) if lines else "无可用交互记录"


def build_interaction_summary(loop_result) -> str:
    """Public wrapper for interaction summary used by fallback paths."""
    return _build_interaction_summary(loop_result)


def _build_extraction_prompt(loop_result) -> str:
    summary = _build_interaction_summary(loop_result)
    return (
        "[记忆提取任务]\n"
        "请以第一人称回顾刚刚的经历，提取值得记住的关键事项。\n"
        "要求：\n"
        "1. 记忆片段要简洁明了，每条控制在20-50字以内\n"
        "2. 专注于核心行动、重要发现或情感体验\n"
        "3. 避免冗长的描述和重复信息\n"
        "4. 用第一人称表达，像写日记摘要\n"
        "输出时只需调用 extract_memories 工具。\n\n"
        "[完整过程回顾]\n"
        f"{summary}"
    )


def _parse_memories_from_tool_call(tool_call: Dict[str, Any]) -> Optional[List[Dict[str, Any]]]:
    args_raw = tool_call.get("function", {}).get("arguments")
    try:
        args = json_repair.loads(args_raw or "{}")
    except Exception:
        return None

    memories = args.get("memories") if isinstance(args, dict) else None
    if not isinstance(memories, list):
        return None

    cleaned: List[Dict[str, Any]] = []
    for item in memories:
        if not isinstance(item, dict):
            continue
        content = item.get("content")
        importance = item.get("importance")
        if isinstance(content, str) and (isinstance(importance, (int, float))):
            cleaned.append({"content": content, "importance": float(importance)})

    return cleaned if cleaned else None


async def perform_memory_extraction(
    loop_result,
    llm_call: Callable[[Dict[str, Any]], Awaitable[Dict[str, Any]]],
) -> Dict[str, Any]:
    """Run the two-pass extraction flow and return parsed memories."""
    if not loop_result.full_history:
        return {"success": False, "memories": [], "error": "no_history"}

    last_turn = loop_result.full_history[-1]
    base_messages = list(last_turn.get("request", {}).get("messages", []))
    base_messages += last_turn.get("tool_messages", []) or []

    actionset = {
        "type": "function",
        "function": {
            "name": "extract_memories",
            "description": "提取个人记忆，返回 memories 数组，每项包含 content (第一人称描述) 和 importance (0-5).",
            "parameters": EXTRACT_MEMORIES_SCHEMA,
        },
    }

    prompt = _build_extraction_prompt(loop_result)
    initial_payload = {
        "messages": base_messages + [{"role": "user", "content": prompt}],
        "tools": [actionset],
        "tool_choice": "auto",
        "metadata": {
            "interaction_type": "memory_extract",
            "interaction_name": "memory_extract",
        },
    }

    # First attempt
    try:
        response = await llm_call(initial_payload)
        for tool_call in response.get("tool_calls", []) or []:
            if tool_call.get("function", {}).get("name") == "extract_memories":
                parsed = _parse_memories_from_tool_call(tool_call)
                if parsed:
                    return {"success": True, "memories": parsed, "error": None}
    except Exception as exc:
        first_error = str(exc)
    else:
        first_error = "no_tool_call"

    # Second attempt: force tool choice with explicit reminder
    force_payload = {
        "messages": initial_payload["messages"]
        + [
            {
                "role": "user",
                "content": "请务必调用 extract_memories 工具，并返回 memories 数组。不要输出其他内容。",
            }
        ],
        "tools": [actionset],
        "tool_choice": {"type": "function", "function": {"name": "extract_memories"}},
        "metadata": {
            "interaction_type": "memory_extract",
            "interaction_name": "memory_extract_retry",
        },
    }

    try:
        response = await llm_call(force_payload)
        for tool_call in response.get("tool_calls", []) or []:
            if tool_call.get("function", {}).get("name") == "extract_memories":
                parsed = _parse_memories_from_tool_call(tool_call)
                if parsed:
                    return {"success": True, "memories": parsed, "error": None}
    except Exception as exc:
        return {"success": False, "memories": [], "error": str(exc)}

    return {"success": False, "memories": [], "error": first_error}
