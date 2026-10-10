# 可组合性实施清单

规格与验收定义见 [PRD](PRD.md)。此文件记录本轮进度与待解决问题；正式合同在验收后更新对应 Core/插件文档。

## A：问题与规格

- [x] 核实 next / stable 身份并创建隔离分支工作树。
- [x] 两个独立只读审查：Core 依赖及资源、social 所有权与推荐链。
- [x] 复现 RR/social 驱动装配环、Information 重复关闭及首错漏清理；核实 pure-data 与既有恢复保障。
- [x] 写明目标、接口方向、Core/插件职责及验证标准。

## B：公共接口

- [ ] 自动化红例：静态复合插件、两种依赖图、创建前预检与恢复。
- [x] Plugin 子插件集合、数据先决与服务图独立展开/预检。
- [x] 主体数据目录与运行期驱动映射分离；两个机制消费目录。
- [x] 信息 provider 单一所有权、共享挂载和失败清理。
- [x] RR 小组/轮次/伙伴历史/原文通过实际 Information 可达。
- [x] 公共工厂惰性导出与 TYPE_CHECKING 对齐；隔离进程核验 root/plain 不加载 social/LLM/Chroma。

## C：社交组合

- [ ] 热门与曝光红例、规范事务逐笔对照。
- [x] 明确领域数据、关系、内容互动、通知、曝光的服务所有权。
- [x] 独立候选、加权与时间策略、公开输入输出。
- [ ] 可选语义索引及任务生命周期独立；无语义路径保持轻量。
- [x] 独立观测插件消费两种策略，拥有自己的纯数据状态。
- [x] 主体实际呈现与只读预览分别接线，正确登记曝光。

## D：组合检验

- [ ] 同类多实例、真实机制驱动输入依赖、纯数据组合与恢复。
- [ ] 旧基线/Next 独立进程脚本：RR+social 逐条比较及比较器破坏测试。
- [ ] Session/ActorFiles/完整 LLMDriver scripted-provider 消费路径。
- [ ] 完整点恢复与继续结果，失败步骤不发布、异步等待/关闭故障。
- [ ] 将发现明确分类为 Core 修复、通用插件、领域规则或无须改动。

## E：收口

- [x] 固定活动集、历史增长、交错推荐分页等分项性能试验；口径与冷恢复限制见 [成本报告](../../research/core-next/composable-mechanisms/cost-report.md)。
- [ ] 焦点、全部离线、实验与 workbench 测试。
- [ ] 非作者挑剔审查与修复复验。
- [ ] 更新正式合同和实际结果；记录未验证范围及明确合同差异。
  - 正式 plugin/composition/actor/interaction/cognition/social 合同、README 和 skill references 已同步；旧新 oracle 结论等待完整比较复验。
  - 文档核验焦点：公共 API、kernel_composable、kernel_input_effects、social_cognition、social_composition、social_composition_ranking、recommendation_observer、round_robin_information 测试通过；公共 API 曾先因类型声明缺项失败。全部测试均清除八种代理变量，使用原仓解释器和 `PYTHONPATH=src:.`。
- [ ] 合入推送 next，核实 stable/main/tag 未变，移除已合并临时工作树。
