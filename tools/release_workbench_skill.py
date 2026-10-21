#!/usr/bin/env python3
"""把 Core 仓的 society0-workbench Skill 单向发布到 Skill Library。

用法：
  python3 tools/release_workbench_skill.py
  python3 tools/release_workbench_skill.py --target ~/.agents/skill-library/ui/society0-workbench
"""
import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "skills" / "society0-workbench"
DEFAULT_TARGET = Path.home() / ".agents" / "skill-library" / "ui" / "society0-workbench"


def git_commit(path):
    try:
        return subprocess.check_output(["git", "-C", str(path), "rev-parse", "HEAD"], text=True).strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return "unknown"


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--target", default=str(DEFAULT_TARGET), help="安装目录")
    args = parser.parse_args()

    if not (SOURCE / "SKILL.md").exists():
        raise SystemExit(f"缺少 Skill 入口：{SOURCE / 'SKILL.md'}")
    target = Path(args.target).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(SOURCE, target)

    examples = sorted(path.name for path in (target / "assets" / "examples").iterdir() if path.is_dir())
    print(f"已安装 {SOURCE} → {target}")
    print(f"核心提交：{git_commit(ROOT)}")
    print(f"示例：{', '.join(examples)}")
    print(f"入口：{target / 'assets' / 'examples' / 'atlas' / 'index.html'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
