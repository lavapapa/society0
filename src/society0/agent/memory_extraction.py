"""Thread-native episodic memory extraction.

Memory extraction is one additional Agent turn appended to the Agent's own
conversation.  The runtime never substitutes a caller-prepared summary for
that conversation and never manufactures fallback memory when extraction
fails or the Agent deliberately returns an empty list.
"""

from __future__ import annotations

import copy
from typing import Any, Awaitable, Callable, Dict, List, Optional



from ..memory_extraction_protocol import (
    _MAX_EXTRACTION_OUTPUT_TOKENS, _MAX_EXTRACTION_RETRY_OUTPUT_TOKENS,
    EXTRACT_MEMORIES_SCHEMA, _extraction_tool, _extraction_prompt,
    _parse_memories_from_response,
)


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
            parsed, tool_call_id, parse_error = _parse_memories_from_response(
                response
            )
            if parsed is not None:
                messages.append(
                    {
                        key: copy.deepcopy(value)
                        for key, value in response.items()
                        if key != "finish_reason"
                    }
                )
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
                        f"你刚才的工具调用未通过校验（{last_error}）。"
                        "现在重新调用 extract_memories 工具。memories 的值必须"
                        "直接是 JSON 数组，不能把数组再次编码成字符串；每条记忆"
                        "必须用不超过 500 字的自然语言概括，不要逐字复制带引号、"
                        "转义符、JSON、表格或重复空白的原文。最多返回 5 条；没有"
                        "值得保留的记忆时，返回空 memories 数组。"
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
