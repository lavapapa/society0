# 记录与读取结果

阶段结果让研究者回答“发生了几次决定”“每位主体返回了什么”。这一课沿用[第一次运行](../start/quickstart.md)的 `runs/hello/run`，完整读取第 2 步的决定表，并学会处理分页与较大的原文记录。

## 一、选择要记录的测量

在[自己的运行计划](first-plan.md)中，阶段已经返回 `StepResult`。下面是可替换该 `decide` 函数的片段：

```python title="替换 decide"
async def decide(context):
    outcomes = await activate(context, actors)
    return StepResult(
        metrics={"activated": len(outcomes)},
        tables={"decisions": (item.result.value for item in outcomes)},
        notes="保存本阶段每个主体的原始决定。",
    )
```

`metrics` 记录指标，`tables` 按行保存记录；运行时另保存激活的主体、状态和原因。这里的表接收生成器，逐行取值。一次需要保存整个 JSON 值时用 `TableValue`；已有不可变数据集时用 `DatasetTable` 引用，详见[结果参考](../reference/results.md)。

指标应对应研究问题，例如“成功发送消息的主体数”。明确命名统计口径，能避免把激活次数、行动次数和发送消息数混为一项。

## 二、完整读取一张表

保存下面完整程序为 `read_results.py`。程序先准备第 2 步的只读视图，再定位阶段头部及其 `decisions` 表。为便于观察分页，每页取一条。

```python title="read_results.py"
import base64
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from society0.kernel.observation import Observation
from society0.kernel.storage import StageStore


def read_all_rows(reader, reference):
    cursor = None
    while True:
        page = reader.result_page(reference, cursor=cursor, limit=1)
        for item in page["items"]:
            if "value" in item:
                yield item["value"]
            else:
                raw, offset = bytearray(), 0
                while True:
                    part = reader.read_result_record(
                        item["payload_ref"], offset=offset, size=4096,
                    )
                    raw.extend(base64.b64decode(part["data"]))
                    if part["next_offset"] is None:
                        break
                    offset = part["next_offset"]
                yield json.loads(raw)
        cursor = page["next_cursor"]
        if cursor is None:
            break


with TemporaryDirectory() as temporary:
    view = Path(temporary) / "complete"
    with StageStore.prepare_readonly("runs/hello/run", view, step=2):
        pass
    with Observation(view) as reader:
        phase = reader.result_phases(step=2)["items"][0]
        header = next(read_all_rows(reader, phase["reference"]))
        rows = list(read_all_rows(reader, header["tables"]["decisions"]))
        print("决定数：", len(rows))
        for row in rows:
            print(row)
```

```bash
uv run python read_results.py
```

预期决定数为 2，Alice 和 Bob 的时间都是 2。将自己的运行路径传入同样流程，就能读取修改后的字段。具有很多阶段的研究还需分页读取 `result_phases`；本例每步只有一个阶段。

## 三、读懂结果页信封

每个页项有 `ordinal` 与 `raw_bytes`，实际业务值放在 `value` 中。超出本页字节预算的行提供 `payload_ref`，供 `read_result_record` 分段取得完整 JSON 原文字节。Observation 把 bytes 编码成 base64，因此上面的程序先还原 bytes，再解码 JSON。

业务记录中也可以有名为 `payload_ref` 的字段：它位于 `value` 内，与分页信封中的原文引用各自独立。续页使用返回的 `next_cursor`，总数来自 `total`。

完整读回一个巨大的 JSON 值仍要容纳该值。分页控制每次传输范围；批量导出时逐条消费迭代器，避免像教学代码一样把整表转换成 list。

## 四、进入可视化检查

完成[对话教程](conversation.md)后，可以把它的两个完整步骤导出给工作台：

```bash
uv run python -m society0.kernel.workbench \
  --run runs/conversation/run --version baseline \
  --steps 1 2 --actors a b c d \
  --output runs/conversation/workbench.json
uv run python skill/scripts/render_workbench.py \
  --data runs/conversation/workbench.json \
  --output runs/conversation/workbench.html
```

在浏览器打开生成的 HTML，检查阶段指标、消息回执和来源位置。导出保留选定完整步骤的记录，运行中的实时诊断继续通过 Observation 读取。多版本选择与完整导出成本见[工作台指南](../guide/workbench.md)。

结果读取已经与运行分开。下一课学习[恢复与继续](recovery.md)，从相同完整点建立另一份可执行运行。
