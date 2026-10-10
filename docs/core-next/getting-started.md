# 第一次运行与恢复

这个例子让 Alice 和 Bob 在两个业务时点各作一次规则决定，保存主体与时间。先完成第一个时点，再从完整步骤建立新运行继续第二个时点；全程使用基础依赖。模型行为和领域机制可在这条运行链之后加入。

## 一、准备

在 macOS 或 Linux 上准备 Git、[uv](https://docs.astral.sh/uv/getting-started/installation/) 和 Python 3.12 及以上。已有源码工作树时直接进入它；首次取得源码时，选择本次要使用的分支或标签：

```sh
git clone --branch next https://github.com/lavapapa/society0.git
cd society0
git switch --detach YOUR_SOURCE_REF
uv sync
source .venv/bin/activate
export SOCIETY0_SOURCE_COMMIT="$(git rev-parse HEAD)"
git status --short
```

将 `YOUR_SOURCE_REF` 替换为包含本教程的源码身份，候选与发行状态见 [验收清单](TODO.md)。已有工作树从 `uv sync` 继续；正式研究使用已提交的干净源码及其锁文件。未提交试验应另存实际源码，提交号本身无法描述这些改动。下面命令均在仓库根目录、已激活的环境内执行，每次尝试选择新的输出目录。

需要助手带领设计研究时，将仓库的整个 `skill/` 目录安装到所用助手的技能目录，并保留 `SKILL.md`、`references/`、`assets/` 与 `scripts/` 的相对关系。技能提供指导和模板；上面的 Python 安装提供执行引擎。可选能力及平台构建见 [安装说明](installation.md)。

## 二、运行

公开配置明确本次要执行的业务时间范围。先为首段运行和恢复后的完整时间范围各写一份配置：

```sh
mkdir -p runs/first-study
python - <<'PY'
import json
import os
from pathlib import Path

for name, start, end in [('first', 1, 1), ('remaining', 1, 2)]:
    config = {'release': {'commit': os.environ['SOCIETY0_SOURCE_COMMIT']},
              'start': start, 'end': end}
    Path(f'runs/first-study/{name}.json').write_text(json.dumps(config))
PY
python -m society0.kernel.runner \
  --factory examples.core_next.rule_run:build \
  --config runs/first-study/first.json \
  --output runs/first-study/first
python -m society0.kernel.observation runs/first-study/first
```

runner 的成功结果包含 `status: completed` 与 `complete_step: 1`。Observation 的 `complete.step` 同为 1，表示这个完整步骤可用于恢复。输出目录的 `runner.json` 保存配置与源码声明，`runner-status.json` 保存进度，`timings.jsonl` 保存计时；结果正文通过结果服务读取。

下面准备第 1 步的只读视图，取得实际指标和两条主体结果。这份小例子的每个结果集都能装入一页；大型结果的分页与正文续读见 [分析指南](../../skill/references/run-monitor-analyze.md)。

```sh
python - <<'PY'
from pathlib import Path
from tempfile import TemporaryDirectory
from society0.kernel.observation import Observation
from society0.kernel.storage import StageStore

with TemporaryDirectory() as temporary:
    view = Path(temporary) / 'complete'
    with StageStore.prepare_readonly('runs/first-study/first', view, step=1):
        pass
    with Observation(view) as reader:
        phase = reader.result_phases(step=1)['items'][0]
        header = reader.result_page(phase['reference'])['items'][0]['value']
        metrics = reader.result_page(header['metrics'])
        decisions = reader.result_page(header['tables']['decisions'])
        print('指标：', [item['value'] for item in metrics['items']])
        print('决定：', [item['value'] for item in decisions['items']])
PY
```

指标是两次决定，决定表依次为 Alice、Bob 的主体名和时间 1。这个例子证明运行、记录与读取接通；具体决定的社会含义由研究者的规则和测量设计提供。

## 三、继续

从原目录的完整第 1 步建立新目录，并在完整时间序列中继续尚未完成的业务时点 2：

```sh
python -m society0.kernel.runner \
  --factory examples.core_next.rule_run:build \
  --config runs/first-study/remaining.json \
  --source runs/first-study/first --step 1 \
  --output runs/first-study/resumed
python -m society0.kernel.observation runs/first-study/resumed
```

新运行的 `complete.step` 为 2，`runner.json` 记录来源完整点与本次时间范围。原目录继续保留第 1 步，新目录包含继承的第 1 步与新执行的第 2 步。runner 自动续步骤编号，SequenceSchedule 按已完成步骤选择完整时间序列中的下一项；本例恢复配置为 `start=1, end=2`，两端均包含。照此分析新目录时，选择第 2 步将看到 Alice、Bob 的时间都是 2。

实际故障时，先从原始事实和 Thread 判断最新可信完整步骤，再按这条路径恢复。进度说明正在做什么，live 数据包括尚未完整的诊断事实，完整描述符确定已发布范围。例如第 2 步失败时，当前目录可能已有它的部分记录，第 1 步仍可作为可信恢复候选。发布成功后若退出报错，运行状态也可能为 failed；应同时核对完整描述符和错误原因。详细读取边界见 [观察合同](observation-contract.md)。

## 四、扩展

接下来阅读 [双机制对话例](../../examples/core_next/conversation_pilot.py)：同一组主体进入 `work` 与 `commons` 两个机制，`pair` 阶段建立关系，`talk` 阶段通过行动发送消息，StepResult 保存配对和消息。用自己的机制替换这条链时，分别决定事实由谁维护、主体看到哪些信息、哪些行动会改变事实，以及结果如何测量。具体接线见 [环境设计](../../skill/references/environment-design.md)。

需要模型决策时使用 [LLM 起步](../../skill/references/runtime-quickstart.md)。该 starter 的记忆设计还需要嵌入服务；无记忆的 LLM 计划按自身所用能力安装与配置。无论驱动如何变化，实际运行都沿本页的配置、完整步骤、结果读取与恢复合同组织。
