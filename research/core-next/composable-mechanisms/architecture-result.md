# 以基础环境检验 Core 的架构结果

plain、RR 与 social 的价值在于让共享状态、驱动装配、信息消费和恢复真正相遇。本轮结果支持一个明确判断：Core 的静态组合与资源边界可以承接这三类消费者；暴露的问题集中在依赖含义、所有权和实际呈现的提交时机。研究行为的逐笔对照、独立消费者及成本边界共同限定这一判断。

## 一、复用

已有底座承担了大部分工作。省略 `install` 的 data-only 插件已有空操作实现；Host 的标准库资源栈、逆序清理、quiesce 与步骤 hooks 继续管理生命周期；`prepare` 在创建时取得外部材料，恢复跳过准备与初始化。共享 SQL 事务继续让跨机制事实、计数、索引和通知原子写入，规范 schema 比较继续容纳独立 DDL 的声明顺序变化。合同见[插件主机](<../../../docs/core-next/plugin-contract.md>)与[组合构建](<../../../docs/core-next/composition-contract.md>)。

plain 提供空机制及正式 RunPlan 恢复的控制组；RR 把主体资料与驱动依赖带进同一装配；social 进一步引入可替换计算、异步资源和有副作用的认知呈现。这些消费者把 Core 问题与领域规则分开了。

## 二、修复

下表将实施规格中的反例与可执行消费者对应起来；测试入口保留具体失败条件，冻结版本的执行结果统一见[集成验收](<integration-result.md>)。

| 暴露的问题 | 修复及实际证据 |
|---|---|
| 静态配方无法统一展开；数据准备与服务安装共用一张依赖图 | `includes` 展开显式命名的实例，`schema_requires` 独立于 `requires`；名称冲突、缺依赖及两图真环在取得资源和创建目录前失败。[预检及恢复反例](<../../../tests/primary/test_kernel_composable.py#L10>)、[双 RR 实例恢复](<../../../tests/primary/test_composition_independent_review.py#L48>)和[双 social 实例](<../../../tests/primary/test_social_independent_review.py#L173>)消费该合同。 |
| 环境读取主体资料，driver 又依赖环境输入，形成装配环 | ActorDirectory 独立拥有持久主体资料，ActorStore 再绑定 driver 工厂。RR 的[真实驱动依赖](<../../../tests/primary/test_round_robin_information.py#L137>)及 social 的[认知组合](<../../../tests/primary/test_social_cognition.py#L42>)检验数据目录与运行映射分离；纯目录可使用尚未安装的 driver 标签。 |
| 同一 provider 多挂载重复关闭，关闭首错遗漏资源；共享 provider 的 ACL 版本串扰 | 创建者单一拥有资源，路由默认借用，独立托管按对象去重并逆序释放；权限依赖通过 per-call scope 合并，provider 声明保持不变。[关闭反例](<../../../tests/primary/test_kernel_composable.py#L77>)、[跨路由游标反例](<../../../tests/primary/test_composition_independent_review.py#L13>)与[并发调用及原 scope 生命周期](<../../../tests/primary/test_kernel_composable.py#L152>)分别验证所有权和访问边界。 |
| 构建过或预览过的材料被误计为主体已见 | `InputBatch.effects` 随最终所选且成功保存的材料执行；丢弃批次、序列化失败均无呈现效果。[批次选择反例](<../../../tests/primary/test_kernel_input_effects.py#L12>)与[实际曝光/恢复](<../../../tests/primary/test_social_cognition.py#L42>)进入完整 LLMDriver；Thread 追加与领域效果各有事务边界，效果失败终止激活。 |
| social 的向量校验借道记忆实现，引入无关依赖 | [纯向量校验](<../../../src/society0/kernel/vectors.py>)承接数量、有限数值及维度合同，由[记忆](<../../../src/society0/kernel/memory.py>)和[社交语义索引](<../../../src/society0/plugins/social_semantic.py>)共同复用。 |

RR 的小组、计划、伙伴历史和原文通过现有 SQLInformation 授权与分页接入；[Session、ActorFiles 与 scripted-provider 测试](<../../../tests/primary/test_round_robin_information.py>)检验主体入口。这里复用通用信息路由，RR 配对与收件规则仍由领域机制承担。

social 将输入获取、排序和呈现分离：推荐服务提供不可变的候选及版本值对象，weighted 与 chronological 通过具名 `rank` 引用选择；[外部观测插件](<../../../examples/core_next/recommendation_observer.py>)消费同一输入与两种输出，拥有自己的纯数据表。[独立叶组合及恢复](<../../../tests/primary/test_social_independent_review.py#L130>)验证推荐可脱离内容写服务、呈现及曝光安装。全局热门走独立索引，推荐保留活动池；[热门反例](<../../../tests/primary/test_social_composition.py#L24>)与[跨领域回滚](<../../../tests/primary/test_social_composition_ranking.py#L58>)分别约束查询和写入。

实际认知保存 `seen/visible/recommended` 游标，同 Moment 重激活及完整点恢复延续位置，完整 Thread 保留此前原文。只读 feed、热门预览与实际呈现分别验证；曝光随实际材料登记并在步骤末提交。支持范围由[社交合同](<../../../docs/core-next/social-contract.md>)和[认知合同](<../../../docs/core-next/cognition-contract.md>)定义。

## 三、证据

语义来源是固定旧版 `v4.1.14 / 4ff2df74668931aef12b1a4951fab1d97d6a0981` 的真实引擎隔离进程。对照在 Next 基线 `7320b13` 的实施工作树完成，随后由冻结源码 `521bda2ad28a9855ef18e76090c353bda907b4f2` 的最终全量验收复验。详见[oracle 来源与逐笔范围](<oracle-report.md>)及[共同轨迹](<oracle-common-trace.json>)。

共同脚本比较 RR 的全部 21 条消息、配对及主体记录，以及 social 动作后的原文、关系、计数、通知、推荐与曝光；主动损坏单条结果必须使比较器失败。Next 跨进程恢复继续与旧连续事实相等。**旧 social 恢复后动态关注边丢失已单列为旧缺陷**，其精确差异与标准化白名单见[oracle 差异](<oracle-report.md#三差异>)。plain 旧路径未激活规则行为，驱动成本等价仍无该项证据；oracle 中的 RR/social 直接收束机制，Runtime 阶段编排另由集成测试覆盖。

冻结源码的[最终离线验收](<integration-result.md>)为 1048 passed、1 skipped、15 deselected；规则 pilot 完成 step 2 且无诊断，实际载荷 Node 测试 9 项通过。跳过项需要独立 sqlite-vec 环境，真实端点用例被排除。本机已有依赖与 Node 24 的结果不覆盖 CI Node 22、重新按锁安装或浏览器交互。此处记录验收条件；合入、推送及工作树清理由[任务清单](<../../../.scratch/next-composition/TODO.md>)另行确认。

## 四、成本

[冻结成本报告](<cost-formal-report.md>)比较 `7320b13` 与 `521bda2` 两个干净源码身份，共用原仓 Python 3.12 虚拟环境、相同依赖声明和锁身份。四主体、固定当前收件箱或活动推荐配置，历史取 100 与 10000；三模式六组业务输出及各自恢复快照均相等。[数字索引](<cost-formal-index.json>)保存源码身份和精度，[分项表](<cost-formal-tables.md>)保存完整观测。

在 A/C/A续/C续分页中，RR 热路径 SQL VM 为 254→254；无嵌入 social 为 2924→2629，排序 4→2 次；嵌入替身为 6807→5602，排序/嵌入均 4→2 次。两个历史规模具有相同 SQL 热点工作量。完整原文每次仍读取并逐字节验证 311296 字节，曝光与内容保持等价。

这些结果限定于单次插桩测量、确定性嵌入及执行全历史扫描及排序的假向量后端。真实向量服务、网络模型和完整 LLM 成本尚无结论。按主体 LRU 默认容量 128，交错主体超过容量会淘汰并重算；版本变化同样重建输入和排序。

恢复仍随历史增长：after 的 RR 为 28.273→117.601 ms，social 为 42.771→382.519 ms，嵌入模式为 85.423→882.764 ms；历史规模成本未改善。这些恢复采用同进程新连接并保留 OS cache。seed、complete 等阶段有测量回退，tracemalloc 阶段新分配峰值也有上升；初始化、完整点、恢复和内存均未全面优化，具体值保留在[回退记录](<cost-formal-report.md#21-回退与未改善>)。

最终性能独立审查已完成并撤销身份阻断；12进程源码身份、六组 semantic 与恢复全等、78行1248个数字、全部回退披露及三个小测通过的复核记录见[独立复审核验](<cost-formal-report.md#31-独立复审核验>)。原始工件已完整复制至[长期归档](</Users/marvin/Documents/同花顺（2）/outputs/society0-next-composition/cost-formal-521bda2/>)，原运行 root 与 raw 来源事实保留。该结论限定于离线假向量、单次插桩与热恢复；合入、推送及 cleanup 仍待完成。

## 五、边界

已支持的是静态具名配方、显式依赖与类型契约、单一资源归属、共享事务，以及完整步骤恢复时重新装配策略。服务对象类型、并发与返回语义由提供方显式约定，公共类型导出由[API 验收](<../../../tests/primary/test_composable_public_api.py>)核对。恢复要求完整新配方的 schema 与同版本完整点一致；无语义配方省去语义表，旧配方物理完整点的跨版本迁移另立任务。

下一轮研究问题是：真实向量后端及完整 LLM 链的成本如何分解；主体交错超过缓存容量后如何保持可接受的重算成本；历史增长下如何降低恢复和完整点成本；无仪表重复测量能否区分环境噪声与 seed/complete 回退；更大活动集、原生内存与独立进程冷恢复如何验收。动态热替换、通用事件总线及通用 DI 的研究由真实消费者提出具体需求后开展。

本轮架构结论落在可执行的组合、主体信息和恢复边界上。后续扩展继续以真实消费者、逐笔事实与分项成本决定 Core 的变化。
