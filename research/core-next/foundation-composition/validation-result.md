# 基础组合验证记录

本文保存本任务实际执行证据。完整 E2E、成本和最终全量结果尚待对应任务执行。

## 一、环境

解释器与基线见[实施记录](implementation-plan.md)。测试使用任务工作树的 src，清除八项 HTTP/HTTPS/ALL/NO_PROXY 大小写变量，SOCIETY0_RUN_REAL_E2E=0、SOCIETY0_RUN_CORE_REAL=0，pytest 排除 real_e2e。测试期间工作树允许本任务未提交差异。没有真实端点调用。

## 二、T01

失败先行：执行 `python -m pytest tests/primary/test_registration_ownership.py -m 'not real_e2e'`，退出 1，共 10 个失败。原因包括旧 mount/register 返回 None、便捷 register 缺少 on_close 参数和部分安装时已登记项无法独立撤销，未出现测试收集或缺依赖失败。原记录：[t01-red.log](evidence/t01-red.log)。

实现后相同文件退出 0，10 passed；见[t01-green.log](evidence/t01-green.log)。覆盖独立注销、同对象重挂载、旧 handle、owned/borrowed、FTS/tags、共享依赖引用、generation 与游标、部分安装失败、作用域错误/取消，以及真实 RR/Social register 的中途错误资源托管。

相关回归：registration_ownership、kernel_interaction、kernel_interaction_review、kernel_composable、plugin_round_robin、plugin_social、social_composition、kernel_domain_revisions、kernel_record_plugin、skill_starter、kernel_memory、core_next_pilot，共 159 passed，退出 0。见[t01-focused.log](evidence/t01-focused.log)及[t01-focused.xml](evidence/t01-focused.xml)。完整命令可由上述文件列表组成 `python -m pytest ... -m 'not real_e2e'`，PYTHONPATH 取任务 src 和根目录。

## 三、T02

初次委托/工件测试 4 failed，另有两个原有测试通过；缺口准确落在 ActorStore 取 directory.store 和 find 调用 Thread 私有路径。另行直接运行原生只读 select，复现 StageReader 没有 run_id，1 failed。见[t02-red.log](evidence/t02-red.log)、[t02-readonly-red.log](evidence/t02-readonly-red.log)。

实施后，相关回归包括 actor_directory_delegation、kernel_actors、kernel_actors_review、kernel_actor_fields、kernel_actor_fields_review、kernel_actor_files、kernel_actor_files_stateful、kernel_composable、kernel_selection 和 kernel_llm，共 111 passed，退出 0。见[t02-focused.log](evidence/t02-focused.log)与[t02-focused.xml](evidence/t02-focused.xml)。覆盖真实 SQL 无 store 门面、完整委托、驱动创建与标签拒绝、persona 独立门面、当前/固定只读分页、按键惰性字段、中文长结果跨 buffer、空结果、跨主体拒绝、scope 失效和完整点恢复。既有成本测试的插桩从显式 storage 服务取得，原断言保持。

## 四、T03

新清单用例最初 2 failed：format 仍为 1，Host 缺少公开描述。见[t03-red.log](evidence/t03-red.log)。实现 PluginDescription 与 Host 描述、runner format2 后，runner/plugins/plugins_review/composition/composable/core_next_workbench/core_next_workbench_review 共 79 passed，退出 0。见[t03-green.log](evidence/t03-green.log)、[t03-green.xml](evidence/t03-green.xml)。

验证同根但子树不同、递归 includes、注入 storage、服务与 schema 两类直接边、不可变值、关闭后静态可读、无资源序列化和工作台导出。原双图 I/O 前失败用例继续通过。首次相关命令误用不存在的 kernel_workbench 文件名，pytest 退出 4 且未执行测试；[t03-focused.log](evidence/t03-focused.log)记录该命令错误，随后改用实际 core_next_workbench 文件执行，以上 79 项为有效结果。真实内置/外部 ranker 配方差异将在 T05/T09 另行覆盖。

## 五、T04

真实 Runtime/Semantic 在 activation 内请求与 after-step flush 两个边界使用 Event 暂停。旧全局 LIFO 在 Runtime 早安装的合法图中先 worker.stop 再 runtime.idle，两个顺序用例失败；Runtime 晚安装的两个用例通过。另有 stage/错误清理用例失败，共 6 failed、2 passed。见[t04-red.log](evidence/t04-red.log)。本次观察证明旧次序不满足新增 Runtime-first 规范；这组旧路径的资源和 SQL 最后访问仍成功，未复现关闭后访问或持久事实损坏。

实现固定 runtime/worker 两阶段后，runtime_shutdown_order/plugins/plugins_review/runtime/runtime_review/composition/social_composition/plugin_social 共 102 passed，退出 0。见[t04-green.log](evidence/t04-green.log)、[t04-green.xml](evidence/t04-green.xml)。四组真实等待均在请求 settled、driver cleanup 和 runtime idle 后停止 worker，再关闭 provider，最后关闭 SQL store；runtime、worker、resource 各阶段抛错时继续余下清理。没有 sleep 竞争或外部请求。

## 六、T05

实际重现外部 SQL 选择反例：缓存入口仍为 1、直接输入为 2，首次/修改后/直接输入为 1/1/2。新来源测试初次 9 failed、1 passed，覆盖关系版本、多个 ReadView、安装接口与表/store 校验、配置固定和 search_revision 缺口。见[t05-source-red.log](evidence/t05-source-red.log)。另行暂停 query embedding，得到 7 failed、4 passed；旧实现漏检外部选择/关系、回复正文及向量变化，首屏没有独立版本对照，权限变化未拒绝首屏。已识别的人格/帖子/正常评论/成员变化用例仍通过，见[t05-semantic-red.log](evidence/t05-semantic-red.log)。

实施后新来源文件与 social_composition/social_composition_ranking/social_independent_review/plugin_social/plugin_social_review/social_cognition/social_content_discovery/runtime_shutdown_order/actor_directory_delegation 共 109 passed，退出 0；见[t05-green.log](evidence/t05-green.log)、[t05-green.xml](evidence/t05-green.xml)。选择写入后得到 1/2/2，rank 调用从 1 增至 2，再次相同输入保持 2；删除选择行得到 1/1。外部关注单独改变全部网络分数。默认 query 的成员/候选/关系/偏好/persona 使用同一视图，退出后不可查询。八种来源变化和显式 query 的外部选择变化在 await 后均拒绝，无排名或语义结果发布与曝光；首屏独立核对权限。准备后的排名/Page 同版、只读配置、新 Run 策略重绑与旧 cursor、完整 Unicode 长正文和精确呈现曝光均有断言。

现有加权九项完整指标与基线参考函数对照继续通过；策略正例、ScoreOnly、输出身份/total/revision 负例、原生大池索引、A/C/A/C 排名与语义 LRU、容量 1 的淘汰回归继续通过。删除同步 ranking_input/ranking_dependencies/Social.rank 后，将两个现役测试迁到实际异步输入/输出；生产代码和示例检索无旧入口调用。semantic 不再持有候选服务，静态服务图随真实借用关系调整。

固定外部选择 2 条、页长 1，历史 100/10000 时，冷输入 SQL VM 为 328/328，热页为 249/249。页长 1 实际物化 1 条，limit10 返回全部 2 条，热页未重新读池或排名；长正文分块拼接大于 100000 bytes 且逐字节相等。见[t05-source-cost.log](evidence/t05-source-cost.log)。这项为当前源码的工作量回归，固定源码前后完整成本比较留待 T13。

一次相关回归有 103 passed、1 failed，原因是成本 fixture 的 ordinal 从 2 开始与原有第二帖冲突；将附加历史从 ordinal3 开始后复核通过。见[t05-focused.log](evidence/t05-focused.log)。该失败未进入产品判断。首次实现后的较小相关集合 49 passed，见[t05-initial.log](evidence/t05-initial.log)。

## 七、T06

原源码缺少通用组合模块，新增测试 collection 因 ModuleNotFoundError 退出 2；见[t06-red.log](evidence/t06-red.log)。新增 SourcePosition、SourceBatch、SourceSpec、CompositePerception、ComposedInput 与 cognition_plugin，复用原 CognitiveInput 和 InputBatch/LLMDriver 保存路径，未更改内核协议。首先 21 passed，见[t06-initial.log](evidence/t06-initial.log)。

再用持久通知 data/source、完整点恢复、真实 Memory 对象和两个普通扩展拓展验证。COG-03/05/06/07/08/09/12：空位置与未初始化可区分，保留 inactive 键，坏 ID/ref 与 missing service/nonmapping 明确失败，来源错误/取消不提前确认，context/首末消息/cursor 编码错误保留此前原文与位置，丢弃批次无 provider 材料或效果，保存后效果能看到全部位置且 provider 尚未调用，两个主体交错不会混合材料/位置/回调。

COG-02/04/13：真实双通知 data/source 在同 actor/Moment/mode 三次激活分别推进水位；A→B→A 保留同 Thread；三个独立 Run 固定 time=1、phase=A、step=1/2/3，省去来源时保留 schema/旧键，重新加入以原水位取积累正文；交换顺序在新 Run 实际入参中可见。另将只读来源改成新位置格式并使用新 ID，旧键与 Thread 前缀保持完整。COG-10：首/次效果写回执前后分别 error/cancel，失败后 complete_step 保持 1，新 Run 恢复 point1 时两份位置、全文和回执全等。这里验证 callback 故障恢复，Runtime/provider/扩展序列化和受控进程中断组合在 T07/T09 补充。

COG-11：两个普通扩展与正式 memory_plugin/MemoryExtension 验证 enter→prepare→source effects→逆序 exit，召回 preparation 能读双来源完整 InputBatch，实际 typed provider 入参含全部原文、扩展和长记忆。本用例明确 auto_recall=True、auto_write=False、active_tools=False，其余既有记忆与 interview 用例保留并继续通过。COG-16：可选模型/记忆/社交 imports 被拦截时，导入认知类型后仍可完成官方 rule_run/run_plan；通知/Thread 历史 100/10000，固定一条完整新增消息的构建 SQL VM 为 115/115，构建器未调用全 Thread read_messages，无预先回执。

通知原型有两项额外失败：tuple 数据引用未接线、重复回执被忽略，见[t06-notice-red.log](evidence/t06-notice-red.log)；修引用后继续重现默认不同实例同表，见[t06-notice-schema-red.log](evidence/t06-notice-schema-red.log)。改成显式 tuple 服务引用、插件实例名限定表、重复确认抛主键错误并回滚整个回执事务，符合交接规格。最后新组合 40 用例和 kernel_cognition/kernel_cognition_review/kernel_memory/kernel_llm 共 115 passed，退出 0；见[t06-final-green.log](evidence/t06-final-green.log)、[t06-final-green.xml](evidence/t06-final-green.xml)。中间 103/113 结果作为过程记录保留，没有替代最终源码验收。

## 八、范围

T01–T06 当前通过。正式 social 来源迁移、官方配方、组合 CLI 和独立跨进程 E2E 仍待后续任务。记录的 provider 材料来自 LLMDriver 实际传入 request_model 的 SDK typed messages，确定性 provider 无网络；正式 SDK 发送边界、真实端点与模型质量均待相应授权验收。全部当前测试为离线确定性测试；未调用真实模型或付费 embedding。
