#!/usr/bin/env python3
"""把项目展示资料写入工作台模板，生成单文件 HTML。

用法：
  python3 render_workbench.py --project <project>/workbench
  python3 render_workbench.py --data data.json --template shell.html --output index.html
"""
import argparse
import json
import sys
from pathlib import Path

MARKER = "null"
PARTIAL_TYPES = {"table", "metric", "series", "records", "thread", "document"}
TYPED_KEYS = {
    "table": "rows", "metric": "items", "series": "series", "records": "records",
    "thread": "messages", "document": "paragraphs", "graph": "nodes", "feed": "posts",
    "events": "events", "timeline": "lanes", "gantt": "tasks", "board": "columns",
    "flow": "steps", "map": "nodes", "track": "tracks", "od": "rows",
    "diffusion": "states", "compare": "worlds", "bars": "bars",
    "distribution": "groups", "heat": "rows", "orderbook": "bids", "matrix": "rows",
    "strategy": "rounds", "forecast": "series", "diagnostics": "runs",
    "artifacts": "files", "candles": "bars", "timemap": "rows", "checkpoints": "checkpoints",
}


def diagnose(data):
    """返回阻断错误与提示；错误定位到具体面板或数据集。"""
    errors, warnings = [], []
    datasets = {item.get("id"): item for item in data.get("datasets") or [] if isinstance(item, dict)}
    panels = data.get("panels") or (data.get("workspace") or {}).get("panels") or []
    if not panels:
        warnings.append("panels 为空：页面只会显示实验设置，没有观察页。设计阶段可以只有 document 与 table。")
    for panel in panels:
        pid = panel.get("id") or "(无 id)"
        dataset_id = panel.get("datasetId")
        kind = panel.get("type")
        if not dataset_id:
            errors.append(f"面板 {pid} 缺少 datasetId")
            continue
        dataset = datasets.get(dataset_id)
        if dataset is None:
            errors.append(f"面板 {pid} 引用了不存在的 datasetId {dataset_id}")
            continue
        key = TYPED_KEYS.get(kind)
        if key and dataset.get("availability") not in (None, "unconfigured", "awaiting", "empty", "error", "loading"):
            if not dataset.get(key):
                errors.append(f"面板 {pid}（{kind}）的数据集 {dataset_id} 缺少 {key}")
    return errors, warnings


def load_data(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise SystemExit(f"资料文件不是合法 JSON：{path}\n{error}") from error


def embed(template, data):
    tag = '<script id="wb-data" type="application/json">'
    start = template.find(tag)
    if start < 0:
        raise SystemExit("模板缺少 #wb-data 节点，无法写入资料")
    body_start = start + len(tag)
    end = template.find("</script>", body_start)
    if end < 0:
        raise SystemExit("模板的 #wb-data 节点没有结束标签")
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    return template[:body_start] + payload + template[end:]


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--project", help="项目工作台目录，含 data.json 与 index.html")
    parser.add_argument("--data", help="资料文件路径")
    parser.add_argument("--template", help="模板 HTML 路径")
    parser.add_argument("--output", help="输出路径")
    args = parser.parse_args()

    if args.project:
        project = Path(args.project)
        data_path = Path(args.data) if args.data else project / "data.json"
        output = Path(args.output) if args.output else project / "index.html"
        if args.template:
            template_path = Path(args.template)
        else:
            manifest = project / "project.json"
            declared = json.loads(manifest.read_text(encoding="utf-8")).get("template") if manifest.exists() else None
            template_path = (project / declared).resolve() if declared else project / "template.html"
    else:
        if not (args.data and args.template and args.output):
            parser.error("需要 --project，或同时提供 --data、--template、--output")
        data_path, template_path, output = Path(args.data), Path(args.template), Path(args.output)

    if not data_path.exists():
        raise SystemExit(f"缺少展示资料：{data_path}")
    if not template_path.exists():
        raise SystemExit(f"缺少模板：{template_path}")

    data = load_data(data_path)
    errors, warnings = diagnose(data)
    for warning in warnings:
        print(f"提示：{warning}")
    if errors:
        for error in errors:
            print(f"错误：{error}")
        raise SystemExit(f"资料未通过合同检查，共 {len(errors)} 处；详见 references/data-contract.md")
    template = template_path.read_text(encoding="utf-8")
    result = embed(template, data)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(result, encoding="utf-8")

    study = data.get("study", {})
    print(f"已生成 {output}")
    print(f"资料：{data_path}（dataKind={data.get('dataKind', '未知')}，revision={study.get('dataRevision') or '未提供'}）")
    print("刷新浏览器即可看到最新资料。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
