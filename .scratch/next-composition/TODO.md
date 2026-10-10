# 可组合性实施清单

规格与验收定义见 [PRD](PRD.md)。此文件记录本轮进度与待解决问题；正式合同在验收后更新对应 Core/插件文档。

## A：问题与规格

- [x] 核实 next / stable 身份并创建隔离分支工作树。
- [x] 两个独立只读审查：Core 依赖及资源、social 所有权与推荐链。
- [x] 复现 RR/social 驱动装配环、Information 重复关闭及首错漏清理；核实 pure-data 与既有恢复保障。
- [x] 写明目标、接口方向、Core/插件职责及验证标准。

## B：公共接口

- [x] 自动化红例：静态复合插件、两种依赖图、创建前预检与恢复。
- [x] Plugin 子插件集合、数据先决与服务图独立展开/预检。
- [x] 主体数据目录与运行期驱动映射分离；两个机制消费目录。
- [x] 信息 provider 单一所有权、共享挂载和失败清理。
- [x] RR 小组/轮次/伙伴历史/原文通过实际 Information 可达。
- [x] 公共工厂惰性导出与 TYPE_CHECKING 对齐；隔离进程核验 root/plain 不加载 social/LLM/Chroma。

## C：社交组合

- [x] 热门与曝光红例、规范事务逐笔对照。
- [x] 明确领域数据、关系、内容互动、通知、曝光的服务所有权。
- [x] 独立候选、加权与时间策略、公开输入输出。
- [x] 可选语义索引及任务生命周期独立；无语义路径保持轻量。
- [x] 独立观测插件消费两种策略，拥有自己的纯数据状态。
- [x] 主体实际呈现与只读预览分别接线，正确登记曝光。

## D：组合检验

- [x] 同类多实例、真实机制驱动输入依赖、纯数据组合与恢复。
- [x] 旧基线/Next 独立进程脚本：RR+social 逐条比较及比较器破坏测试。
- [x] Session/ActorFiles/完整 LLMDriver scripted-provider 消费路径。
- [x] 完整点恢复与继续结果，失败步骤不发布、异步等待/关闭故障。
- [x] 将发现明确分类为 Core 修复、通用插件、领域规则或无须改动。

## E：收口

- [x] 固定活动集、历史增长、交错推荐分页等分项性能试验；探索口径保留在 [成本报告](<../../research/core-next/composable-mechanisms/cost-report.md>)。
- [x] 冻结版本分项测量：before `7320b13`、after `521bda2`，六组业务比较与恢复快照相等；来源、条件、回退与冷恢复限制见 [正式成本报告](<../../research/core-next/composable-mechanisms/cost-formal-report.md>)、[索引](<../../research/core-next/composable-mechanisms/cost-formal-index.json>)及[分项表](<../../research/core-next/composable-mechanisms/cost-formal-tables.md>)。
- [x] 焦点、全部离线、实验与 workbench 测试：冻结 `521bda2`，1048 passed / 1 skipped / 15 deselected；实际 pilot step 2 无诊断，Node 9 项通过。条件与排除范围见 [最终验收](<../../research/core-next/composable-mechanisms/integration-result.md>)。
- [x] 非作者组合、权限、认知、策略及 oracle 挑剔审查与修复复验；[架构结果](<../../research/core-next/composable-mechanisms/architecture-result.md>)对应独立反例，最终全量包含其回归。
- [x] 更新正式合同和实际结果；[真实旧引擎 oracle](<../../research/core-next/composable-mechanisms/oracle-report.md>)已完成比较复验，旧恢复丢边、标准化范围与剩余覆盖单列。
  - 正式 plugin/composition/actor/interaction/cognition/social 合同、README 和 skill references 已同步；公共 API 类型导出已复验。架构结果记录 Core 修复、既有能力复用、领域职责与下一轮问题。
- [x] 最终性能独立审查完成，身份阻断已撤销；12进程、六组语义及恢复、78行1248数字与全部回退披露复核通过，小测3绿；原始工件完整归档，限定离线假向量/单次插桩/热恢复，见[复审核验与归档入口](<../../research/core-next/composable-mechanisms/cost-formal-report.md#31-独立复审核验>)。
- [ ] 合入并推送 next，核实 stable/main/tag 未变。
- [ ] 核实最终工作树 clean，移除已合并临时工作树及完成基准工作树收尾。
