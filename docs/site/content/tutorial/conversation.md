# 两个轮次对话机制

前面几课的主体各自返回一条决定，彼此之间没有消息往来。这一课换成仓库自带的对话工厂，四位主体 `a`、`b`、`c`、`d` 同时参加工作交流 `work` 与公共讨论 `commons`，每人每轮向两处的当前伙伴各发一条消息。两个机制排成员的顺序不同，配出来的伙伴也不同，跑完之后你能从结果里读出每一次发送的回执。这一课不再用 `my_simulation.py`，产物放在 `runs/conversation/run`。

## 用仓库自带的对话工厂跑两步

完成[基础安装](../start/installation.md)后，在仓库根目录执行。这个教程使用仓库的[完整对话工厂](https://github.com/lavapapa/society0/blob/@COMMIT@/examples/core_next/conversation_pilot.py)，无需模型服务。

```bash
mkdir -p runs/conversation
uv run python - <<'PY'
import json
import subprocess
from pathlib import Path

config = {
    "release": {"commit": subprocess.check_output(
        ["git", "rev-parse", "HEAD"], text=True).strip()},
    "start": 1,
    "end": 2,
}
Path("runs/conversation/config.json").write_text(
    json.dumps(config, ensure_ascii=False), encoding="utf-8",
)
PY
uv run python -m society0.kernel.runner \
  --factory examples.core_next.conversation_pilot:build \
  --config runs/conversation/config.json \
  --output runs/conversation/run
```

预期完成两步。每一步里有 `pair` 和 `talk` 两个阶段，先由机制配好本轮的分组，再激活四位主体。

## 完整程序：把 8 条回执读完

下面这份完整程序保存为 `inspect_conversation.py` 后运行。第 1 步的 `talk` 是这一步里索引为 1 的第二个阶段。

```python title="inspect_conversation.py"
from society0.kernel.results import Results
from society0.kernel.storage import StageReader

with StageReader("runs/conversation/run") as reader:
    results = Results(reader)
    header = results.phase(1, 1)
    reference = header["tables"]["messages"]
    page = results.page(reference, limit=4)
    print("消息回执总数：", page["total"])
    while True:
        for item in page["items"]:
            print(item["value"])
        if page["next_cursor"] is None:
            break
        page = results.page(reference, cursor=page["next_cursor"], limit=4)
```

```bash
uv run python inspect_conversation.py
```

预期有 8 条回执，四位主体各向两个机制发出一条。每条回执包含 `mechanism`、`actor`、`round`、收件人和消息 ID。回执用来定位实际消息，正文要到下一课通过信息接口读取。

## 顺着工厂代码看阶段顺序

工厂里调用 `round_robin_plugin` 两次，分别命名为 `work` 和 `commons`，两者共用同一个主体目录和 StageStore。示例用 `rule_preset` 装配几个机制共用的服务，阶段由研究代码写明：

```python title="原例中的阶段结构（阅读片段）"
[Phase("pair", prepare), Phase("talk", decide)]
```

`prepare` 调用两个机制的 `start_round`，`decide` 再把四位主体激活。主体的 `talk` 函数通过绑定在 Session 上的 `session.actions` 发送消息，只有发送返回 `completed`，这次规则决定才记成完成。消息本身和计数由机制在同一个事务里写入。

这里的 `prepare` 只是普通阶段函数的名称；`Phase.prepare` 是另一回事，指激活前的共享准备，详见[调度指南](../guide/schedule.md)。

## 试跑第三轮，或从第 2 步继续

四位主体能排三轮比赛。把配置里的 `end` 改成 3，换一个新的输出目录从头运行，就会看到第三轮的配对和回执。要从已有的第 2 步接着跑，可以按[恢复教程](recovery.md)交出原来的完整步骤；配置里的 `start=1` 保持不变，让调度仍拿着完整的时间线。

成员 ID 不能重复，人数要正好分成完整的偶数组，组大小在接口支持的范围内。`start_round` 建立配对，`advance_round` 推进轮号，完整语义见[轮次对话参考](../reference/round-robin.md)。用现成的工厂时，照它写好的阶段顺序走就可以。

接下来学习[读取信息](files.md)，从当前主体能够发现的目录走到消息原文。
