# 独立交付审查：2026-10-04

本次独立审查围绕完整历史、恢复后的动作与信息、真实缓存计量、成熟 Agent 主循环以及外部消费者展开。以下证据对应 `source-candidate.tar.gz` 冻结副本。该副本包含最后的社交帖子 `content_path` 信息可用性修复；双平台、清源 wheel、外目录教程与 Node 消费均重新绑定此副本。

## 一、源码

冻结副本来自 `codex/society0-core-next` 工作树，谱系提交为 `0ae6f7dd3a5baac4fbaacc272c500c0cbcb0e4d0`，包含已经接受的未提交修改。可复现身份记录在 [source-candidate-identity.json](source-candidate-identity.json)，实际输入保存在 [source-candidate.tar.gz](source-candidate.tar.gz)。提交号用于说明谱系，验收实际读取归档中的源码。

该副本包内有 47 个 Python 文件、11201 行物理文本，其中 kernel 为 31 个文件、7975 行；测试为 167 个文件、16696 行。kernel 是包内子集，行数包含注释与空行。`llm.py` 为 647 行，谱系 HEAD 为 617 行，因此本轮没有减行结论。Git 状态记录中，产品及依赖、指南及示例、测试及基准、研究工件分别有 29、41、85、26 个状态条目；未跟踪目录按一条计数。完整记录见 [candidate-source-scale.json](candidate-source-scale.json)、[modification-categories.json](modification-categories.json) 与 [git-status.txt](git-status.txt)。

## 二、验收

macOS 确定性 primary 与 e2e 共 808 项通过、15 项真实用例排除，用时 35.14 秒；experiments 75 项通过，用时 2.88 秒。Linux 使用相同归档及既有依赖环境，分别为 808 项通过、15 项排除、54.81 秒和 75 项通过、4.50 秒。日志分别为 [candidate-macos.txt](candidate-macos.txt)、[candidate-experiments-macos.txt](candidate-experiments-macos.txt)、[candidate-linux.txt](candidate-linux.txt) 与 [candidate-experiments-linux.txt](candidate-experiments-linux.txt)。两平台复验同一批 883 项 Python 确定性用例，计数不相加。

正式 runner 实际生成两步运行工件，再导出工作台载荷供 Node 消费，9 项通过、0 项跳过。相关证据见 [social-final-pilot-runner.txt](social-final-pilot-runner.txt)、[social-final-workbench-payload.json](social-final-workbench-payload.json) 与 [social-final-node.txt](social-final-node.txt)。

安装验收发现既有工作树的构建目录残留：初次 wheel 包含 90 个 Python 文件，其中 43 个已经从源码删除的旧文件。红灯保存在 [wheel-pollution-red.json](wheel-pollution-red.json)。从冻结归档新解压目录构建后，wheel 的 47 个文件与归档源码逐文件直接字节比较相等，证据为 [social-wheel-build.txt](social-wheel-build.txt) 与 [social-wheel-source-equality.json](social-wheel-source-equality.json)。新的基础环境仅安装该 wheel，外目录消费者按入门文档执行准备、运行、结果读取及恢复第二步；导入来自安装目录，旧模块及可选模型依赖均未混入。见 [base-tutorial-social-bound.txt](base-tutorial-social-bound.txt)、[social-base-install.txt](social-base-install.txt) 与 [social-final/society0-6.0.0-py3-none-any.whl](social-final/society0-6.0.0-py3-none-any.whl)。

## 三、判断

`Agent.iter` 实际接管工具推进，工具回调执行领域动作；ThreadModel 小桥将 SDK 持有的消息列表借给唯一物理 provider 路径并返回原始 typed response。长历史独立反例使用完整多轮原文和同一时点的检查点恢复，断言实际传输前缀、typed 原文、线程身份以及重复 call_id 的领域副作用至多一次。每次激活物化一次历史，provider 没有再创建完整历史副本。完整历史在激活及工具等待期间持续驻留，这是适配成本；本轮没有同源吞吐收益声明。

Ledger 保留业务资格、动作副作用、重复回执、整批预算与完成状态的领域语义。严格 schema 规范化仍维护可选字段、nullable 与 enum 等合同，直接替换为 SDK 的必填字段变换会改变语义。记忆提取仍使用原有两请求纠正与解析协议，其强制提取工具、截断拒绝及预算尚未整体迁移到 SDK structured output。本轮以明确所有权释放 role 快照，独立弱引用反例先红后绿，确保提取器物理请求前释放冗余完整历史；自定义提取消费者仍收到完整原文。见 [memory-snapshot-red.txt](memory-snapshot-red.txt)、[memory-snapshot-green.txt](memory-snapshot-green.txt)。

缓存独立测试直接消费成熟 SDK 的多 chunk SSE，覆盖累计与增量用量、缓存缺失与显式零、实际缓存数、主体归属及恢复，共 6 项通过，见 [stream-usage.txt](stream-usage.txt)。SDK 默认累加适用于增量字段；累计端点应使用已有 `openai_continuous_usage_stats=True` 配置。缺失缓存字段保持未报告，显式零保持已报告零。SDK 的 total 是输入与输出之和的规范化属性，原始服务 total 没有保留，本轮沿用该口径。

社交增量复用既有页面 `_items` 物化，在帖子元数据中附上与 feed 一致的正文路径。实时与只读工厂共用该入口，没有新增 SQL、正文加载或权限算法。六项失败先行用例覆盖元数据不加载冷正文、投影字段、权限、字节分页及完整点恢复，最终全量包含这些断言。

最终 candidate 的 47 个产品 Python 文件与已完成安装及 Node 验收的合法 wheel 文件逐字节相等，见 [复用身份核对](candidate-wheel-source-equality.json)。因此复用产品相同的安装与工作台证据。导出名经标准 wheel 名称解析器核查；最终可安装文件位于 `social-final/society0-6.0.0-py3-none-any.whl`。构建污染红灯保留，已按授权删除本工作树生成的 build/lib，原运行工件保持保存。

## 四、边界

真实断言已加强到 assistant 实际回答与已完成行动：恢复事实核对 assistant 内容，浏览核对实际领域动作及市场正文，多步社交核对作者、时点与正文。此前 browse 达到既有 max_turns 的失败保留。最终社交正文入口修复后，social_publish、browse、multi_tick 三项实际复验通过，保持原预算和实际行动断言。VFS 运行完成且提交 count=12、total=546；报告源定义短语为 `原文校验成功。`，旧期望遗漏句号，模型提交精确保留句号，因此首断言失败。两位独立审查者认定应从报告的明确换行标记后取得完整尾串作为精确期望。真实测试与离线脚本现共享 `assert_vfs_artifacts`，现有工件全部后续断言已通过，额外验证两次范围读取拼接与 117 字节正文逐字节相等；新增提供方调用为零，没有去除标点或多格式容忍。证据见 [oracle 复核](../provider-acceptance-20261004/vfs-oracle-review.md)与 [原始结果](../provider-acceptance-20261004/vfs-oracle-review.json)。最终 candidate 归档已包含 oracle 修正、共享断言函数及离线消费者更新；双平台非真实主组与实验组均以该身份重验。15 个真实场景的证据来自有明确源码版本的多次运行，其中 VFS 为同工件精确 oracle 复核，整体映射见 [提供方最终报告](../provider-acceptance-20261004/final-report.md)。它们构成组合验收证据，不能称为同一源码的一次全绿真实测试。

记忆消融采用同一恢复问题的一对实际回答：关闭自动召回时回答资料不足，开启时肯定回答 B42 和已收 500 元。该成对结果支持召回在本样例中的可用性，单个样例无法估计统计效果或长期稳定性；完整原文与用量见提供方最终报告。

本轮两个工件目录各自的 `.gitignore` 精确排除数据库 wal/shm/journal、运行 writer.lock 和 vectors 缓存目录。原始文件保持本地保存，uv.lock 与有证据意义的 JSON 保持可提交。

本报告证明该归档的确定性行为、平台运行和干净基础安装；正式发布仍需要干净且身份明确的提交与推送。后续产品修复会产生新的验收对象，当前 wheel 和平台结果保留为这一版本的证据。

当前最有价值的交付判断是：成熟 Agent 已实质接管推进，完整历史和恢复合同得到独立验证，缓存统计的端点口径得到识别，干净打包发现并消除了构建残留。真实浏览已经在既有预算内完成；VFS 的精确源定义与全部后续断言也已由现有真实工件复核通过。
