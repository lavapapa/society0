# 新版真实套件合同

本组在真实服务调用前冻结验收脚本。`tests/e2e/test_core_next_real.py` 包含原 13 项语义的新 Core 消费者、独立进程中断恢复和 V06 信息发现任务，共 15 项。当前离线结构证据使用确定响应和本地 HTTP 服务，覆盖正式插件、提供方适配器、SQLite、Chroma、原生 Shell 与进程恢复；该证据不代表外部模型已经完成任务。

## 一、运行

入口使用 `SOCIETY0_RUN_CORE_REAL=1` 显式启用。提供方参数来自 `SOCIETY0_REAL_LLM_URL/MODEL/KEY` 与 `SOCIETY0_REAL_EMBED_URL/MODEL/KEY`；运行目录与代码身份分别使用 `SOCIETY0_REAL_OUTPUT`、`SOCIETY0_REAL_RELEASE`。模型端点使用 `SOCIETY0_REAL_LLM_TRUST_ENV=1` 时继承已授权的代理环境，此选择进入公开 profile；默认值 0 使用直连。内网 embedding 保持直连。凭据仅在进程内进入 SDK，runner 清单记录环境变量名称、去凭据后的端点参数以及实际依赖版本。

正式执行前须由部署步骤确认干净且已推送的提交，并逐字节比较实际部署的 src/tests；RELEASE 字符串本身不构成此证据。每次尝试使用新的输出根目录。已有案例目录会被拒绝，失败原始 Thread、SDK 区间、未完成步骤与进程日志保留。测试不通过切换目录延续或增加既定尝试次数；修改合同后的新运行须说明变化与前次失败。

## 二、预算

本次任务包含动态发现、完整参数查询与记忆提取。预先设计的默认 profile 为 max_tokens=1024、temperature=0、parallel_tool_calls=false、reasoning_effort=minimal，物理尝试上限为 1、端点超时 60 秒；它与此前 256 token 小探针分属不同合同。普通领域与访谈激活最多 8 次模型请求、4 次领域行动，V06 最多 20 次请求、2 次领域行动。正常完成直接结束，达到硬预算或截断保留 incomplete。

默认模型许可 2，embedding 许可 2；显式并发覆盖案例单独将模型许可固定为 1、激活许可为 3。SDK 调用的时间包裹只记录开始、结束与并发数量，调用原方法并原样返回。真实并发案例要求激活重叠和 SDK 重叠实际发生，覆盖案例要求 SDK 峰值严格为 1。区间保存在各运行目录 sdk-intervals.json；物理请求、响应、用量和原始参数仍来自 Thread/ResourceCalls。

## 三、判断

真实用例通过正式 thread_plugin、model_plugin、embedding_plugin、actor_plugin 安装期驱动工厂、memory_plugin、runtime_plugin 与 CodeSchedule 装配。社会网络保持默认语义推荐，接入真实 embedding 与独享 Chroma 客户端；完成或失败退出后调用安装版本支持的公开 client.close。

V06 从共享根目录发现资料，完整读取 12 条分页报价，按 64 字节续读 UTF-8 正文，通过 Bashkit 中的 jq 复算 count 与 total，再自主发现、描述并执行正式行动。验收从 Thread 的实际调用与成功工具反馈核对连续四页：每页 limit=3、total=12，下一次请求携带上一页 next_cursor，记录身份与金额依次覆盖全部 12 条且末页游标为空。权威提交结果应为 count=12、total=546 和从原文读取的核对短语；Thread 保留全部中间调用与工具反馈。允许模型根据明确工具反馈纠错，错误反馈另存，不替换或删除原始记录。

完整点记忆回退案例按记忆身份逐项比较恢复前后的正文、类型、时间、重要性、可见步骤与原始向量字节，确认第一步记忆完整保留，未发布的第二步 600 元报价未进入恢复记忆。跨进程案例在第二步模型和记忆处理后直接退出，验证第一步完整点、第二步 live 诊断、恢复后原 Thread 原文和召回内容，并在新进程完成第二步。实际服务通过与确定性结构通过分别统计，最后的发布结论依赖同一冻结代码的真实执行结果。
