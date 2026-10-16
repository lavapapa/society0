# 观察运行

前面几课的读取都靠自己写脚本，一次打开一个运行目录。这一课用观察服务，它能读到已经保存的进度、会话和结果：仿真还在跑的时候可以另开一个进程看，对已经跑完的教学运行也能下同样的命令，整个过程不会激活主体，也不会连模型服务。练习先用快速开始留下的 `runs/hello/run`，读到模型 Thread 时换回[模型教程](llm.md)的 `runs/cognition/run`。

## 先看已经发布到哪一步

对[首次运行](../start/quickstart.md)执行：

```bash
uv run python -m society0.kernel.observation runs/hello/run
```

预期 `complete.step` 是 2。下面几个字段各自回答不同的问题：

| 字段 | 如何使用 |
| --- | --- |
| `complete` | 确认已发布的完整身份，选择恢复或分析起点 |
| `complete_step_lower_bound` | 当前数据库已经确认的完成下界 |
| `live_revision` | 当前短事务数据版本，可能包含进行中的事实 |
| `progress` | 判断最近尝试的阶段与工作进展 |
| `failed` | 查看当前状态是否已被标为需要恢复 |

等网络返回的时候，业务时间可以一直不动；同步计算的时候，进度快照也可能暂时不刷新。想知道进展，顺着实际请求和事件看，比只看更新时间有信息。

## 完整程序：接着上一次的位置读 Thread

做完[模型教程](llm.md)之后，把下面这份完整程序保存为 `observe_thread.py`。它一页页读完主体 `a` 当前已经有的事件，并留下末尾的游标。

```python title="observe_thread.py"
from society0.kernel.observation import Observation

with Observation("runs/cognition/run") as reader:
    directory = reader.list_threads(actor="a", limit=20)
    thread_id = directory["items"][0]["id"]
    cursor = None
    while True:
        page = reader.thread_tail(thread_id, cursor=cursor, limit=20)
        for event in page["items"]:
            print(event)
        cursor = page["cursor"]
        if not page["items"]:
            break
    print("后续追加位置：", cursor)
```

```bash
uv run python observe_thread.py
```

这一例只激活了一个主体、一个 Moment，所以目录里的第一项就是要看的 Thread；运行更大时要继续翻页挑。打印出来的事件里可能出现 `payload_ref`，它指向完整正文；`read_thread_payload` 返回的是 base64 字节，按 `next_offset` 拼起来就能拿到原来的 JSON。

Thread tail 读到末尾仍会留下 `cursor`，下一次轮询接着用它发现新事件；分页结果里的 `next_cursor=None` 表示这一批已经读完。返回的 `complete_through` 标出哪些事件已经被完整步骤覆盖。

## 另开一个终端，用 HTTP 查

要用 HTTP 就装上 `observe`，同时保留自己原来那些 extra，然后启动服务：

```bash
uv sync --frozen --extra observe
uv run python -m society0.kernel.observation runs/hello/run \
  --serve 8711 --capacity 4 --timeout 5
```

服务终端保持运行。在另一终端执行：

```bash
curl -s http://127.0.0.1:8711/ \
  -H 'Content-Type: application/json' \
  -d '{"method":"status","params":{}}'
```

得到的 JSON 和 CLI 的状态语义一致。按 `Ctrl+C` 结束服务。端口被占用就换一个空闲的，查询地址跟着一起改。

`resource_usage` 查提供方报告的物理调用次数和 token，`action_summary` 查 LLM 实际发起的工具行动和终止诊断。首次规则运行没有模型请求，固定脚本提供方也不产生真正收费的服务用量。实际字段和计量缺失时怎么算，见[诊断参考](../reference/diagnostics.md)。

## 把读取固定在某一个完整步骤

只对某一个完整步骤读一次状态：

```bash
uv run python -m society0.kernel.observation runs/hello/run \
  --complete-step 1 --request '{"method":"status","params":{}}'
```

CLI 会准备选中的完整步骤，读完把临时视图清掉。要连着读很多页，就用 [ObservationService](../reference/observation.md) 复用同一份准备好的视图，或者按[结果教程](results.md)开一个只读分析目录，省得每个请求都重新准备一遍。

走到这里，你手上已经有一个能运行、能读结果、能从完整步骤恢复、还能实时观察的小实验。接下来可以按[操作指南](../guide/index.md)扩展机制，或者用[研究 skill](../resources/research-skill.md)把研究问题组织成带对照和测量的实验。
