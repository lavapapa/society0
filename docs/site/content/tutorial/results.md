# 记录与读取结果

阶段结果回答的是“发生了几次决定”“每位主体返回了什么”这类问题。这一课回到[第一次运行](../start/quickstart.md)留下的 `runs/hello/run`，把第 2 步的决定表完整读一遍，顺势看清分页怎么走、一行记录太大时原文放在哪里。

## 阶段里决定记录哪些测量

在[自己的运行计划](first-plan.md)里，阶段函数已经返回 `StepResult`。下面这段可以替换其中的 `decide` 函数：

```python title="替换 decide"
async def decide(context):
    outcomes = await activate(context, actors)
    return StepResult(
        metrics={"activated": len(outcomes)},
        tables={"decisions": (item.result.value for item in outcomes)},
        notes="保存本阶段每个主体的原始决定。",
    )
```

`metrics` 放指标，`tables` 逐行保存记录，运行时另外保存激活了哪些主体、状态和原因。这里的表可以直接收生成器，取一行写一行。要整块保存一个 JSON 值时用 `TableValue`；手上已经有不可变数据集时用 `DatasetTable` 引用，详见[结果参考](../reference/results.md)。

指标要对着研究问题来定，比如“成功发送消息的主体数”。名字里写清楚统计的是什么，激活次数、行动次数和发送消息数才不会混成一项。

## 完整程序：把 decisions 表读完

把下面这份完整程序保存为 `read_results.py`。它先准备第 2 步的只读视图，再找到阶段头部和其中的 `decisions` 表；每页只取一条，方便看清分页。

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

预期决定数是 2，Alice 和 Bob 的时间都是 2。把自己的运行路径传进同一段流程，就能读到改过的字段。阶段很多的研究还要给 `result_phases` 分页，这一例每步只有一个阶段。

## 一页结果里都有什么字段

每个页项带着 `ordinal` 和 `raw_bytes`，真正的业务值放在 `value` 里。一行超过这一页的字节预算时，页项给出 `payload_ref`，交给 `read_result_record` 分段取回完整的 JSON 原文字节。Observation 把 bytes 编成 base64，所以上面的程序先把 bytes 还原回来，再解码 JSON。

业务记录本身也可能有一个叫 `payload_ref` 的字段，它在 `value` 里面，和分页给出的原文引用互不相干。翻下一页用返回的 `next_cursor`，总数从 `total` 取。

把一个很大的 JSON 值完整读回来，内存里终究要放下它。分页控制的是每次传输多少；批量导出时逐条消费迭代器，别像教学代码这样把整张表转成 list。

结果读取已经和运行分开。下一课学习[恢复与继续](recovery.md)，从相同的完整步骤建立另一份可执行的运行。
