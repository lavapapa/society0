# Society0 3.0.0 发布说明

3.0.0 将可恢复检查点硬切换为 `complete_step_v3`。新格式只保存一份权威
World/Environment/Agent 状态，不再在 observation 和 Environment snapshot 中重复整个
World。旧 `complete_step_v2` 不由 3.0.0 恢复；历史实验应使用当时固定的
Society0 版本读取。

检查点写入还有三项改变：

- JSON/gzip 编码在 worker thread 中执行，不再长时间阻塞 async 事件循环。
- 压缩字节的 SHA-256 和长度在写入时增量计算。保存时不再重读压缩文件，
  也不再立即解压整个 World 来复核 id/step；恢复时仍完整验证。
- 取消或失败发生在 World rename 后、complete marker 发布前时，仅清理本次未引用组件，
  已发布的旧检查点保持不变。

Provider 故障也改为更细的恢复粒度：连接和超时错误先重试当前物理请求；
空响应在当前主体激活中重试；工具参数错误返回同一 Agent 修正。重试耗尽后
返回结构化的主体级错误。World writer、Thread 持久化、checkpoint 和状态不变量错误
继续作为整个 step 的失败边界。

Chroma 备份格式本次未改变。多个检查点不会直接共用可变的 live Chroma 目录。
