#!/usr/bin/env python3
"""创建一个最小工作台项目，并生成可立即打开的空壳。

用法：
  python3 create_workbench.py --output-dir <project>/workbench --title "实验名称"
"""
import argparse
import json
import os
import sys
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = SKILL_ROOT / "assets" / "shell.html"


def empty_data(title):
    return {
        "schemaVersion": 1,
        "dataKind": "mock",
        "study": {
            "id": "study-local",
            "title": title,
            "question": "",
            "description": "对话开始后，资料会逐步补充进来。",
            "dataRevision": "0",
            "updatedAt": "",
        },
        "versions": [], "plugins": [], "worlds": [], "runs": [],
        "entities": [], "observations": [], "datasets": [],
        "documents": [], "artifacts": [],
        "workspace": {"pages": ["setup"], "panels": [], "defaultSelection": {}, "timeModes": ["round"]},
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--output-dir", required=True, help="项目工作台目录")
    parser.add_argument("--title", required=True, help="实验标题")
    args = parser.parse_args()

    target = Path(args.output_dir)
    index = target / "index.html"
    if index.exists():
        raise SystemExit(f"目标已存在工作台：{index}\n如需更新资料，请修改 data.json 后运行 render_workbench.py")

    if not TEMPLATE.exists():
        raise SystemExit(f"缺少模板：{TEMPLATE}")

    target.mkdir(parents=True, exist_ok=True)
    data = empty_data(args.title)
    data_path = target / "data.json"
    data_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import render_workbench

    template = TEMPLATE.read_text(encoding="utf-8")
    index.write_text(render_workbench.embed(template, data), encoding="utf-8")

    project = {
        "title": args.title,
        "data": "data.json",
        "template": os.path.relpath(os.path.realpath(TEMPLATE), os.path.realpath(target)),
        "output": "index.html",
    }
    (target / "project.json").write_text(json.dumps(project, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"已创建 {target}")
    print(f"打开：{index}")
    print("后续修改 data.json 后运行 render_workbench.py --project <目录>，再刷新页面。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
