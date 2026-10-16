# 两个轮次对话机制

现在把规则决定变成真实交流。四位主体 `a`、`b`、`c`、`d` 同时参加工作交流 `work` 与公共讨论 `commons`。两种机制采用不同成员顺序，因此可以形成不同配对；每位主体在每轮向两处的当前伙伴各发送一条消息。

## 一、运行已有完整计划

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

预期完成两步。每步有 `pair` 和 `talk` 两个阶段：先由机制建立本轮配对，再激活四位主体。

## 二、读取消息回执

下面是完整读取脚本，保存为 `inspect_conversation.py` 后运行。第 1 步的 `talk` 是索引为 1 的第二个阶段。

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

预期有 8 条回执：四位主体各向两个机制发送一条。每条包含 `mechanism`、`actor`、`round`、收件人和消息 ID。回执用于定位实际消息；下一课会通过信息接口读取正文。

## 三、沿代码理解顺序

工厂里两次调用 `round_robin_plugin`，分别命名为 `work` 和 `commons`，共用同一个主体目录和 StageStore。示例使用 `rule_preset` 装配共同服务，研究代码负责明确阶段：

```python title="原例中的阶段结构（阅读片段）"
[Phase("pair", prepare), Phase("talk", decide)]
```

`prepare` 调用两个机制的 `start_round`；`decide` 激活四位主体。主体的 `talk` 函数通过绑定的 `session.actions` 发送消息，只有发送返回 `completed` 才将本次规则决定标为完成。机制把消息事实与计数写入同一事务。

这里的 `prepare` 是普通阶段函数名称。`Phase.prepare` 另有激活前共享准备能力，详见[调度指南](../guide/schedule.md)。

## 四、尝试第三轮

四人一组有三轮赛程。将配置的 `end` 改为 3，在新的输出目录从头运行，就会看到第三轮的配对和回执。想从已有第 2 步继续，可按[恢复教程](recovery.md)提供原完整点；保留 `start=1`，让调度仍持有完整时间线。

成员必须组成完整的偶数组，每组大小在接口支持范围内。`start_round` 建立配对，`advance_round` 推进轮号；完整语义见[轮次对话参考](../reference/round-robin.md)。采用现成工厂时沿用它已写好的阶段顺序即可。

接下来学习[读取信息](files.md)，从当前主体能够发现的目录走到消息原文。
