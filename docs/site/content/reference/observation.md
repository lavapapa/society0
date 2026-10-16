# Observation：运行观察

`from society0.kernel.observation import Observation, ObservationService`。观察器直接读取既有运行目录，提供 Python、CLI 和 HTTP 入口；读取过程不装配机制、不连接模型，也不推进步骤，因此不会改动那份运行。调用者提供运行目录路径，用完自己关闭观察对象。

## 运行状态与追加读取

Observation 在上下文管理器中复用同线程只读连接，每次请求使用独立短事务。

<!-- api: society0.kernel.observation.Observation -->
<!-- api: society0.kernel.observation.Observation.status -->
<!-- api: society0.kernel.observation.Observation.list_threads -->
<!-- api: society0.kernel.observation.Observation.thread_tail -->
<!-- api: society0.kernel.observation.Observation.read_thread_payload -->

status 返回运行身份、live_revision、完整步骤下界、实际 complete、failed 和 progress。完整身份来自描述符；progress 是允许落后的诊断快照，模型等待或同步计算期间更新时间可能不变。描述符身份或格式有误时读取失败。

list_threads 和 thread_tail 保留追加 cursor，当前尾部空页后仍可继续发现新增内容。tail 的 complete_through 标出已经纳入完整步骤的事件前缀。大事件提供 payload_ref，read_thread_payload 返回 base64 原文段、总字节数和下一偏移；先拼接原始字节，再解码 JSON。

max_bytes 约束最终 JSON 响应，包含身份、游标及 base64 膨胀。预算容纳不了必要身份时明确报错。分页、短投影和范围读取按请求量工作；完整正文的读取仍承担实际内容成本。

## live 与 complete

默认读取 live，包含未完成步骤的诊断事实；固定历史分析使用服务准备的 complete 视图。

<!-- api: society0.kernel.observation.ObservationService -->
<!-- api: society0.kernel.observation.ObservationService.prepare_complete -->
<!-- api: society0.kernel.observation.ObservationService.preparation_status -->
<!-- api: society0.kernel.observation.ObservationService.call -->
<!-- api: society0.kernel.observation.ObservationService.clear_prepared -->

prepare_complete 启动独立进程物化完整步骤，状态为 preparing、ready 或 failed。call 的 params 传入 view 选择已准备视图，省略时读取源运行 live。每个服务同时持有一个准备任务和一个 ready；新视图成功前保留旧 ready，忙时返回 preparation_busy。clear_prepared 或关闭服务会取消并等待进程、删除自己拥有的临时视图，旧 view 随之失效。

准备需要完整链重建与临时数据库空间，峰值可同时包含源、旧 ready 和新 building。同文件系统不可变工件可硬链接，跨文件系统需复制。只读视图使用稳定的源运行及完整步骤身份；换步骤或运行后拒绝旧游标。业务恢复分支使用新运行身份。

## Python、CLI 与 HTTP

以下示例对实际运行目录执行只读状态和目录查询：

```python
from society0.kernel.observation import Observation

def inspect_run(path):
    with Observation(path) as reader:
        return reader.status(), reader.list_threads(limit=20)
```

<!-- api: society0.kernel.observation.make_app -->
<!-- api: society0.kernel.observation.make_server -->

CLI `python -m society0.kernel.observation RUN_DIR` 输出状态；加 `--serve PORT` 启动 HTTP，需 observe 可选依赖。HTTP 接收包含 method、params 的 JSON，请求容量满返回 503 server_busy。timeout 限定收发等待，同步数据库线程的查询耗时由实际工作决定。一次性 `--complete-step` 会每次重建并清理视图，连续历史分页宜复用服务。

Results、工件和领域资料均有分页或范围接口；领域 Information 通过 information_factory 显式绑定。进一步见[观察教程](../tutorial/observation.md)、[结果接口](results.md)及[诊断](diagnostics.md)。源码入口为 [ObservationService](https://github.com/lavapapa/society0/blob/@COMMIT@/src/society0/kernel/observation.py#L363)。
