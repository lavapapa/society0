from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import textwrap

import pytest

from society0.incremental_checkpoint import V4CheckpointStore


_CHILD = r"""
import os
import sys
from pathlib import Path

from society0.incremental_checkpoint import PersistenceKind, StateDeltaJournal, V4CheckpointStore

root = Path(sys.argv[1])
mode = sys.argv[2]
store = V4CheckpointStore(root)
journal = StateDeltaJournal({
    ("environment", "state", "counter"): PersistenceKind.REPLACEABLE,
})
journal.begin_tick(1)
journal.record_set(("environment", "state", "counter"), 1)
delta = journal.seal_tick()
original = store._atomic_write

def crash_at_marker(path, payload):
    if path.parent == store.complete_dir and path.name == "step_000001.json":
        if mode == "before":
            os._exit(73)
        written = original(path, payload)
        os._exit(74)
    return original(path, payload)

store._atomic_write = crash_at_marker
store.publish(delta)
"""


def _root_store(path) -> V4CheckpointStore:
    store = V4CheckpointStore(path)
    store.publish_root(
        (
            {
                "path": ["environment", "state", "counter"],
                "operation": "set",
                "value": 0,
                "sequence": 0,
            },
        ),
        metadata={"run_id": "crash-test"},
    )
    return store


@pytest.mark.parametrize(
    ("mode", "returncode", "latest_step", "counter"),
    [("before", 73, 0, 0), ("after", 74, 1, 1)],
)
def test_process_crash_visibility_is_exactly_the_complete_marker_boundary(
    tmp_path,
    mode,
    returncode,
    latest_step,
    counter,
):
    _root_store(tmp_path)
    env = dict(os.environ)
    src = str(Path(__file__).resolve().parents[2] / "src")
    env["PYTHONPATH"] = src

    result = subprocess.run(
        [sys.executable, "-c", textwrap.dedent(_CHILD), str(tmp_path), mode],
        env=env,
        check=False,
    )

    assert result.returncode == returncode
    reopened = V4CheckpointStore(tmp_path)
    assert reopened.resolve()["step"] == latest_step
    assert reopened.restore(latest_step)["environment"]["state"]["counter"] == counter
