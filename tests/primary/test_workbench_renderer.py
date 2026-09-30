import json
import re
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "skill" / "scripts" / "render_workbench.py"


def test_render_workbench_embeds_transformed_data(tmp_path):
    payload = {
        "study": {"title": "研究 </script>", "question": "如何解释变化？"},
        "versions": [{"id": "v1", "name": "版本 1", "config": {}, "entities": [], "runs": []}],
    }
    data_path = tmp_path / "workbench-data.json"
    output_path = tmp_path / "workbench.html"
    data_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--data", str(data_path), "--output", str(output_path)],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    html = output_path.read_text(encoding="utf-8")
    start = '<script id="society0-workbench-data" type="application/json">'
    block = html.split(start, 1)[1].split("</script>", 1)[0]
    assert json.loads(block) == payload
    assert "\\u003c/script>" in block
    assert not re.search(r"<script\b[^>]*\bsrc=", html)


def test_render_workbench_rejects_unscoped_runs(tmp_path):
    data_path = tmp_path / "workbench-data.json"
    output_path = tmp_path / "workbench.html"
    data_path.write_text(json.dumps({"study": {}, "versions": [{"id": "v1", "config": {}, "entities": [], "runs": [{"id": "pilot"}]}]}), encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--data", str(data_path), "--output", str(output_path)],
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "ticks 和 snapshots" in result.stderr
    assert not output_path.exists()


def test_render_workbench_rejects_duplicate_version_ids(tmp_path):
    data_path = tmp_path / "workbench-data.json"
    output_path = tmp_path / "workbench.html"
    version = {"id": "v1", "config": {}, "entities": [], "runs": []}
    data_path.write_text(json.dumps({"study": {}, "versions": [version, version]}), encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--data", str(data_path), "--output", str(output_path)],
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "id 必须唯一" in result.stderr
