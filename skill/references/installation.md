# 安装与环境验收

首次使用、缺少环境或修复依赖时读取本页。已有通过验收的环境直接复用，在正式实验 Step2 核对配置即可。正式实验仍按 Step1 两轮并行搜索与专业设计选择、Step2 配置、Step3 自动构建运行推进；安装验收使用独立目录。

## 一、定位环境

先确认用户选定的设备、现有 Next 仓库、解释器与本地研究目录。以下核对命令用于已有环境；缺少源码或解释器时先到第二节准备。命令使用 macOS/Linux 的 Bash；将路径替换为该设备已有的目录，保留引号。Windows 可在 Linux 环境中准备运行。Python 3.12 及以上是基础要求。

```sh
export SOCIETY0_REPO="/absolute/path/to/society0"
export SOCIETY0_PYTHON="/absolute/path/to/existing/venv/bin/python"
export SOCIETY0_STUDY="/absolute/path/to/local/study"
"$SOCIETY0_PYTHON" --version
git -C "$SOCIETY0_REPO" branch --show-current
git -C "$SOCIETY0_REPO" rev-parse HEAD
git -C "$SOCIETY0_REPO" status --short
"$SOCIETY0_PYTHON" -c 'import sys, society0; print(sys.executable); print(society0.__file__)'
mkdir -p "$SOCIETY0_STUDY"
df -h "$SOCIETY0_STUDY"
```

核对源码来自 Next 或其任务分支；仓库默认分支属于旧稳定线。解释器的包路径必须对应选定源码。未提交改动须保存实际源码副本，提交号无法描述它们。确认本地文件系统可写，预留运行数据库、WAL、完整点、Thread、向量及导出工件的空间；运行中 SQLite/WAL 使用本地磁盘，网络盘用于封存后的传输。磁盘容量按 pilot 实测增长估算。

独立安装的 skill 保留完整目录结构；本页链接在 skill 内解析。仓库资料通过已克隆仓库定位：`$SOCIETY0_REPO/docs/core-next/installation.md` 的“安装范围／构建与使用”、`$SOCIETY0_REPO/pyproject.toml` 的依赖组，以及 `$SOCIETY0_REPO/docs/core-next/runner-contract.md` 的 `run_plan` 合同。让文件工具展开环境变量为绝对路径后读取这些资料。

## 二、按需安装

已有环境先做第三节验收。首次缺少源码时执行以下命令；目标目录应尚不存在。这里明确选择 `next`，随后记录实际提交，后续复现使用该提交。

```sh
# 先将 SOCIETY0_REPO 设为本机选定的新克隆位置。
git clone --branch next --single-branch https://github.com/lavapapa/society0.git "$SOCIETY0_REPO"
export SOCIETY0_RELEASE="$(git -C "$SOCIETY0_REPO" rev-parse HEAD)"
```

需要新 Python 环境时，在已克隆仓库中执行基础安装；先准备 Git、[uv](https://docs.astral.sh/uv/getting-started/installation/) 及 Python 3.12+。`--frozen` 使用仓库锁文件。以下操作实际安装依赖，应取得本次安装授权后执行。

```sh
(cd "$SOCIETY0_REPO" && uv sync --frozen)
export SOCIETY0_PYTHON="$SOCIETY0_REPO/.venv/bin/python"
```

按研究所需选一条同步命令，每次同步列全需要保留的 extras。LLM + 记忆 starter 需要 `llm` 和 `memory`；纯规则验收使用基础安装即可。

```sh
# LLM + 记忆
(cd "$SOCIETY0_REPO" && uv sync --frozen --extra llm --extra memory)
# SNS 研究再加入 social
(cd "$SOCIETY0_REPO" && uv sync --frozen --extra llm --extra memory --extra social)
# 有 shell 或 HTTP 观察服务需求时，再给对应同步命令加入 --extra shell / --extra observe。
```

`llm`/`shell` 的本地原生文件组件通过仓库 `tool.uv.sources` 构建；源码构建的 Rust 与平台工具链要求见上述仓库安装文档。已有兼容 wheel 时按该文档的 wheel 路径安装。基础规则环境无需 Rust。其他模型提供方按需加 `anthropic` 或 `google`；避免默认安装所有 extras。

复用共享解释器或其他工作树的环境时，先检查依赖可用性；不要直接同步或重装共享环境。测试当前工作树可用 `PYTHONPATH="$SOCIETY0_REPO/src" "$SOCIETY0_PYTHON" ...`，并检查实际导入路径。依赖缺失时在用户同意的专用环境中安装所需组。

## 三、离线验收

以下完整命令在已克隆仓库根目录运行，创建零请求初始化、两主体规则运行及完整点恢复。每次验收使用新的 `SOCIETY0_STUDY` 目录；已有输出目录保持原样。该过程不需要模型凭据。

<!-- offline-recovery-start -->
```sh
cd "$SOCIETY0_REPO"
export SOCIETY0_RELEASE="${SOCIETY0_RELEASE:-$(git rev-parse HEAD)}"
mkdir -p "$SOCIETY0_STUDY/environment-check"
"$SOCIETY0_PYTHON" - <<'PY'
import json
import os
from pathlib import Path
root = Path(os.environ['SOCIETY0_STUDY']) / 'environment-check'
for name, end in [('check', 0), ('first', 1), ('remaining', 2)]:
    config = {'release': {'commit': os.environ['SOCIETY0_RELEASE']}, 'start': 1, 'end': end}
    with (root / (name + '.json')).open('x') as stream:
        json.dump(config, stream)
PY
"$SOCIETY0_PYTHON" -m society0.kernel.runner --factory examples.core_next.rule_run:build \
  --config "$SOCIETY0_STUDY/environment-check/check.json" --output "$SOCIETY0_STUDY/environment-check/check"
"$SOCIETY0_PYTHON" -m society0.kernel.runner --factory examples.core_next.rule_run:build \
  --config "$SOCIETY0_STUDY/environment-check/first.json" --output "$SOCIETY0_STUDY/environment-check/first"
"$SOCIETY0_PYTHON" -m society0.kernel.runner --factory examples.core_next.rule_run:build \
  --config "$SOCIETY0_STUDY/environment-check/remaining.json" \
  --source "$SOCIETY0_STUDY/environment-check/first" --step 1 \
  --output "$SOCIETY0_STUDY/environment-check/resumed"
"$SOCIETY0_PYTHON" - <<'PY'
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from society0.kernel.observation import Observation
from society0.kernel.storage import StageStore
root = Path(os.environ['SOCIETY0_STUDY']) / 'environment-check'
for name, step in [('check', 0), ('first', 1), ('resumed', 2)]:
    with Observation(root / name) as reader:
        assert reader.status()['complete']['step'] == step
        print(name, 'complete_step', step)
with TemporaryDirectory(dir=root) as temporary:
    view = Path(temporary) / 'complete'
    with StageStore.prepare_readonly(root / 'resumed', view, step=2):
        pass
    with Observation(view) as reader:
        phase = reader.result_phases(step=2)['items'][0]
        header = reader.result_page(phase['reference'])['items'][0]['value']
        rows = reader.result_page(header['tables']['decisions'])['items']
        decisions = [row['value'] for row in rows]
        assert decisions == [{'actor': 'alice', 'time': 2}, {'actor': 'bob', 'time': 2}]
        print('restored decisions', decisions)
PY
```
<!-- offline-recovery-end -->

验收应显示完整步骤 0、1、2，恢复后的 Alice、Bob 时间均为 2，原运行仍停在 1。恢复配置保留完整时间序列，来源与新输出目录分开。真实故障时先通过 Observation 与原始 Thread 核对可信完整点，再显式指定 `--source` 与 `--step`；未完成步骤的诊断留在原目录。

## 四、留证与交接

将无密钥环境记录写入研究目录：操作系统与架构、解释器绝对路径及版本、源码路径/提交/脏状态和源码副本位置、实际包导入路径、锁文件位置、已装依赖版本、本地磁盘位置、上述命令/退出码/完整步骤及待解决问题。模型配置记录端点用途、模型 ID、参数、维度、预算、并发与凭据环境变量名称；凭据值及含凭据的 URL、请求头和环境转储保持私有。可执行 `"$SOCIETY0_PYTHON" -m pip list --format=json`（有 pip 时）或 `uv pip list --python "$SOCIETY0_PYTHON" --format=json` 取得版本清单，发布前检查本地来源信息。

环境验收成功后进入 [运行起步](runtime-quickstart.md)，按已选择的设计配置模型并核对真实试跑授权。已有批准预算直接复用；缺少配置或授权时记录具体待补项及“真实端点试跑未执行”，保留离线验收结果。正式使用开始另按主 Skill 接入 society0-workbench。
