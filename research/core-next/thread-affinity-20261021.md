# Thread 服务选择开发记录

本次补丁让同一 Thread 在配置不变时持续请求同一个推理实例，并将重复响应提前结束改为显式启用。工作在独立分支完成，供父智能体选择；尚未合入、推送或部署。

## 一、基点与方案

2026-10-21 从 origin 拉取 next 后，权威工作树及 origin/next 均为 `412657e133f2e9323e311c1640b4dc77a5c2e219`。开发分支为 `task/core-thread-affinity-20261021`，工作树位于 workspace 的 `.codex-worktrees/core-thread-affinity-20261021`。复用原 Core 的本地 Python 环境，以 PYTHONPATH=src 强制使用开发工作树源码；依赖和锁文件保持原样。

检查了 ThreadStore 的创建、describe、close、reopen、检查点恢复，以及决定与记忆提取的 request_model 调用。已有持久字段 provider_session_id 允许调用方传入任意字符串，用作提供方会话身份；id 是 Thread 身份，ordinal 是累计序号。它们均保留原用途。关闭后同一 Thread 可以重新打开，提供方也可以重新创建，单纯在 ModelProvider 中保存运行期映射无法覆盖这两个路径。

最小方案：在现有 thread_heads 中增加一个 model_route REAL 字段，创建 Thread 时均匀抽样一次。每个 ModelProvider 初始化时预计算配置端点的累计权重，请求读取原本就要读取的 Thread 头，将固定抽样点映射到累计权重区间。区间长度为该端点权重除以总权重，因此新 Thread 的选择概率保持原有权重比例。端点表 O(E)，每次选择 O(log E)；没有按全部历史 Thread 维护的内存绑定表，也没有历史扫描。每个 Thread 的持久记录增加一个常数大小数值。

原始提供方参数、session_id、端点与共享并发许可、重试、错误留证和取消清理路径保持原有实现；EmbeddingProvider 继续逐批加权抽样。所有 profile 共用该 Thread 的抽样点，分别按自身权重区间解释；不同 profile 的抽样结果因而可能相关。权重控制 Thread 分配比例，实际流量受各 Thread 工作量影响。没有哈希、指纹或从标识符派生路由。

## 二、预算与验证

原默认 repeated_response_limit=4 在第五个连续相同响应时触发 incomplete，早于显式 max_turns=64。该默认确实缩紧推理预算，现改为 None；显式配置仍可启用。Core 的 max_turns 默认原本就是 None，本次保留；调用方配置 64 时完整保留该预算。

执行命令（从开发工作树）：

```sh
PYTHONPATH=src ../../research/simulation/society0core/.venv/bin/python -m pytest tests/primary/test_thread_model_affinity.py tests/primary/test_kernel_models.py tests/primary/test_kernel_threads.py tests/primary/test_kernel_llm.py -q
```

69 项通过，git diff --check 通过。新增 4 项直接测试覆盖：1:3 权重区间边界、多主体并发、同 Thread 续轮、关闭重开、检查点恢复与提供方重建、任意 provider_session_id、提供方原始参数、503 重试耗尽及再次请求保持端点、默认重复响应运行至第 64 轮，以及显式 limit=4 的既有第 5 轮终止行为。原有相关测试同时覆盖容量和关闭取消等路径。初轮两处测试断言问题已经修正：并发结果不保证到达顺序，Thread tail 默认分页无法一次读到 64 轮后的关闭事件。

按委托限制，未运行真实模型、部署操作或全仓测试；没有修改模型、工具或历史预算。当前为叶子智能体，无法启动独立审查子智能体。测试验证的是服务选择与语义；实际 KV 命中率、两实例负载和墙钟收益需要部署后的实验数据。

## 三、采用条件

Thread 存储新增列，本补丁应从新运行创建状态。旧版本创建的检查点缺少 model_route，未提供迁移或静默重抽样。用本版本创建的完整检查点已验证恢复后保持选择。配置中的端点顺序和权重必须在同一次运行及恢复中保持一致；改变配置可能改变映射。服务故障仍按原逻辑重试后失败，没有增加跨端点故障转移。

父智能体可以在核对新运行安排后选择本补丁，预算默认修复可独立选择；现有部署和运行工件均保持原状。
