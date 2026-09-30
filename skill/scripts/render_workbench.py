"""把实验转换后的 JSON 数据嵌入 Society0 单文件工作台。"""

import argparse
import json
from pathlib import Path


MARKER = '<script id="society0-workbench-data" type="application/json">'
DEFAULT_TEMPLATE = Path(__file__).resolve().parents[1] / "assets" / "workbench-template.html"


def render(template: str, payload: dict) -> str:
    if not isinstance(payload, dict):
        raise ValueError("转换结果必须是 JSON 对象")
    required = {"study", "versions"}
    missing = required - payload.keys()
    if missing:
        raise ValueError(f"转换结果缺少字段：{', '.join(sorted(missing))}")
    if not isinstance(payload["versions"], list):
        raise ValueError("versions 必须是数组")
    version_ids = [version.get("id") for version in payload["versions"] if isinstance(version, dict)]
    if any(not isinstance(version_id, str) or not version_id for version_id in version_ids):
        raise ValueError("配置版本 id 必须是非空字符串")
    if len(version_ids) != len(set(version_ids)):
        raise ValueError("配置版本 id 必须唯一")
    for version in payload["versions"]:
        if not isinstance(version, dict) or not {"id", "config", "entities", "runs"} <= version.keys():
            raise ValueError("每个配置版本需要 id、config、entities 和 runs")
        if not isinstance(version["config"], dict) or not all(isinstance(version[key], list) for key in ("entities", "runs")):
            raise ValueError("配置版本的 config 必须是对象，entities 和 runs 必须是数组")
        for run in version["runs"]:
            if not isinstance(run, dict) or not {"id", "ticks", "snapshots"} <= run.keys():
                raise ValueError("每次试运行需要 id、ticks 和 snapshots")
            if not isinstance(run["ticks"], list) or not isinstance(run["snapshots"], list):
                raise ValueError("试运行的 ticks 和 snapshots 必须是数组")
    try:
        start = template.index(MARKER) + len(MARKER)
        end = template.index("</script>", start)
    except ValueError as error:
        raise ValueError("模板中找不到 society0-workbench-data 数据块") from error
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c")
    return f"{template[:start]}\n{encoded}\n{template[end:]}"


def main() -> None:
    parser = argparse.ArgumentParser(description="把转换后的 Society0 实验数据写入静态工作台")
    parser.add_argument("--data", required=True, type=Path, help="实验转换脚本生成的 JSON 文件")
    parser.add_argument("--output", required=True, type=Path, help="输出的 HTML 文件")
    parser.add_argument("--template", type=Path, default=DEFAULT_TEMPLATE, help="可选的自定义 HTML 模板")
    args = parser.parse_args()
    try:
        payload = json.loads(args.data.read_text(encoding="utf-8"))
        html = render(args.template.read_text(encoding="utf-8"), payload)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(html, encoding="utf-8")
    except (OSError, ValueError, TypeError) as error:
        parser.exit(1, f"生成工作台失败：{error}\n")


if __name__ == "__main__":
    main()
