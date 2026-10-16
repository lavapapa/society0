# 记忆的三个开关

主体保存经验、激活时自动想起经验、主动检索经验，是三种可独立配置的行为。这一课用同一份确定性小例子观察它们，模型响应与嵌入都在本地生成，向量检索使用实际 Chroma。

## 一、运行一次自动写入

先完成[模型驱动教程](llm.md)，并安装记忆依赖：

```bash
uv sync --frozen --extra llm --extra social --extra memory
mkdir -p runs/memory
uv run python - <<'PY'
import json
import subprocess
from pathlib import Path

config = {
    "release": {"commit": subprocess.check_output(
        ["git", "rev-parse", "HEAD"], text=True).strip()},
    "case": "extensions", "variant": "memory-write",
    "provider": "scripted", "start": 1, "end": 1,
}
Path("runs/memory/config.json").write_text(
    json.dumps(config, ensure_ascii=False), encoding="utf-8",
)
PY
uv run python -m society0.kernel.runner \
  --factory examples.core_next.composed_cognition:build \
  --config runs/memory/config.json \
  --output runs/memory/write
```

这个[完整例子](https://github.com/lavapapa/society0/blob/@COMMIT@/examples/core_next/composed_shutdown_extensions.py#L267)在第 1 步先加入种子经验，再让主体完成两次激活。脚本提供方实际调用资料工具和 `probe.record` 行动；自动提取器从每次完整经历产生独立记忆。最终应发布第 1 步。

## 二、核对策略与保存事实

保存以下完整程序为 `inspect_memory.py`。短 SQL 查询读取示例所用的记忆状态表；表结构属于当前[记忆实现](https://github.com/lavapapa/society0/blob/@COMMIT@/src/society0/kernel/memory.py#L19)，用于此处核对，业务操作仍通过 Memory 服务执行。

```python title="inspect_memory.py"
from society0.kernel.storage import StageReader

with StageReader("runs/memory/write") as reader:
    counts = reader.read(lambda view: view.query(
        "SELECT actor,state,count(*) FROM memory_rows GROUP BY actor,state"
    ))
    jobs = reader.read(lambda view: view.query(
        "SELECT actor,state,count(*) FROM memory_jobs GROUP BY actor,state"
    ))
    print("记忆：", counts)
    print("作业：", jobs)
```

```bash
uv run python inspect_memory.py
```

应看到主体 `a` 的已就绪记忆与已完成作业。此场景有一条初始种子和两次自动提取产生的经验。完整正文与提取输入可通过[Thread 观察](observation.md)继续核对；数量检查回答有没有保存，内容核对回答保存了什么。

## 三、只改变一种策略

复制配置为新文件，修改 `variant`，并为各次运行选择不同输出目录。对应关系如下：

| variant | auto_write | auto_recall | active_tools | 应观察的变化 |
| --- | --- | --- | --- | --- |
| `memory-write` | 开 | 关 | 关 | 完成激活后保存新经验 |
| `memory-recall` | 关 | 开 | 关 | 种子记忆进入认知，自动写入保持关闭 |
| `memory-tools` | 关 | 关 | 开 | 脚本主动调用 `memory.recall` 与 `memory.remember` |
| `memory-all` | 开 | 开 | 开 | 自动行为与主动工具同时存在 |

例如新建 `runs/memory/recall.json`，把 variant 改为 `memory-recall`，运行：

```bash
uv run python -m society0.kernel.runner \
  --factory examples.core_next.composed_cognition:build \
  --config runs/memory/recall.json \
  --output runs/memory/recall
```

把读取程序指向新运行，便能比较写入结果。自动召回关闭后，主动工具仍可以检索；主动工具关闭后，自动写入仍可以保存经验。每个开关都对应一种研究条件，应分别记录。

## 四、接入自己的驱动

以下是已有模型计划中的**装配片段**，沿用[研究 starter](https://github.com/lavapapa/society0/blob/@COMMIT@/skill/assets/minimal_experiment.py#L70)自定义的 `cognition.input` 服务，依赖具名模型、嵌入和向量客户端资源。使用[多来源 cognition_plugin](../guide/cognition.md)时，输入服务引用应写为 `("cognition", "input_builder")`：

```python title="记忆与驱动连接片段"
from society0.kernel.memory import MemoryPolicy
from society0.plugins import memory_plugin, llm_driver_plugin

memory = memory_plugin(
    client=("resources", "client"),
    embedding=("resources", "embeddings", "main"),
    extraction=("resources", "models", "main"),
    policy=MemoryPolicy(auto_write=True, auto_recall=True, active_tools=True),
    recall_query=lambda session: "此前看到的消息与判断",
)
driver = llm_driver_plugin(
    provider=("resources", "models", "main"),
    input_builder=("cognition", "input"),
    extensions=(("memory", "extension"),),
)
```

将 `memory` 与 `driver` 加入已有插件集合，再把主体的驱动标签绑定到该工厂。完整资源连接可参考[研究 starter](https://github.com/lavapapa/society0/blob/@COMMIT@/skill/assets/minimal_experiment.py#L81)：当前程序在成功浏览后启用自动提取，访谈沿用默认的跳过自动写入边界，自动召回保持独立。

自动写入处理 `completed` 和 `waiting`，未完成激活保留诊断。记忆正文、原始向量与作业回执保存在 SQL，检索索引从这些事实重建；默认可见版本与衰减使用 `Session.step`。这些边界的原因见[记忆概念](../concepts/memory.md)。

完成这组对照后，进入[结果教程](results.md)，把主体决定和测量组织成可分页读取的研究输出。
