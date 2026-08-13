"""Society0 测试包。"""
import gzip
import json
from pathlib import Path
from typing import Any


def read_gzip_json(path: Path) -> Any:
    """Read a gzip-compressed JSON test artifact."""
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        return json.load(handle)


def read_last_v4_checkpoint(run_dir: Path) -> Any:
    """Read the latest recoverable v4 checkpoint from a test run."""
    from society0.incremental_checkpoint import V4CheckpointStore
    from society0.persistence import PersistenceManager

    record = PersistenceManager.resolve_last_complete_from(run_dir)
    return V4CheckpointStore(run_dir).restore(int(record["step"]))
