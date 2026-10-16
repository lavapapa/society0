# ThreadStore 与模型消息

`from society0.kernel.threads import ThreadStore`。ThreadStore 在共享 StageStore 中保存会话身份、完整消息、请求水位、工具回执和认知位置；传入 StageReader 时用于只读观察。

## 一、会话与消息

正式装配使用 thread_plugin 声明 schema。open 返回稳定 Thread ID，append_message 返回该 Thread 内单调事件序号。

<!-- api: society0.kernel.threads.ThreadStore -->
<!-- api: society0.kernel.threads.ThreadStore.open -->
<!-- api: society0.kernel.threads.ThreadStore.append_message -->
<!-- api: society0.kernel.threads.ThreadStore.close -->
<!-- api: society0.kernel.threads.ThreadStore.reopen -->

close 接受 completed、waiting、incomplete，追加终态事件并维护状态。reopen 保留原 Thread 与 provider_session_id，旧结果仍可追溯；驱动决定是否允许继续。`find(actor, moment, kind)` 通过持久索引定位同一认知时点的最新会话。

消息要求 JSON 对象，原角色字段和提供方附加字段保留。非法编码使本次事务回滚。模型响应以 SDK typed 消息保存，opaque reasoning、签名与工具 namespace 进入原始快照；角色视图服务于研究读取。

## 二、完整请求与认知位置

历史完整性贯穿每次模型请求。分页服务于观察和传输，LLMDriver 不据此裁剪历史。

<!-- api: society0.kernel.threads.ThreadStore.snapshot_messages -->
<!-- api: society0.kernel.threads.ThreadStore.read_messages -->
<!-- api: society0.kernel.threads.ThreadStore.read_request -->
<!-- api: society0.kernel.threads.ThreadStore.append_input -->
<!-- api: society0.kernel.model_messages.history -->

snapshot_messages 在短读内返回 messages 与 through；raw=True 保留 typed JSON。read_messages 默认返回完整角色视图。read_request 重建原请求选定水位的完整输入，后来追加内容不会进入旧请求；物理重试仍引用同一水位。

append_input 将整批消息、consumer 游标及可选 context 同事务保存。context 省略时保留旧上下文引用。认知来源 effects 在输入保存成功后执行，使实际呈现与消费位置具有明确先后关系。

工具回执按 call_id 复用，内容冲突被拒绝。工件登记同时验证主体并纳入完整步骤依赖；读取已有回执或输出不重新执行原动作。

## 三、观察与成本

正文使用独立压缩块，目录、短元数据和追加水位通过当前索引取得。

<!-- api: society0.kernel.threads.ThreadStore.tail -->
<!-- api: society0.kernel.threads.ThreadStore.read_payload -->

tail 返回 items、next_seq 和 total，大正文提供 payload_ref；read_payload 按原始 JSON 字节定位压缩块。完整模型历史和原请求重建仍随所选全部内容增长，需要相应内存。Thread 无独立检查点，完整步骤覆盖共享事务；未完成步骤已落盘消息保留诊断，恢复仅包含选定完整点。

```python
# threads 是已装配的 ThreadStore，thread_id 来自目录或驱动结果。
def inspect_messages(threads, thread_id):
    return threads.snapshot_messages(thread_id, raw=True)
```

持续观察见[Observation](observation.md)，生命周期见[完整认知](../concepts/cognition.md)。源码入口为 [ThreadStore](https://github.com/lavapapa/society0/blob/@COMMIT@/src/society0/kernel/threads.py#L80)。
