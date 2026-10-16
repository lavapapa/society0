# 观察运行

观察服务读取已经保存的进度、会话与结果。你可以在仿真运行时从另一个进程查看，也可以对已完成的教学运行执行同样命令。读取不会激活主体或连接模型服务。

## 一、先看完整步骤

对[首次运行](../start/quickstart.md)执行：

```bash
uv run python -m society0.kernel.observation runs/hello/run
```

预期 `complete.step` 为 2。几个字段回答不同问题：

| 字段 | 如何使用 |
| --- | --- |
| `complete` | 确认已发布的完整身份，选择恢复或分析起点 |
| `complete_step_lower_bound` | 当前数据库已经确认的完成下界 |
| `live_revision` | 当前短事务数据版本，可能包含进行中的事实 |
| `progress` | 判断最近尝试的阶段与工作进展 |
| `failed` | 查看当前状态是否已被标为需要恢复 |

网络等待期间，业务时间可以保持不变；同步计算期间，进度快照也可能暂时不刷新。沿实际请求与事件追踪进展，比单看更新时间更有信息。

## 二、读取模型 Thread

完成[模型教程](llm.md)后，保存以下完整程序为 `observe_thread.py`。它分页读完主体 `a` 当前已有的事件，并保留末尾游标。

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

本例只激活一个主体、一个 Moment，因此第一个目录项就是要看的 Thread。较大运行需要继续分页选择目录。事件打印中可能出现 `payload_ref`，它指向完整正文；`read_thread_payload` 返回 base64 字节，按 `next_offset` 拼接即可取得原 JSON。

Thread tail 到达末尾仍保留 `cursor`，用于下一次轮询发现新事件。有限结果页的 `next_cursor=None` 则表示该页集结束。返回的 `complete_through` 标出已受完整步骤覆盖的事件范围。

## 三、从另一终端读取 HTTP

需要 HTTP 时安装 `observe`，同时保留自己的其他 extra，然后启动服务：

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

应得到与 CLI 相同语义的状态 JSON。按 `Ctrl+C` 结束服务。端口若已占用，选择空闲端口，并同步修改查询地址。

`resource_usage` 查询提供方报告的物理调用与 token，`action_summary` 查询实际 LLM 工具行动与终止诊断。首次规则运行没有模型请求；固定脚本提供方也不产生真实收费服务用量。实际字段和缺失计量的口径见[诊断参考](../reference/diagnostics.md)。

## 四、固定历史读取边界

对某个完整点执行一次状态读取：

```bash
uv run python -m society0.kernel.observation runs/hello/run \
  --complete-step 1 --request '{"method":"status","params":{}}'
```

CLI 准备所选完整点，执行读取后清理临时视图。要连续读取很多页，使用 [ObservationService](../reference/observation.md)复用同一个准备视图，或沿[结果教程](results.md)使用只读分析目录，避免每个请求重新物化。

到这里，你已经能运行、读取、恢复并观察一个小实验。下一步可按[操作指南](../guide/index.md)扩展机制，或用[研究 skill](../resources/research-skill.md)将研究问题组织为有对照和测量的实验。
