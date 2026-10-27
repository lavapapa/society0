# 认识模型驱动

前面几课的决定都由规则函数给出。这一课换成模型驱动：当前认知、完整会话和可调用工具一起交给提供方，驱动再执行它返回的工具请求。提供方先用本地的固定响应，方便你看清完整输入怎么进到 Thread 里、消费位置指向哪里。产物放在 `runs/cognition/run`，运行里的主体是 `a`。

## 装好可选依赖，跑一次离线组合

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

这个[完整工厂](https://github.com/lavapapa/society0/blob/@COMMIT@/examples/core_next/composed_cognition.py)用的是真正的 LLMDriver 和 typed 模型消息，文本由 `ScriptedProvider` 给出；社交语义嵌入关闭，所以不需要外部模型或嵌入服务。两步里主体 `a` 各被激活两次，两次都落在同一个 Moment。

## 完整程序：读 Thread 和消费位置

把下面这份完整程序保存为 `inspect_cognition.py`：

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

预期找到一个持久 Thread，消费位置里带着社交来源和通知来源各自的键。消息中有完整背景和中文原文，也有固定提供方的响应。第二次激活只追加新材料，之前输入过的原文继续留在 Thread 里。追加什么由来源位置决定，上下文里已经有什么由 Thread 决定。

这一例的固定响应直接结束决定，工具仍然由驱动提供；要看到主体完成领域动作，可以继续运行[记忆教程](memory.md)的脚本化工具场景。

## 换成 interview，提交结构化结果

把配置复制一份，只把 `variant` 改成 `interview`，其他字段保留，写到 `runs/cognition/interview.json`：

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

把读取脚本里的路径换成新运行，`threads.find` 的最后一个参数改成 `"interview"`。这一次 Thread 记录的是 `submit_result` 的结构化提交，里面有示例的测量字段。decision 让主体挑选领域行动，interview 让主体按 schema 提交测量，两种 mode 各有自己的 Thread 身份。

## 接到真实提供方和记忆服务

完整认知、提供方、记忆与行动策略分别装配。先用本地脚本场景核对输入和完成条件，再为自己的运行计划配置远端服务。

接入真实服务时，按[提供方指南](../guide/models.md)选择 profile、配置凭据与请求参数。用 `llm_driver_plugin` 绑定 provider 和 input_builder，`LLMPolicy` 决定哪些行动允许、哪些必需、哪些算完成，以及预算多少。已有的硬上限被耗尽，或者输出被截断，这次激活记成 `incomplete`；这样的步骤怎么处理，由阶段策略决定。

本地固定响应用来学清楚输入、位置和结果之间的保存关系。模型能不能理解任务、会不会挑对行动，要针对实际提供方和研究条件单独验证。下一课是[记忆的三个开关](memory.md)，看经验写入与下一次召回如何分别开关。
