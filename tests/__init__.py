"""Society0 测试包。"""
import gzip
import json
from pathlib import Path
from typing import Any


def read_gzip_json(path: Path) -> Any:
    """Read a gzip-compressed JSON test artifact."""
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        return json.load(handle)
