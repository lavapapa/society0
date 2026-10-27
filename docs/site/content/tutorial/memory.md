# 记忆的三个开关

上一课的模型主体拿到的都是本地固定响应。这一课给它加上记忆：把经验存下来、激活时自动想起、由主体主动检索，这三件事各有开关，可以分别打开或关上。模型响应和嵌入都在本地生成，向量检索用的是真实的 Chroma。工厂仍用上一课的 `examples.core_next.composed_cognition:build`，产物放在 `runs/memory/write`，参与运行的主体是 `a`。

## 只开自动写入，跑一步

先完成[模型驱动教程](llm.md)，再装上记忆相关的依赖：

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

这个[完整例子](https://github.com/lavapapa/society0/blob/@COMMIT@/examples/core_next/composed_shutdown_extensions.py#L267)在第 1 步先放入一条种子经验，再让主体激活两次。脚本提供方会真的调用资料工具和 `probe.record` 行动，自动提取器每次从一段完整经历产生一条独立记忆。最后应该发布第 1 步。

## 完整程序：查记忆表和作业表

把下面这份完整程序保存为 `inspect_memory.py`。它用两条短 SQL 查询读示例用到的记忆状态表；表结构属于当前的[记忆实现](https://github.com/lavapapa/society0/blob/@COMMIT@/src/society0/kernel/memory.py#L19)，这里只是拿来核对，日常业务操作仍走 Memory 服务。

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

应该看到主体 `a` 的就绪记忆和已完成作业。这个场景里有一条初始种子，加上两次自动提取产生的经验。正文和提取时用的输入可以按[Thread 观察](observation.md)继续核对：数量回答有没有存下来，内容回答存了什么。

## 每次只改一个开关，对照结果

把配置复制成新文件，改掉 `variant`，每次运行换一个输出目录。各 variant 的差别如下：

| variant | auto_write | auto_recall | active_tools | 应观察的变化 |
| --- | --- | --- | --- | --- |
| `memory-write` | 开 | 关 | 关 | 完成激活后保存新经验 |
| `memory-recall` | 关 | 开 | 关 | 种子记忆进入认知，自动写入保持关闭 |
| `memory-tools` | 关 | 关 | 开 | 脚本主动调用 `memory.recall` 与 `memory.remember` |
| `memory-all` | 开 | 开 | 开 | 自动行为与主动工具同时存在 |

例如新建 `runs/memory/recall.json`，把 variant 改成 `memory-recall`，再运行：

```bash
uv run python -m society0.kernel.runner \
  --factory examples.core_next.composed_cognition:build \
  --config runs/memory/recall.json \
  --output runs/memory/recall
```

把读取程序指向新运行，就能比较两次写入的差别。自动召回关掉之后，主体仍能用主动工具检索；主动工具关掉之后，自动写入仍会把经验存下来。每个开关对应一种研究条件，记录时要分开写。

## 装配片段：把记忆接到自己的驱动上

下面这段是放进已有模型计划里的**装配片段**。它使用[多来源 cognition_plugin](../guide/cognition.md)的 `input_builder` 服务，依赖具名模型、嵌入和向量客户端资源：

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
    input_builder=("cognition", "input_builder"),
    extensions=(("memory", "extension"),),
)
```

把 `memory` 和 `driver` 加进已有的插件集合，再把主体的驱动标签指到这份工厂。资源接线见[模型与嵌入](../guide/models.md)。访谈默认跳过自动写入，自动召回独立配置。

自动写入会处理 `completed` 和 `waiting`，未完成的激活保留诊断。记忆正文、原始向量和作业回执都存在 SQL 里，检索索引由这些事实重建；默认可见版本和衰减用的是 `Session.step`。这些边界的理由见[记忆概念](../concepts/memory.md)。

完成这组对照后，进入[结果教程](results.md)，把主体决定和测量组织成可分页读取的研究输出。
