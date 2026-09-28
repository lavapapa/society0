# 读取运行进度与结果

Society0 的观测接口可用于正在运行和已经结束的实验。查询进程读取运行产物，在本地建立可重建索引；它不创建仿真 World，也不调用模型服务。本文说明查询入口与读取边界，格式变化见 [5.0.0 发行说明](release-5.0.0.md)。

## 一、启动

在安装 Society0 的 Python 环境中执行以下命令。`--index-dir` 应放在查询机器的本地磁盘，运行目录可以位于共享存储。本地派生索引使用 SQLite WAL，`--index-dir` 不应指向 Ceph、NFS 等网络文件系统。省略索引目录时使用临时本地目录，进程正常关闭后清理。

```bash
python -m pip install -e .
python -m society0.observation runs/example --index-dir /tmp/example-index --method status
python -m society0.observation runs/example --index-dir /tmp/example-index --method sync
python -m society0.observation runs/example --index-dir /tmp/example-index --serve 8765
```

服务监听 `127.0.0.1`，后台每 0.5 秒检查一次完整提交并更新索引。索引处理大批数据时，状态查询继续响应。单次 CLI `sync` 等待索引完成；服务中的 `sync` 返回当前水位，后台负责继续处理。在线与离线采用相同方法。

## 二、状态

向服务发送 JSON 方法名和参数，响应的 `result` 保存结果；错误响应的 `error` 说明失败原因。以下命令适用于本地服务。

```bash
curl http://127.0.0.1:8765 -H 'Content-Type: application/json' \
  -d '{"method":"status","params":{}}'
```

响应结构如下，ID 和时间取决于实际运行：

```json
{"result":{"run_id":"…","phase":"code_step_started","executing_step":4,
 "last_completed_step":3,"observed_at":1790000000.0,"producer_pid":12345,
 "status_age_seconds":1.2,"committed_checkpoint":{"checkpoint_id":"…","step":3},
 "indexed_checkpoint":{"checkpoint_id":"…","step":2},"index_error":null}}
```

`committed_checkpoint` 表示完整 marker 已公布的数据，`indexed_checkpoint` 表示查询索引已经处理完的数据。运行进度另由 `executing_step` 和 `last_completed_step` 表示。同步长计算期间生产者可能尚未报告新进度，`status_age_seconds` 会继续增加；进程存在本身不证明计算正在推进。

`watch` 是短轮询。保留返回的 `next_cursor`，下次作为 `cursor` 传入；位置未改变时 `items` 为空。通知只给出最新状态和引用，历史数据从状态页与 Thread 页取得。服务不为断开的消费者保留通知队列。换运行时重新取得游标。

## 三、状态页

Python 入口适合批量分析，HTTP 的方法名与参数相同。先同步索引，再固定 `checkpoint_id` 读取所有页面。

```python
from society0 import ObservationReader

with ObservationReader("runs/example", index_dir="/tmp/example-index") as reader:
    status = reader.sync()
    checkpoint = status["indexed_checkpoint"]["checkpoint_id"]
    cursor = None
    while True:
        page = reader.state_page(checkpoint, ["environment", "state"],
                                 cursor=cursor, limit=100, max_bytes=1048576)
        for item in page["items"]:
            print(item["path"], item.get("value"), item["content_ref"])
        cursor = page["next_cursor"]
        if cursor is None:
            break
```

每页包含 `items`、精确 `total`、`next_cursor` 和提交身份。条目按持久化状态的插入次序返回，数组位置保持数值顺序；替换已有条目保留位置，删除后重建产生新位置。`bytes` 统计紧凑 JSON `items` 数组的字节数，响应外层的身份与水位另计。返回 `next_cursor=null` 表示固定版本已经读完；新提交不会改变后续页面。

最小分页单位是持久化声明中的可替换值、可替换映射 entry 或追加记录。可查询某个声明集合、祖先路径或完整 entry。一个 entry 内部的任意嵌套字段通过其完整内容取得；请求内部路径时，`unsupported_path` 给出最近可读祖先。普通代理的嵌套修改在 journal 中归并为完整 entry，因此页面能表示其最新值。直接调用底层存储构造的非规范父子交叠修改可能返回 `unsupported_operation`；恢复接口仍按原始操作顺序恢复这类检查点。

大条目返回 `content_ref`，较小条目同时提供 `value`。引用指向原始完整操作 JSON，其 `value` 字段是该条目内容。下面以固定缓冲写出一个超大条目；文件中的 JSON 可以包含任意大的嵌套值。将全部页面的 `path` 与条目值按顺序赋入映射或序列，即可取得整个集合；空容器也有明确条目。

```python
with ObservationReader("runs/example", index_dir="/tmp/example-index") as reader:
    with open("entry.json", "wb") as output:
        offset = 0
        while True:
            part = reader.read_content(item["content_ref"], offset=offset, max_bytes=65536)
            output.write(part["data"])
            offset = part["next_offset"]
            if offset is None:
                break
```

Python 返回 `bytes`；HTTP/CLI 响应将 `data` 编码为 Base64，并给出 `encoding="base64"`。每个区间是内容流的一段，可能从 UTF-8 字符或 JSON token 中间开始。完整写出后再解析。请求尚未建立索引的检查点返回 `index_pending`；读取器不自动改用另一个检查点。

## 四、交互与结果

Thread 页默认读取调用时已写出的完整前缀。分页期间追加的新事件留给下一次首屏请求，半行留待写入完成；读者从不修复或截断源文件。

```python
page = reader.thread_page("thr_…", limit=100, max_bytes=65536)
committed_page = reader.thread_page("thr_…", checkpoint_id=checkpoint,
                                     limit=100, max_bytes=65536)
```

`readable_through` 给出可读取边界。默认实时读取的 `durable_through` 为空；传入已索引检查点时，边界来自该检查点公布的 Thread 引用，并返回其耐久范围。超大事件保留 `event_ref`，外置正文保留 `payload_ref`；均可交给 `read_content` 分段读取。Thread 已经可读与包含它的状态已经提交是两项独立事实。

大表的步骤结果保存数据集引用，使用 `dataset_page(reference, cursor=…, limit=…, max_bytes=…)` 取得记录。数据集需出现在完整检查点的 annotations 中才能作为已提交步骤结果读取；单独生成的文件与失败步骤的结果不会因此获得提交身份。summary 的历史统计引用标为 `run_diagnostics`，属于运行诊断。需要完整诊断历史时调用 `load_run_summary(run_dir, include_history=True)`，其内存开销随读取历史增长。

## 五、维护

索引属于本地派生数据。停止查询服务后可重新建立索引，原始运行目录保持不变：

```bash
python -m society0.observation runs/example --index-dir /tmp/example-index \
  --rebuild-index --method sync
```

索引事务中断后，重新启动会从最后完成的索引检查点继续。`index_error` 表示后台处理失败，状态中仍保留最后成功水位。恢复或 fork 的运行引用来源组件，缺失来源返回 `source_missing`。移动运行前，导出包含依赖的独立目录：

```python
from society0.incremental_checkpoint import V4CheckpointStore

store = V4CheckpointStore("runs/example", create=False)
analysis = store.export_bundle("exports/example-analysis", mode="analysis")
# 原运行以及来源运行均结束、原生产者进程退出后，才导出带记忆的恢复包。
restorable = store.export_bundle("exports/example-restore", mode="restore")
```

目标目录须尚不存在。`analysis` 包含状态、Thread 与已提交数据集，`bundle.json` 的 `full_restore` 为 false。`restore` 还复制恢复所需记忆库；涉及记忆时要求运行有最终状态且原生产者 PID 已退出，避免普通文件复制读取活动 Chroma。来源链使用相同检查；纯状态、无记忆依赖的运行无需等待记忆库关闭。移动导出目录时保留其整体结构，原始 source 依赖由包内相对引用替代。

查询索引的初次建立随权威记录总数增长，此后按新提交增量更新。当前状态分页通过集合与顺序索引定位，不解压整个世界。World 本身的累计状态内存、完整恢复内存及任意大条目的完整解析开销，仍需由仿真和分析任务分别安排。性能数据保存在本次研究目录，接口可用不自动证明所有部署文件系统和真实模型链路都已验收。
