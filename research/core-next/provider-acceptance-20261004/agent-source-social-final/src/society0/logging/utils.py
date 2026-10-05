"""日志辅助工具，提供文本摘要与样本截取能力。"""

from __future__ import annotations

from typing import Any, Callable, Dict, Iterable, List, Optional


def summarize_text(value: Any, *, limit: int = 240) -> Dict[str, Any]:
    """对文本进行截断摘要，返回预览/长度等信息。"""

    if value is None:
        text = ""
    elif isinstance(value, str):
        text = value
    else:
        text = str(value)

    normalized = text.strip()
    length = len(normalized)
    if length <= limit:
        return {
            "preview": normalized,
            "length": length,
            "full": normalized,
            "truncated": False,
        }

    preview = normalized[:limit].rstrip()
    return {
        "preview": f"{preview}...",
        "length": length,
        "full": normalized,
        "truncated": True,
    }


def sample_items(
    items: Optional[Iterable[Any]],
    *,
    limit: int = 5,
    transform: Optional[Callable[[Any], str]] = None,
) -> Dict[str, Any]:
    """采样序列中的前若干项，并返回总数与样本列表。"""

    if items is None:
        return {"sample": [], "total": 0}

    transform = transform or (lambda x: str(x))
    sample_list: List[str] = []
    total = 0
    for value in items:
        if total < limit:
            try:
                sample_list.append(transform(value))
            except Exception:
                sample_list.append(str(value))
        total += 1

    return {"sample": sample_list, "total": total}

