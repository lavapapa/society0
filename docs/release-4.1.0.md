# Society0 4.1.0

## Agent 回合

- OpenAI-compatible 响应保留 `finish_reason`，`length` 会成为明确的主体级
  激活失败，不再被解释为正常的无行动回合。
- 写入 Agent Thread 的对话改为逐条追加，provider 请求只记录消息数量、工具
  名称和有效参数，避免多轮对话反复复制完整上下文。

## Thread 与记忆

- Agent Thread schema 升级到 v2，删除逐事件 payload hash 和事件哈希链；关闭
  Thread 的文件哈希及大型 blob 的内容哈希继续承担检查点恢复校验。
- 普通事件不再逐条 `fsync`；记忆 pending、记忆 receipt 和 Thread close 仍是
  明确的持久化边界。
- 记忆 pending 与 receipt 不再各自复制完整对话和 `full_history`。恢复继续使用
  稳定 memory id，并从 Thread 的逐条消息重建上下文。
