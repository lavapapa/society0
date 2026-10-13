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

## 八、T07

新规范初次 4 failed：social_source_plugin 未提供，SocialPerception 仍返回旧 tuple，见[t07-source-red.log](evidence/t07-source-red.log)。将工厂改为独立 source，保留唯一 SocialPerception 实现，同次调用计算 messages/position/fresh/ids/effects，移去 Thread、环境文案和完整 builder 的依赖与出口；删除旧工厂导出。两个现役消费者改为显式 social_source + cognition + notification_data/source，默认 consumer 为 operating_context，旧单来源 CognitiveInput 调用方继续保留。

原有增量排序、元数据变化、曝光、恢复及公共 oracle 场景继续通过。旧手工附加通知/单 actor 回执用例已改用真正持久通知及独立水位；丢弃输入同时断言没有两份原文、两个来源效果或 operating_context 游标。现役两进程测试也追加和恢复双来源正文、Thread 与通知回执。

COG-01/02：social→notice 与 notice→social 在不同 Run 真实完成 3 次同 Moment 激活，分别只增加社交和通知，两水位独立；SDK typed 入参完整背景/环境一份，全部新增正文按配置顺序进入，旧原文各一次。A→B→A 使用真实 Runtime，原 Thread 前缀保留，通知回执按各自 Thread 保存，曝光准确区分新 Thread 的再次呈现。COG-12：通过 Event 交错两个主体，各自实际请求保留共享帖子及仅自己的通知，a 的通知位置为 3、b 为 2，回执分别绑定自己的 Thread 和真实通知 id。

COG-10：实际 Runtime 中扩展消息编码失败、provider 异常与发布前中断，保存后观察器确认双水位与通知回执已存在；错误后 complete_step 保持 1，失败 StageStore 禁止继续读取。新目录恢复 point1 时两个来源呈现效果、失败 Thread 与未发布消息全部隔离。第一次相关回归 53 passed、4 failed 来自测试用法：并发 Phase 未显式 independent，以及在已失效 StageStore 上读取；改成正式独立阶段，并在保存后/失效前采样再核对恢复，保持产品失败规范。见[t07-focused.log](evidence/t07-focused.log)。

最终 social_cognition/cognition_oracle_review/cognition_composition/recommendation_sources/plugin_social/social_composition/social_composition_ranking/social_independent_review/kernel_cognition/kernel_cognition_review/kernel_memory/kernel_llm 共 218 passed，退出 0，见[t07-green.log](evidence/t07-green.log)、[t07-green.xml](evidence/t07-green.xml)。较早 11 passed 的阶段结果保留在[t07-initial.log](evidence/t07-initial.log)。生产代码、示例、skill 和现役测试已无 social_cognition_plugin/旧 consumer 调用；公开合同及 skill 的工厂接线同步更新。历史 oracle 与参考源保持原身份。

## 九、T08

公开 presets 模块缺失时 collection 退出 2，见[t08-red.log](evidence/t08-red.log)。新增 rule_preset/llm_preset，返回普通 Plugin 元组；显式 records/data 互斥，records 生成器延迟至初始化，固定单驱动名字与持久标签，原样接入 policy/extensions/shell_factory/provider_selector。Runtime 使用已有默认参数或显式替代根，Schedule 为调用方真实插件。

BASE-01：官方 conversation_pilot 改用 rule_preset，仍执行 work/commons 双机制、全部四主体行动与原消息；在可选模型/记忆/社交 imports 被拦截的独立进程中公开 run_plan 完成，sys.modules 同样确认无隐式可选导入。BASE-02/03：官方 LLM preset 真实进入 LLMDriver 与完整 Thread，typed provider 实际接收双源全文，无 memory 配方不安装资源；显式 memory_plugin + MemoryExtension 分别覆盖 write-only、recall-only、active-only、三项同时启用和 interview。召回全文、工具可见性、实际提取和 SQL 写入数量独立符合策略，interview 保持无自动写入。

BASE-04：替代 Runtime/自定义 Schedule 真实被调用，run_plan 服务引用明确，默认 runtime 缺席，manifest.effective_runtime 为实际 capacity=3/max_activations=7，退出经过 runtime 阶段。BASE-05/07：完整点恢复继续持久 rule 标签和完整时间线，目录一次安装，初始化生成器在恢复时没有重复消费；包含空 records 的双配置立即拒绝，重复基础名字沿 Host 失败。BASE-06：缺插件、扩展与真实环在 prepare/目录 I/O 前失败；三类已存在插件的缺服务在安装时 KeyError，已安装拥有者各关闭一次，无 provider 请求与 runner manifest；显式 None provider/builder 在配方构造时报错。

第一次较小回归 12 passed、3 failed：测试 Phase 返回了 activate handle，违反 results 的 StepResult/None 规范。将测试阶段改成返回 None，恢复配方同时按 SequenceSchedule 的完整时间线定位下一步骤；产品规范保持。见[t08-initial.log](evidence/t08-initial.log)。接着 15 passed，见[t08-focused.log](evidence/t08-focused.log)。补入独立记忆和缺服务用例后最终 foundation_presets/core_next_pilot/kernel_runner/kernel_plugins/kernel_cognition/kernel_memory 共 78 passed，退出 0；其中新 preset 22 用例，见[t08-final-green.log](evidence/t08-final-green.log)、[t08-final-green.xml](evidence/t08-final-green.xml)。中间 75 结果保留在[t08-green.log](evidence/t08-green.log)。

公开用法归入[builtin-composition](../../../docs/core-next/builtin-composition.md)，[README](../../../README.md) 已链接；参数、默认值、单驱动边界、外部目录、完整时间线、可选资源和实际替换责任与代码一致。

## 十、T09 进行中

公开工厂缺失时 CLI 五个案例失败，见[t09-entry-red.log](evidence/t09-entry-red.log)。新增[组合入口](../../../examples/core_next/composed_cognition.py)与[只读验收器](../../../tests/e2e/core_next_composition_assertions.py)，消费 RunPlan/run_plan、llm_preset、标准 Schedule/Runtime、社交来源和原生通知来源。两个案例共用请求、保存后观察、诊断和激活完成工件；请求载荷取自实际 request_model typed 参数及 options，并核对 Thread 请求事件的 through。正文含完整中文、emoji 与首尾标识；社交显式允许长正文。

目前五个基础用例完成：连续运行与受控失败后新进程恢复逐项比较实际请求与选项；显式空来源、通知单来源、通知优先顺序；两实例来源在恢复中省略并重新加入，保留非活动水位并呈现积累原文。随后覆盖 source-read、三类非法 append 数据、保存后部分回调写入、provider-error、after-step-flush、before-complete 八类故障。每类均核对 runner 首异常、失败完成水位和新目录从上一完整点恢复。部分回调失败额外证明 live 原生回执已落入部分写入，而 provider 与后续 sentinel 均未调用。

初始接线失败依次定位为 social_plugin 的 positional members 参数误用、保存后观察器在写事务中重入读取、社交默认长度拒绝长正文、JSON 来源正文需要解码验收。修正后五个基础用例通过；扩展故障用例后 12 passed，见[t09-faults-initial.log](evidence/t09-faults-initial.log)。变体首轮 2 passed/1 failed：interview 未声明结构化测量 schema，验收正确拒绝无工具选项，见[t09-variants-red.log](evidence/t09-variants-red.log)。补入明确 schema 与真实 submit_result ToolCallPart、实际 DriverResult 捕获后，重排、丢弃输入与 interview 共 15 passed，见[t09-outcomes-initial.log](evidence/t09-outcomes-initial.log)、[t09-outcomes-initial.xml](evidence/t09-outcomes-initial.xml)。背景每个 Thread 一次；主观状态每次激活增加一次，历史全部保留。最后公共入口、foundation_presets、social_cognition、cognition_composition 共 87 passed，见[t09-partial-green.log](evidence/t09-partial-green.log)、[t09-partial-green.xml](evidence/t09-partial-green.xml)。

新增[SDK 组合夹具](../../../tests/e2e/core_next_composition_sdk.py)：消费同一公开 RunPlan，替换 capture Plugin 并加入原生 model_plugin，保持 LLMDriver 完整循环。实际 endpoint.start 后包裹 chat.completions.create，在发送点读取原 Thread request 序号、水位与 physical_request_id。离线传输复用成熟 SSE 夹具，由真实 SDK 编码并读取流；同时捕获 create 入参与 HTTP JSON，对完整消息、全部函数 schema、tool_choice=auto、parallel_tool_calls=false、stream=true、temperature 与 extra_body 做核对。SDK 初次三例红为入口缺失，见[t09-sdk-red.log](evidence/t09-sdk-red.log)。接通后两例通过、一例恢复比较发现 provider_response_id 的独立 UUID4 差异，见[t09-sdk-initial.log](evidence/t09-sdk-initial.log)。归一化限于可验证 UUID4 的顺序身份，额外证明 typed 历史里的 ID 引用能对应实际保存的响应；请求序号、水位、载荷与工具选项继续逐项比较。最终 18 passed，见[t09-sdk-green.log](evidence/t09-sdk-green.log)。

真实验证的无嵌入消费者新增显式 configuration(embedding=False)，保留现有默认嵌入要求；公开 profile 对实际装配的能力生成，所用 key 保持去除。该缺口先 TypeError 再绿，见[t09-live-config-red.log](evidence/t09-live-config-red.log)。冻结 task 首先被两个 SDK 案例发现未进入输入，见[t09-goal-red.log](evidence/t09-goal-red.log)；将完整 task 原文交给标准环境输入后通过。公开入口、SDK 与 LLM 独立配置共 19 passed，真实案例两项按标记排除，见[t09-sdk-live-config-green.log](evidence/t09-sdk-live-config-green.log)、[t09-sdk-live-config-green.xml](evidence/t09-sdk-live-config-green.xml)。真实模式的[runner 工厂](../../../tests/e2e/core_next_composition_live.py)和[验收](../../../tests/e2e/test_core_next_composition_live.py)已接好，下一步在冻结部署副本里实际执行结构化工具、领域动作和完整点恢复。

新增规则公开案例[目录与工件](../../../examples/core_next/composed_directory_artifacts.py)、[推荐](../../../examples/core_next/composed_recommendation.py)。代码实现分别委派聚焦子任务，文档与整体方案由父代理自行处理。目录行为红为两个实际成功 runner 之后发现 native persona/driver 取值未委托；改用可观察 override SQL 的目录、两个具名驱动后，default/readonly/persona/artifact-read 四场景全过并从 step 1 新进程恢复到 step 2，见[t09-directory-red.log](evidence/t09-directory-red.log)、[t09-directory-green.log](evidence/t09-directory-green.log)。只读目录分页保持 run_id 身份；persona 的同 ReadView、依赖版本、评分变化与缓存命中按实际事实核对。工件使用没有 .store 的显式 Thread 门面，a/b 的长中文 emoji 正文、工具结果、续页字节、归属错误与 ScopeClosed 分别核对权威数据。

推荐行为红由 external selection 缺表依赖引起，真实 completed 的缓存只见一帖而公开 fresh 快照见两帖，见[t09-recommendation-red.log](evidence/t09-recommendation-red.log)。default/external-selection/external-relations/native/semantic-change/paging 六场景与六组完整点恢复共 12 passed，见[t09-recommendation-green.log](evidence/t09-recommendation-green.log)。自有 SQL 保存实际 source revision/following、完整九项分数、页与 rank/embed 计数；同宿主 Event 控制的五类 await 变更均实际拒绝旧计算，后续新请求重算并命中；原文与向量维持规范追加写入。比较具名规范化新 Run 身份、运行本地 SQL revision、cursor 身份与进程冷缓存计数，validate 另精确核对每一版本绑定、实际计数和完整原文。

总入口与交接验收 CLI 首轮四红，见[t09-dispatch-red.log](evidence/t09-dispatch-red.log)。现将规则案例接到相同 composed_cognition:build，并支持交接的 --continuous/--output（参数规范统一，不保留旧 --compare 别名）；总验收器汇总实际只读事实和比较。父代理最终统一重跑总入口10场景、多源15场景、SDK3场景、目录4场景、推荐12场景共 44 passed（50.37s），见[t09-public-domains-green.log](evidence/t09-public-domains-green.log)、[t09-public-domains-green.xml](evidence/t09-public-domains-green.xml)。

T09 已补齐 [注册与清单](../../../examples/core_next/composed_registration_manifest.py)、[关闭与扩展](../../../examples/core_next/composed_shutdown_extensions.py)、[配置边界](../../../examples/core_next/composed_configuration.py) 和 [来源空值／并发／Moment](../../../examples/core_next/composed_source_edges.py)。前两组为聚焦编码委派；公共文档、全局接线、配置与来源场景由父代理自行完成。注册/清单作者从实际业务未执行与错误完成的 8 红达到 8 绿，原证据见 [red](evidence/t09-registration-manifest-red.log)、[green](evidence/t09-registration-manifest-author-green.log)。关闭作者红为任务未受控取消而 CLI 完成到 step 2；扩展红为完整标准驱动运行后 B 原文的四条实际 provider 检查失败；两组最终 12 passed，见 [shutdown red](evidence/t09-shutdown-red.log)、[extensions red](evidence/t09-extensions-red-checks.json)、[author green](evidence/t09-shutdown-extensions-author-green.log)。Journal 在原生 abort_step 后记录关闭事实，与未撤销前 SQL 前缀逐项比对；清单写失败精确 FileExistsError、标准状态文件尚未创建、StageReader complete_step=0，消费者附属 cli-result.json 保存实际退出码与 stdout/stderr。完整 owned/borrowed 关闭与独立 Host 使用事实均经公开消费者核对。

配置十个负例与三个合法例、无可选模型/记忆依赖的 base-only 真实 runner、来源缺席/None/0/{}、容量 2 的实际 Event 交错和三个进程的 Moment A→B→A 均通过。初次配置新入口红包含未知启动验收参数和合法计划缺口；中间 RunContract 字段、Actor 取值与 initializer Writer 接线错误单独保留为开发诊断。来源初次三个公开 CLI 因未支持 variant 红，随后将 Phase execution 明确设为 independent，Moment 从打开事件的公开 payload 取得。见 [configuration red](evidence/t09-configuration-red.log)、[source red](evidence/t09-source-edges-red.log)、[17 passed](evidence/t09-source-config-green.log)。显式边界来源 ID 的追加检查发现错误落入原双源校验，保留 [red](evidence/t09-explicit-edge-source-red.log) 后用声明的来源替换基础认知 binding。

`capture=false` 首轮实际保留 4 条额外完整 provider 副本，见 [red](evidence/t09-capture-disabled-red.log)。现关闭夹具额外 requests/observations/outcomes/sentinel，原生请求、完整 Thread、实际 typed 正文、来源 cursor、逐帖事实与通知确认逐值保持，见 [green](evidence/t09-capture-disabled-green.log)。该开关服务成本采样；正常行为与故障验收仍使用完整捕获。新增 [公共运行说明](../../../examples/core_next/README.md) 由父代理编写。

统一命令 `python -m pytest tests/e2e/test_core_next_compos*.py -m 'not real_e2e'` 经实际标准公共子进程完成 99 passed、2 deselected、97.32s，见 [log](evidence/t09-all-public-cases-green.log)、[XML](evidence/t09-all-public-cases-green.xml)。这批涵盖十类公共场景、连续／新进程恢复、离线 SDK 与作者故障破坏验收器；开发身份为 HEAD 704b8fc 加本轮待提交修改。最后显式来源、Moment 与成本完整事实追加核对 4 passed，见 [final](evidence/t09-final-source-cost.log)。当前 JSON 报告包含代码身份、Run 身份、具名检查、事实位置与首项差异；比较保留请求序号、水位、所有入参及原始正文，具名处理生成时间、离线响应身份、Run 本地版本及经单独验证的引用。作者消费者测试属于开发验收，独立端到端验证留在 T12；T10–T14 与真实 memory/embedding 继续执行。

## 十一、集中收尾

当前调用方扫描覆盖 src、examples、skill 与现役 tests；已删除的 social_cognition_plugin、旧 facade ranking_input/ranking_dependencies/social.rank 在这些现役调用中无残留。WeightedRanker.rank 与独立推荐观测策略仍为有效接口；research 内冻结旧源码保留原身份。核对发现 source_edges.compare 导入 tests 包，阻止测试包的实际独立消费者产生 ImportError，见 [red](evidence/t10-example-dependency-red.log)。比较器改为本确定性示例内部仅剔除 typed 生成时间，实际连续与恢复消费者在测试包不可用时通过，见 [green](evidence/t10-example-dependency-green.log)。该测试使用公开 CLI 生成真实工件再调用公开比较接口。

公开入口、组合配方、skill 的主体／环境／调度指引、能力矩阵及迁移索引已对齐；模型合同与订阅指南分别说明现有 CLI OAuth 的 openai-codex 和应用账户 siwc。当前收尾、E2E 与发布按 [两项 todo](implementation-plan.md) 推进，本阶段由本人执行。固定源码 `0f62933df713f43338264da2ff1f7102878a7211` 的一次确定性全量执行 `python -m pytest -m 'not real_e2e'`，结果 1173 passed、17 deselected、151.85s，见 [log](evidence/final-deterministic-0f62933.log)、[XML](evidence/final-deterministic-0f62933.xml)。现行 pytest 默认范围为 tests/primary 与 tests/e2e，真实端点按标记排除，成本实验另行按固定规格执行。工作台第一次执行 npm test 因本任务 node_modules 缺 React 失败，见 [原失败](evidence/final-workbench-0f62933.log)；核对任务专属 node_modules 绝对路径后使用已有锁文件 npm ci --ignore-scripts 补齐，无锁文件改动，随后 8 passed、1 skipped，见 [ready](evidence/final-workbench-ready-0f62933.log)。跳过项已由本人公共 CLI 的真实产物补齐：从固定提交 archive 运行双轮次对话，另一个进程从 step 1 创建新目录并完成 step 2，分别通过 workbench 公共 CLI 导出。全部领域结果行、配对／指标／备注相等，逐阶段 elapsed_s 单独保留；单文件 HTML 嵌入数据与实际导出逐值相等。实际 payload 下 Node 9 passed、0 skipped，见 [端到端结论](</Users/marvin/Documents/同花顺（2）/outputs/society0-next-composition/acceptance-0f62933/conversation-verdict.json>) 与 [渲染记录](</Users/marvin/Documents/同花顺（2）/outputs/society0-next-composition/acceptance-0f62933/workbench-real-payload.log>)。对话 E2E 不导入 tests helper，不通过 pytest 代跑公共使用步骤；本次比较脚本先误纳计时列、再将 header 的指标行空单元格误作数字，两项均属比较器范围／UI 单元格处理，实际业务行始终相等，未修改产品也未重跑仿真。本人继续从固定 archive 执行公共 runner，共 17 个消费者验收报告通过，覆盖全部十类 case。12 个有限场景实际运行，加上双来源与 memory-all 的新进程恢复、回调部分写入故障后的可信 step 1 恢复，以及 notice A/B→B→A/B 三进程启停链和合法双图配置。每个公共只读报告全部 checks 通过；使用普通 subprocess，未启动 pytest，也未委派子智能体。完整输入、实际工具、原向量、本地 Chroma、资源所有权、异步推荐途中变更以及两种安装顺序／两种关闭异常均保留真实工件，见 [执行脚本](</Users/marvin/Documents/同花顺（2）/outputs/society0-next-composition/acceptance-0f62933/public-consumers.py>)、[汇总](</Users/marvin/Documents/同花顺（2）/outputs/society0-next-composition/acceptance-0f62933/public-consumers-verdict.json>) 和 [来源启停记录](</Users/marvin/Documents/同花顺（2）/outputs/society0-next-composition/acceptance-0f62933/public-source-lifecycle.log>)。

本人真实记忆与新进程恢复链已通过：用户另行授权 SiliconFlow 嵌入凭据，实际 BAAI/bge-m3 双原文请求 HTTP 200、1024 维；生成与记忆提取仍用 native Codex OAuth。两个主体在完整点 step 1 各自产生自动提取与主动保存的记忆，step 2 独立开启自动召回且保留主动工具；故意在 step 2 业务回调末尾抛错后，仅从可信 step 1 恢复至新目录并完成 step 2。只读核对六份原记忆与原向量逐字段相等、原两个 Thread identity 和完整消息逐字段相等、跨主体读取受拒，以及恢复后两个主体均实际 memory.recall、读取记忆原文、发布包含各自准确时间与暗号的动态并自然结束。最终恢复工件包含四次完整激活（两次继承、两次新执行）；失败源另有两个完成后未发布的 step 2 激活。见 [原记忆／向量与 Thread 验收](</Users/marvin/Documents/同花顺（2）/outputs/society0-next-composition/acceptance-0f62933/real-memory-verdict.json>)、[真实行动与原文读取](</Users/marvin/Documents/同花顺（2）/outputs/society0-next-composition/acceptance-0f62933/real-memory-domain-verdict.json>)、[原模型／记忆阶段时长与工作项](</Users/marvin/Documents/同花顺（2）/outputs/society0-next-composition/acceptance-0f62933/real-memory-timing-jobs.json>)。第一次消费者误返回 MemoryPolicy 已改为标准 MemoryActivation；下一次主体 A 完成、主体 B 遇 Codex 过载，完整点为 0，保留失败诊断后进行了单次原配置重试。无预算抬升、模型付费替换或失败目录续写。

用户额外授权一个部署子智能体处理 GPU 备用嵌入；其仅完成资源核实，四张 H200 利用率 88%—100%，拟选 CPU 小模型避免竞争。专属复用 SSH 路线持续出现 timeout/reset，缓存与空闲端口未核实，备用服务尚未部署。无依赖安装、新增显存、远端文件或共享服务变更；子智能体未写本仓库或文档，全部任务连接已退出。SiliconFlow 是本次实际可用的真实嵌入端点，备用部署需稳定的管理路线。

T13 无仪表计时与独立进程冷恢复入口已实现，三模式的 timing／instrumented／cold 同业务内容与 step 2 快照逐字段相等。未采集字段为 null，源码身份相同才接受 cold resume，social hot rank=2、embedding hot embed=2 固定为已有基线。见 [入口合同](evidence/t13-entry-contract.json)。固定双源正式采样已完成：12 个工作量运行、60 个无仪表样本、60 个独立解释器冷恢复，全部业务语义与恢复快照相等；详情、回退和测量边界见 [成本结果](cost-result.md)。已快进合入并推送权威 Next，任务工作树与临时分支已移除；源代码与测量身份保留在 Git 历史，运行和失败工件保留。独立 GPU 备用嵌入仍需稳定管理路线。

## 十二、范围

T01–T09 开发验证通过；调用方总核对、全量验证、独立端到端、成本与发布仍按 T10–T14 执行。typed provider 材料来自 LLMDriver 实际 request_model 入参，确定性 provider 无网络。用户已授权使用 simcat 可达 GPU/LiteLLM 服务及所需临时密钥，并授权在部署不可用时使用已有 Codex OAuth 模型；已验证 Pod 内网 SSH 与真实模型清单访问。正式 SDK 离线发送及真实 OAuth 响应、领域工具与完整点恢复已验；真实记忆/embedding 与独立验证继续执行。首次真实组合从固定 `be6dc5c` archive 部署副本经 simcat 任务转发发出实际 SDK 请求：已授权 GPU 服务返回 HTTP 400，原文为 `\"auto\" tool choice requires --enable-auto-tool-choice and --tool-call-parser to be set`。原生 Thread 保存 provider_error 与原参数，DriverResult 为 incomplete/provider_request_error，runner 的 complete_step=0；这次失败未到达预设 step 2，单独保留并确认无可信 step 1 可恢复。共享服务保持不变。后续使用已授权 Codex OAuth：现有安装依赖已经提供 OpenAICodexProvider/OpenAICodexModel，原生模型入口新增明确 `provider_type=openai-codex`，直接委托成熟适配器、专用 HTTP 客户端及其只读 CLI auth 来源，刷新仅在内存，SDK max_retries=0，模型不提供嵌入。两构造测试首轮接线错误保留后修正，实质红为 unsupported provider_type，见[t09-codex-meaningful-red.log](evidence/t09-codex-meaningful-red.log)。OAuth 独立配置首轮要求无关 URL/key，修正为明确能力参数后，native Codex/provider fields/multi-provider/auth/SDK 共 27 passed，见[t09-codex-green.log](evidence/t09-codex-green.log)、[t09-codex-green.xml](evidence/t09-codex-green.xml)。固定 `c7a12a8` archive 的首轮 Codex 初始化遇到进程 ALL_PROXY 指向 SOCKS，现有 httpx2 环境缺 socksio；原 ImportError 留存，未进入 provider 推理。按已有本机 HTTP 代理路线显式设置本次环境后真实 interview 1 passed（19.82s）：故障运行实际 complete_step=1，2 次 SDK 与 2 次有效 submit_result；step 2 部分回调写入确实新增 notice 2，恢复到新目录且沿原 Thread 完成 step 2，累计4次 SDK、4项非空中文结构化测量。随后独立顺序运行领域动作 1 passed（93.05s）：2次 completed 激活、各1次成功 social.publish_post、15次完整 SDK 调用；发现、参数与执行均由标准工具链完成，所有七个完整工具保持可见，正常推理预算与全文保持。真实日志见[t09-live-codex-interview.log](evidence/t09-live-codex-interview.log)、[t09-live-codex-actions.log](evidence/t09-live-codex-actions.log)。真实记忆/embedding 和全部迁移消费者仍待验证。OAuth 由成熟适配器从已授权 CLI 来源只读加载，刷新在内存；密钥/凭据不进入源码、清单或日志。任务回环转发使用结束后已撤销，SSH 探测连接继续供本任务后续工作。模型服务地址与实际工件保持在显式运行环境和本任务私有运行记录。
