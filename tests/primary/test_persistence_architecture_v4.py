"""Checkpoint v4 核心的依赖方向合同。"""

from __future__ import annotations

import ast
from pathlib import Path

import society0


def test_persistence_core_does_not_import_builtin_or_external_environments():
    package_root = Path(society0.__file__).resolve().parent
    core_modules = (
        "state_persistence.py",
        "incremental_checkpoint.py",
        "state_proxy.py",
        "persistence.py",
    )
    forbidden: list[tuple[str, str]] = []

    for filename in core_modules:
        tree = ast.parse((package_root / filename).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                imported = [node.module or ""]
            else:
                continue
            for module in imported:
                if (
                    module == "industry_chain"
                    or module.startswith("industry_chain.")
                    or module.startswith("society0.env.")
                    or module.startswith("env.")
                ):
                    forbidden.append((filename, module))

    assert forbidden == []
