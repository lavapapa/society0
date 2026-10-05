# 真实服务续验

2026-09-29 用户授权使用既有 LLM、embedding 与 Chroma 服务。本记录保存续验过程；最终结果汇总到 [统一验收报告](acceptance-report.md)。最终统一 v3 快照的 14 项真实测试已全部通过，实际大检查点和查询磁盘成本已完成专项测量。

## 一、条件

在 simulation 的独立目录 `/mnt/data/l20/qin/runtime-observation-real-tests-20260929` 部署 `c2ad77b` 快照。首次运行用来确认服务合同和发现真实链路问题；存储与查询优化完成后，使用最终源码重新执行全部测试。测试不修改现有实验、部署或服务。

读取实际服务模型列表后选择 `qwen3.8-27b` 与 `bge-large-zh-v1.5`。embedding 维数为 1024，请求省略 dimensions 参数；LLM 使用既有 `enable_thinking=false`、`parallel_tool_calls=false` 合同。密钥在服务器进程中从既有运行环境配置加载，未拷贝到本机或记录到此仓库。

## 二、范围

执行原有 13 项真实端点测试，保留默认并发 6、测试既有主体数、工具、历史、记忆及 max_turns 配置。新增独立进程测试覆盖真实记忆写入、完整步骤提交、下一步异常、进程退出、新进程恢复、真实向量检索和继续行动，并检查观察服务的提交边界。原测试的总结读取若仍使用旧历史内嵌结构，将按实际失败改用公开完整历史读取 API。

首次 smoke 已通过：1 passed，用时 123.78 秒，首次加载 Ceph 上的 Python 依赖占较长时间。该次首次导入延迟单独保留，未计作模型响应时间。

## 三、发现

首轮 `c2ad77b` 快照执行结果为 6 passed、8 failed、0 skipped，用时 940.08 秒，见 [原始测试记录](real-baseline-tests.txt)。新增跨进程恢复测试通过，两种状态访问模式的真实记忆往返也通过。

三个失败来自真实测试仍直接读取 summary 中已外置的 agent_batches；测试改用公开 `load_run_summary(..., include_history=True)`。另五个失败来自旧测试把动作预算耗尽视作成功：工具已成功发消息、发帖或读取热帖，运行合同正确报告 `action_budget_exhausted` 和未完成。独立审查确认这五个测试均要求“一次指定动作成功后结束”，因此为它们声明对应 `completion_action_tags`，保持动作预算、主体数、业务结果与记忆有效性断言。对应终止原因预期改为 `completion_action_tag`；发帖测试去除额外一次收尾模型调用的旧假设，以每主体至少一次 instruct 请求及实际动作/记忆记录验收。旧失败运行与新成功运行不用于总体提速比较。

新增确定性负例 `test_nonterminal_budget_exhaustion_never_becomes_success_memory` 验证一次成功工具调用后预算耗尽仍为 error/incomplete，允许召回但不写成功记忆，且不追加收尾请求。独立复验见 [合同审查](real-contract-independent-20260929.txt)。该负例现同时覆盖输出截断。测试合同及产品行为已经独立复验。


## 四、结果

中间 v2 `source-revision2` 快照执行 **14 passed、0 skipped，254.09 秒**，见 [逐项结果](real-verified-results.json)、[原始记录](real-verified-tests.txt)及[阶段时间](real-verified-timing.json)。原有 13 项和新增的独立进程恢复测试全部实际执行，覆盖两种状态访问模式、真实工具调用、记忆提取、embedding、Chroma 落盘与新进程召回，以及未提交步骤和已提交 Thread 的边界。

最终成功前的一轮为 12 passed、2 failed，见[保留记录](real-revision1-tests.txt)。其中诊断字符串断言改为分别核对 required_actions 与 completion_action_tags；另一项测试的发布和浏览分别触及原 80、120 token 输出上限，提供方返回 length，运行按未完成处理。用户随后明确批准将该成功链测试的提供方输出配置设为 256，动作数、max_turns、业务结果和记忆断言保持。此调整属于测试运行前提，不作为性能收益，也未继续任何旧失败运行。确定性负例同时证明 length 和动作预算耗尽均不进入成功记忆。

11 次引擎运行的初始化累计 69.429 秒，其中 Chroma 创建累计 58.027 秒；root 发布累计 16.676 秒，总结累计 20.782 秒。关闭 persistence 和模型资源分别累计约 0.001、0.004 秒。50 次受插件观测的逻辑 LLM 请求累计 89.011 秒，30 次逻辑 embedding 请求累计 37.215 秒；存在并发与 embedding 合批，累计请求时间和数量不能当作串行墙时或物理请求数。带运行日志的部分另记录 46 次 LLM、14 次 embedding 成功，无失败；直接 manager 测试和子进程与插件的覆盖范围不同，详见机器结果中的资源日志计数。

这组真实服务测试支持提供方参数和恢复链正确性的判断。性能前后对照采用相同成功语义的多步确定性 Agent、存储和查询基准；旧失败运行与新成功运行的总耗时不作提速比较。最终提交身份将在统一验收报告中关联部署源码的逐字节核对记录。

在真实 saturation 工件副本上重算总结耗时 2.560 秒：8 次数据集写入累计 2.480 秒，内部 16 次 fsync 累计 2.334 秒；主体、事件和资源统计均小于 5 毫秒，输出文件统计约 66 毫秒。此样本将主要等待定位到多个小数据集的耐久写入，见[细分结果](real-summary-stages.json)，后续优化仍需同语义复验。


总结单容器的同工件复测为 0.149 秒，单次容器发布约 0.059 秒，两次 fsync 累计 0.036 秒，见[单容器结果](real-summary-single-container.json)。这是保留原 v2 编码、单独替换总结模块的隔离对照，未修改已经完成真实测试的部署快照。同步次数下降具有确定性，墙时仍受 Ceph 当时延迟影响。最终 v3 与查询布局变化已按下节完成全部真实测试。


第二次单容器重算还在计时外，将 agent_operations、resources、events 的完整历史与原真实工件逐值比较，三者一致；此次墙时 0.288 秒，仍为一次容器写入、两次 fsync。见[真实内容等值](real-summary-content-equivalence.json)。两次样本的变化也说明 fsync 延迟存在波动。


## 五、最终快照

独立部署 `source-final-v3` 完成 **14 passed、0 skipped、234.71 秒**，见[原始日志](real-verified-v3-tests.txt)、[机器结果](real-verified-v3-results.json)与[阶段计时](real-verified-v3-timing.json)。此版本包含 v3 字典及顺序元数据流、最后分页字节预算修复、直属集合索引、并发只读 HTTP 和单容器总结。下载实际部署的 src/tests 后，其文件集合和逐字节内容均与本报告所在提交一致，记录见[源码核对](deployed-source-v3.json)。

最终测量中，11 次初始化累计 52.43 秒，Chroma 创建占 43.58 秒，root 发布累计 22.66 秒，总结累计 4.68 秒。总结内主体、事件、资源聚合累计分别为 0.0066、0.0116、0.0199 秒，输出文件统计累计 0.409 秒。LLM 与 embedding 插件逻辑请求分别为 50/30 次、累计 67.78/50.71 秒；运行资源日志中分别有 46/13 次成功，零失败。不同覆盖范围与合批口径继续按第四节解释，不推导整体提速比例。

全部模型与 embedding 参数、动作和记忆行为保持本轮已审查的成功 profile。测试使用独立目录，原始失败、已有实验和共享服务保持原状。最终软件、性能边界和交付身份统一见验收报告。
