# 认识模型驱动

规则函数由研究者决定下一步；模型驱动把当前认知、完整会话与可调用工具交给提供方，再执行返回的工具请求。这一课先使用本地确定性响应，观察完整输入和 Thread 如何保存，再找到接入真实模型的位置。

## 一、准备离线组合示例

在仓库根目录安装例子需要的可选依赖。`llm` 的原生组件构建要求见[安装](../start/installation.md)。

```bash
uv sync --frozen --extra llm --extra social
mkdir -p runs/cognition
uv run python - <<'PY'
import json
import subprocess
from pathlib import Path

config = {
    "release": {"commit": subprocess.check_output(
        ["git", "rev-parse", "HEAD"], text=True).strip()},
    "case": "multi-source", "variant": "default",
    "provider": "scripted", "start": 1, "end": 2,
}
Path("runs/cognition/config.json").write_text(
    json.dumps(config, ensure_ascii=False), encoding="utf-8",
)
PY
uv run python -m society0.kernel.runner \
  --factory examples.core_next.composed_cognition:build \
  --config runs/cognition/config.json \
  --output runs/cognition/run
```

这个[完整工厂](https://github.com/lavapapa/society0/blob/@COMMIT@/examples/core_next/composed_cognition.py)使用真正的 LLMDriver 和 typed 模型消息，由 `ScriptedProvider` 返回固定文本；社交语义嵌入关闭，无需外部模型或嵌入服务。两步中各激活主体 `a` 两次，沿用相同 Moment。

## 二、查看实际 Thread 与来源位置

保存下面完整程序为 `inspect_cognition.py`：

```python title="inspect_cognition.py"
from society0.kernel.interaction import Moment
from society0.kernel.storage import StageReader
from society0.kernel.threads import ThreadStore

with StageReader("runs/cognition/run") as reader:
    threads = ThreadStore(reader)
    thread_id = threads.find("a", Moment(1, "read"), "decision")
    print("Thread：", thread_id)
    print("消费位置：", threads.input_cursor(thread_id, "operating_context"))
    messages = threads.read_messages(thread_id)
    print("完整消息数量：", len(messages))
    for message in messages:
        print(message)
```

```bash
uv run python inspect_cognition.py
```

预期找到一个持久 Thread，消费位置中含社交来源和通知来源各自的键。消息里既有完整背景与中文原文，也有固定提供方响应。第二次激活追加新材料，之前已输入的原文继续保留。来源位置决定追加什么，Thread 决定完整上下文中已经有什么。

这一例的固定响应直接结束决定。工具仍由驱动提供，后面的真实研究 starter 才要求主体实际完成指定领域动作。

## 三、试一次结构化访谈

从配置复制出另一份，把 `variant` 改为 `interview`，保留其他字段，写到 `runs/cognition/interview.json`：

```bash
uv run python - <<'PY'
import json
from pathlib import Path
config = json.loads(Path("runs/cognition/config.json").read_text())
config["variant"] = "interview"
Path("runs/cognition/interview.json").write_text(
    json.dumps(config, ensure_ascii=False), encoding="utf-8",
)
PY
uv run python -m society0.kernel.runner \
  --factory examples.core_next.composed_cognition:build \
  --config runs/cognition/interview.json \
  --output runs/cognition/interview
```

将读取脚本路径改成新运行，并将 `threads.find` 的最后一个参数改为 `"interview"`。Thread 会记录 `submit_result` 的结构化提交，其中包含示例测量字段。decision 用于主体选择领域行动，interview 用于按 schema 提交测量；两种 mode 有各自的 Thread 身份。

## 四、转向真实研究

[完整研究 starter](https://github.com/lavapapa/society0/blob/@COMMIT@/skill/assets/minimal_experiment.py)把过程分为浏览与访谈：先要求 `news.view_details` 成功，再在下一步提交可信度评分与理由。它装配模型、嵌入与记忆服务，适合在理解当前链条后复制修改。

接入真实服务时，按[提供方指南](../guide/models.md)选择 profile、配置凭据与请求参数。用 `llm_driver_plugin` 绑定 provider 和 input_builder；`LLMPolicy` 决定允许、必需、完成行动与预算。已有硬上限耗尽或输出截断会记录为 `incomplete`，完整步骤如何处理它由阶段策略决定。

本地固定响应用于学习输入、位置和结果的保存关系；模型是否理解任务、选择合适行动，需要针对实际提供方和研究条件另行验证。下一课学习[记忆的三个开关](memory.md)，把经验写入与下一次召回连接起来。
