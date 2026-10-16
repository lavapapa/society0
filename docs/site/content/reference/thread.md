# ThreadStore 与模型消息

`from society0.kernel.threads import ThreadStore`。ThreadStore 在共享 StageStore 里保存会话身份、完整消息、请求水位、工具回执和认知位置；传入 StageReader 时用于只读观察。正式装配时由 thread_plugin 声明 schema，ThreadStore 直接借用同一个 StageStore，自己不建表，也没有独立连接。

## 会话的开启、结束与消息

会话身份、消息和终态都记在共享存储里。

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

工具回执按 call_id 复用，内容对不上会被拒绝。登记工件时会同时核验主体，并把工件纳入完整步骤的依赖；读取已有回执或输出不会重新执行原来的动作。

## 分页观察与读取开销

消息正文放在独立的压缩块里，目录、短元数据和追加水位从当前索引取得。

<!-- api: society0.kernel.threads.ThreadStore.tail -->
<!-- api: society0.kernel.threads.ThreadStore.read_payload -->

tail 返回 items、next_seq 和 total，正文较大时给出 payload_ref；read_payload 按原始 JSON 字节定位到压缩块。重建完整模型历史和原请求时，内存占用仍随所选取的全部内容增长。Thread 没有独立检查点，完整步骤覆盖共享事务；未完成步骤里已经落盘的消息只保留作诊断，恢复只包含选定的完整点。

```python
# threads 是已装配的 ThreadStore，thread_id 来自目录或驱动结果。
def inspect_messages(threads, thread_id):
    return threads.snapshot_messages(thread_id, raw=True)
```

持续观察见[Observation](observation.md)，生命周期见[完整认知](../concepts/cognition.md)。源码入口为 [ThreadStore](https://github.com/lavapapa/society0/blob/@COMMIT@/src/society0/kernel/threads.py#L80)。
