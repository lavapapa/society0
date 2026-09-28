"""pytest 插件：真实测试阶段计时，仅保留方法名、次数和秒数。"""
from __future__ import annotations
import functools
import inspect
import json
import os
from pathlib import Path
import time

_started = time.perf_counter()
_rows = []
_current = "collection"


def _wrap(owner, name):
    original = getattr(owner, name)
    label = owner.__name__ + "." + name
    if inspect.iscoroutinefunction(original):
        @functools.wraps(original)
        async def wrapped(*args, **kwargs):
            start = time.perf_counter()
            try:
                return await original(*args, **kwargs)
            finally:
                _rows.append({"test": _current, "stage": label, "seconds": time.perf_counter()-start})
    else:
        @functools.wraps(original)
        def wrapped(*args, **kwargs):
            start = time.perf_counter()
            try:
                return original(*args, **kwargs)
            finally:
                _rows.append({"test": _current, "stage": label, "seconds": time.perf_counter()-start})
    setattr(owner, name, wrapped)


def pytest_configure(config):
    from society0.society import Society0
    from society0.persistence import PersistenceManager
    from society0.resource_managers import LLMManager, EmbeddingManager
    for owner, names in [
        (Society0, ["__init__", "run", "_initialize", "_save_summary", "_close_model_managers", "_summarize_output_files", "_summarize_events", "_summarize_agent_operations", "_summarize_resource_calls"]),
        (PersistenceManager, ["__init__", "_create_chroma_client", "_sync_chroma_to_store", "publish_root", "close"]),
        (LLMManager, ["request", "close"]), (EmbeddingManager, ["request", "close"]),
    ]:
        for name in names:
            _wrap(owner, name)


def pytest_runtest_setup(item):
    global _current
    _current = item.nodeid


def pytest_runtest_logreport(report):
    _rows.append({"test": report.nodeid, "stage": "pytest."+report.when,
                  "seconds": report.duration, "outcome": report.outcome})


def pytest_sessionfinish(session, exitstatus):
    output = os.environ.get("SOCIETY0_TEST_TIMING_FILE")
    if output:
        Path(output).write_text(json.dumps({"wall_seconds": time.perf_counter()-_started,
            "exitstatus": exitstatus, "timings": _rows}, indent=2))
