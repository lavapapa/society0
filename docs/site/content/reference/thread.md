# ThreadStore 与模型消息

`from society0.kernel.threads import ThreadStore`。ThreadStore 在共享 StageStore 里保存会话身份、完整消息、请求水位、工具回执和认知位置；传入 StageReader 时用于只读观察。正式装配时由 thread_plugin 声明 schema，ThreadStore 直接借用同一个 StageStore，自己不建表，也没有独立连接。

## 会话的开启、结束与消息

<!-- api: society0.kernel.threads.ThreadStore -->
<!-- api: society0.kernel.threads.ThreadStore.open -->
<!-- api: society0.kernel.threads.ThreadStore.append_message -->
<!-- api: society0.kernel.threads.ThreadStore.close -->
<!-- api: society0.kernel.threads.ThreadStore.reopen -->

open 返回一个稳定的 Thread ID，append_message 返回该 Thread 内单调递增的事件序号。close 接受 completed、waiting、incomplete 三种终态，追加终态事件并维护状态。reopen 保留原来的 Thread 和 provider_session_id，旧结果仍然可以追溯；是否允许继续由驱动决定。`find(actor, moment, kind)` 通过持久索引找到同一个认知时点上最新的会话。

每条消息必须是 JSON 对象，原来的角色字段和提供方附加的字段都会保留。编码不合法会让这次事务回滚。模型响应按 SDK 的 typed 消息保存，opaque reasoning、签名和工具 namespace 进入原始快照；角色视图供研究读取使用。

## 完整请求与认知位置

每次模型请求都基于完整历史；分页只服务于观察和传输，LLMDriver 不会拿它裁剪历史。

<!-- api: society0.kernel.threads.ThreadStore.snapshot_messages -->
<!-- api: society0.kernel.threads.ThreadStore.read_messages -->
<!-- api: society0.kernel.threads.ThreadStore.read_request -->
<!-- api: society0.kernel.threads.ThreadStore.append_input -->
<!-- api: society0.kernel.model_messages.history -->

snapshot_messages 在一次短读里返回 messages 和 through；raw=True 时保留 typed JSON。read_messages 默认返回完整的角色视图。read_request 会按原请求选定的水位重建完整输入，后来追加的内容不会进入旧请求；物理重试仍然引用同一个水位。

append_input 把整批消息、consumer 游标和可选的 context 放在同一个事务里保存；省略 context 时沿用旧的上下文引用。认知来源的 effects 在输入保存成功之后才执行，实际呈现和消费位置因此有明确的先后。

## 输入引用、模型路由与端点亲和

辅助请求需要读取另一条 Thread 已经保存的前缀时，请求事件只记地址，不复制正文。

<!-- api: society0.kernel.threads.ThreadStore.read_messages_by_reference -->
<!-- api: society0.kernel.threads.ThreadStore.model_route -->
<!-- api: society0.kernel.threads.ThreadStore.last_provider_endpoint -->

`record_request` 与 `record_provider_request` 接受可选的 `prefix` 与 `suffix`，每项是 `{thread_id, after, through}` 的持久化消息范围，必须属于同一主体、落在该 Thread 已保存的区间内。请求事件保留这两个引用，`read_request` 同时返回引用和按引用展开后的 messages，正文在物理请求取得许可后才物化：被引用的消息仍只有一份，用量也只记在真正发出请求的 Thread 上。给出引用时 `through` 仍是本次请求固定水位；普通请求不写这两个键，事件形状与以前一致。

`open` 的 `model_route` 可以显式给定，取值范围 `[0, 1)`，省略时才在本次抽样点生成并持久化。同一 Thread 的后续请求、续会和恢复都用这个值解析端点。

端点亲和按提供方作用域保存：`record_model_response` 在写入响应事实的同一个事务里更新 `thread_provider_endpoints` 的当前投影，`last_provider_endpoint(thread_id, provider=...)` 是一次主键查找，不扫描响应历史。同一个 Thread 上记忆提取使用的另一 profile、同一个 Moment 显式换上的提供方都是合法追加，各自保留自己的证据；决策提供方续会只看自己的那一行。旧端点被当前配置删除时投影仍是旧 id，`verify_route` 因此明确报告 `model_route_unavailable`，不会静默改投。具名装配由 `model_plugin` 绑定 `models.profile` 作用域；独立构造的提供方可以调用 `bind_scope` 声明身份，未声明时用空串作用域，表示该 Thread 只有一个未具名提供方，沿用原来的单提供方合同。作用域是显式字符串，不取自对象身份，也不用历史兜底。

工具回执按 call_id 复用，内容对不上会被拒绝。登记工件时会同时核验主体，并把工件纳入完整步骤的依赖；读取已有回执或输出不会重新执行原来的动作。

## 分页观察与读取开销

消息正文放在独立的压缩块里，目录、短元数据和追加水位从当前索引取得。

<!-- api: society0.kernel.threads.ThreadStore.tail -->
<!-- api: society0.kernel.threads.ThreadStore.read_payload -->

tail 返回 items、next_seq 和 total，正文较大时给出 payload_ref；read_payload 按原始 JSON 字节定位到压缩块。重建完整模型历史和原请求时，内存占用仍随所选取的全部内容增长。Thread 没有独立检查点，完整步骤覆盖共享事务；未完成步骤里已经落盘的消息只保留作诊断，恢复只包含选定的完整步骤。

```python
# threads 是已装配的 ThreadStore，thread_id 来自目录或驱动结果。
def inspect_messages(threads, thread_id):
    return threads.snapshot_messages(thread_id, raw=True)
```

持续观察见[Observation](observation.md)，生命周期见[完整认知](../concepts/cognition.md)。源码入口为 [ThreadStore](https://github.com/lavapapa/society0/blob/@COMMIT@/src/society0/kernel/threads.py#L80)。
