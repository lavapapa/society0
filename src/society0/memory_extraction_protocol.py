"""两代引擎共用的记忆提取提示、工具声明与解析规则。"""
from __future__ import annotations
import math
from jsonschema import Draft202012Validator
from typing import Any, Dict, List, Optional
import json_repair

_MAX_EXTRACTION_OUTPUT_TOKENS = 4_096
_MAX_EXTRACTION_RETRY_OUTPUT_TOKENS = 4_096


EXTRACT_MEMORIES_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "memories": {
            "type": "array",
            "maxItems": 5,
            "items": {
                "type": "object",
                "properties": {
                    "content": {"type": "string", "minLength": 1, "maxLength": 500},
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
        "最多保留 5 条，每条不超过 500 字；用简洁的自然语言概括，"
        "不要复制原始 JSON、表格、重复空白或大段带转义符的文本。"
        "如果 Thread 明确要求你记住某项信息供后续互动使用，必须保留该信息。"
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
    errors=list(Draft202012Validator(EXTRACT_MEMORIES_SCHEMA).iter_errors(arguments))
    if errors:
        error=errors[0]
        path=list(error.absolute_path)
        if not path or path[0]!='memories': code='invalid_tool_arguments'
        elif len(path)>=3 and path[2]=='content': code='invalid_memory_content'
        elif len(path)>=3 and path[2]=='importance': code='invalid_memory_importance'
        elif len(path)>=2: code='invalid_memory_item'
        else: code='invalid_tool_arguments'
        return None,None,code
    cleaned=[]
    for item in arguments['memories']:
        if not item['content'].strip(): return None,None,'invalid_memory_content'
        if not math.isfinite(float(item['importance'])): return None,None,'invalid_memory_importance'
        cleaned.append({'content':item['content'].strip(),'importance':float(item['importance'])})
    return cleaned, str(tool_call.get("id") or ""), ""
