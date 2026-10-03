# 外部观察

观察服务连接既有运行目录，读取原生 SQLite、完整步骤描述符和不可变正文。它不安装运行插件、不建立模型连接，也不启动模拟步骤。Python、CLI 和 HTTP 共用读取实现；实时追加、固定信息页和完整步骤视图采用各自清楚的读取边界。

## 一、水位

`Observation(path).status()` 返回 run_id、live_revision、complete_step_lower_bound、complete、failed 和 progress。complete 来自不可变步骤描述符；current 内的 complete_step 是写入器已经确认的下界。正常发布窗口中，从下界查找后续描述符通常相差零至一项，查询不枚举整个步骤目录。描述符身份不一致、JSON 不完整或下界描述符缺失时，调用明确失败。此短接口确认发布身份，不代替对全部历史组件的恢复校验。

progress 是独立的允许落后快照，包括运行状态、阶段、活动数、已结束激活数和更新时间。网络等待期间阶段可能保持不变；同步业务计算也不会自动刷新该时间。Thread 中实际 provider request/response 原文可进一步解释进展，进度年龄不等于模拟时间。failed 是权威 current 的失败标记，当前未完成事实可供诊断，恢复仍选择完整步骤。

`with Observation(path) as reader` 在同一线程复用只读连接，每次请求独立开始和结束短读事务。回调、游标及 SQLite 快照不跨网络等待。当前 HTTP 以有界线程处理每次请求，并在该请求内使用独立 reader，未复用跨请求连接。不同 schema 规模下的实际 HTTP 连接数、延迟与长期 Python reader 对照见 `research/core-next/observation-validation-20261004.md`。

## 二、读取

`list_threads(actor=None, cursor=None, limit=100, max_bytes=65536)` 返回目录 items、精确 total 和始终保留的 cursor。目录按持久 ordinal 继续，在当时末尾得到空页后，原 cursor 仍可发现后来建立的 Thread。`thread_tail(thread_id, cursor=None, limit=100, max_bytes=65536)` 同样保留追加位置；total 是当前事件数，complete_through 是当前权威完整步骤所覆盖的最后事件序号。事件在规范写入时登记 publish_step，原生索引按 `(thread_id,publish_step,seq)` 定位完整前缀，恢复分支继承的事件保持原步骤归属。

消息内容较大时，事件包含 payload_ref，`read_thread_payload(reference, offset=0, size=65536, max_bytes=131072)` 返回 base64 数据、完整原文字节数和 next_offset。分段读取以 JSON 原文字节为单位；应先拼接并解码完整 JSON，UTF-8 多字节字符可能跨段。`resource_tail` 与 `read_resource_payload` 对共享物理资源正文提供相同的追加及分段合同。resource_tail 的 kind 区分 embedding 物理尝试和 embedding_use 逻辑使用，逻辑缓存命中不代表新增收费物理请求。

`read_thread_artifact(thread_id=..., reference=..., actor=..., ...)` 按 Thread 已登记的工件关联读取精确原始字节，并校验主体归属。`result_phases(step=...)` 发现阶段结果引用，`result_page(reference, ...)` 读取指标、表、激活结果与完整头部；单个巨行通过 `read_result_record` 取得。`result_summary()` 使用写入时维护的短计数。完整物理资源用量的按模型、主体汇总投影由 T06 的后续实际消费者接入，当前接口不扫描全历史替代该投影。

这些接口的 max_bytes 约束最终紧凑 JSON 字节数，包含游标、字段和 base64 膨胀；HTTP 成功响应直接返回该对象，不再套额外结果信封。尾页与目录的最小预算为 1,024 字节，结果集页保持 Results 的 512 字节下限。预算容纳不了一个必需身份时明确报错，避免无进展空页。所有游标绑定运行身份；业务恢复分支重新查询，已有同源结果集引用依照 Results 合同仍可读取。完整步骤的派生只读视图使用 `source_run_id:complete:step` 显式身份，同一不可变完整点重建后可继续原游标和正文引用；换步骤或源运行会拒绝旧游标。这一身份约定适用于只读准备视图，业务分叉保持新运行身份。

## 三、视图

`ObservationService` 提供 `prepare_complete(step)`、`preparation_status()` 和 `clear_prepared()`。准备在独立子进程中执行真实 StageStore.restore；状态由 preparing 转到 ready 或 failed。ready 返回 view、source_run_id、step、耗时及准备目录逻辑字节数。查询请求带 `view` 选择该准备视图；不带 view 始终读取原运行 live 数据。原运行 status 与准备操作分离，准备失败或被杀死不会改写其完成身份。

一个服务同时允许一个准备任务和一个 ready 视图。新准备尚未成功时保留旧 ready，忙时返回 preparation_busy；成功后替换旧视图。clear_prepared 取消准备进程、等待退出并删除本服务拥有的构建目录和 ready 目录，失效 view 返回 view_expired。退出服务完成同样清理；关闭后公开准备入口拒绝分配新进程。

这个策略的峰值包含旧 ready 和新 building，且每个恢复目录含 current 与 root 两份数据库，还可能复制完整链所声明的工件。源运行本身也继续占用空间。`logical_bytes` 是完成后的单个准备目录大小，不能代表整个过程峰值；实际大根报告需要另行统计源、ready、building 及 WAL 的峰值。取消按进程终止与退出确认实施，未宣称 SQLite native backup 内部可逐条协作取消。

领域信息通过显式 `information_factory(reader) -> Information` 接入。`query(actor=..., moment=..., path=..., query=...)` 使用提供者声明的数据及权限版本；同表另一主体写入也可能使当前页失效。测试中每页之间持续更新同表另一主体时，20 次续页均明确过期，停止更新后可读完。高更新率下可选择完整视图；不可变消息使用追加序号继续。

`read_document` 沿注册的 DocumentSpec 按字节读取原文。正式 `round_robin_information(reader, name)` 与 `social_information(reader, name)` 和运行机制共用同一组 SQL 路由；Social 只读工厂提供帖子、评论、通知等既有资料，不执行推荐 feed 的嵌入计算或消费曝光。普通 SQLInformation 对选定字段的内存物化仍受提供者实现影响，HTTP 的最终 wire 预算不等于任意插件 provider 的内存预算。超大字段应使用已声明文档范围入口或显式字段投影；通用 SQL 巨字段预检保留为 P04 的独立任务。

## 四、调用

下面的运行路径和 Thread ID 是占位值，需替换为实际运行目录和目录页返回的身份。Python 的追加 cursor 可由调用方保存为 JSON，观察进程重启后继续读取同一运行。

```python
from society0.kernel.observation import Observation

with Observation('/path/to/run') as reader:
    status = reader.status()
    directory = reader.list_threads(actor='alice', limit=20)
    if directory['items']:
        thread_id = directory['items'][0]['id']
        first = reader.thread_tail(thread_id, limit=20, max_bytes=65536)
        later = reader.thread_tail(thread_id, cursor=first['cursor'])
```

CLI 默认输出一次状态。一次历史读取使用 `--complete-step N`，等待准备完成、执行所请求的读取并清理临时视图。每次一次性历史调用都会重新恢复所选完整点；同一完整点的稳定派生身份允许续页与续读，但每次重建的复制成本仍实际发生。连续历史分页与巨正文读取宜使用 Python 服务作用域或 HTTP 服务复用一次准备，避免重复恢复。一次性调用 prepare_complete 等生命周期方法会得到 session_required 错误，并提示服务用法。

```sh
python -m society0.kernel.observation /path/to/run
python -m society0.kernel.observation /path/to/run --complete-step 1 --request '{"method":"list_threads","params":{"limit":20}}'
python -m society0.kernel.observation /path/to/run --serve 8711 --capacity 4 --timeout 5
curl -s http://127.0.0.1:8711/ -H 'Content-Type: application/json' -d '{"method":"thread_tail","params":{"thread_id":"THREAD_ID","max_bytes":65536}}'
```

HTTP 默认绑定本机，每个在途请求占一个工作槽。请求体和响应写入均有 socket timeout；槽满及时返回 503 与 server_busy，结束、超时或断连释放槽。默认请求体上限为 1 MiB、传输响应上限为 16 MiB；单次数据页还受所传 max_bytes 约束。HTTP 不提供无界事件队列，状态轮询得到最新快照，Thread tail 提供持续事实位置。CLI 失败输出 JSON error.code 并以非零状态退出，HTTP 失败使用结构化 error；Python 保留异常调用方式。

外部读取的完整性来自持久身份、追加序号、完整步骤与原文范围。实时性应结合实际可见延迟、失败诊断和资源成本判断；轮询频率、服务启动以及单页成功均不足以代表全部数据已经消费完毕。
