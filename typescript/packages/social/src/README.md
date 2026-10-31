# Social 插件使用

Social 将网络、内容互动、传播、推荐、感知和研究测量接入 Core。公共数据合同从 `@society0/social/contracts` 导入；各插件入口的 `Config`、`metadata` 和 `api` 声明参数、作用域与服务依赖。

## 一、装配

先建立 run 级领域仓库及其 schema，再执行 bootstrap 初始化，随后装配 generation 级机制。

- [SocialRepository](social-repository/index.ts) 持有成员、关系、帖子、回复、反应、投递、通知、传播事件与呈现数据。[schema](social-repository/schema.ts) 是表、约束和索引的依据。
- [Social 初始化](social-init/index.ts) 选择拓扑贡献，校验全部主体引用，再分批创建成员和关系。恢复已有 run 使用 Core 历史激活路径。
- [拓扑配置](topology-edges/index.ts) 与[生成算法](topology-edges/algorithms.ts) 提供显式边、随机、小世界、无标度、完全图和目标变异系数网络。随机算法使用 Core 的具名随机地址；显式边与完全图可独立装配。
- [Social](social/index.ts) 登记行动和信息查询；[邻居传播](propagation-neighbors/index.ts) 是独立具名策略。第三方策略通过 `SocialReadPort` 选择收件人，领域仓库执行跨表写入。
- [轮流来源](round-robin/index.ts) 使用持久 ProposalSource 游标决定下一主体；推进、完整点和分支由 Core 运行器负责。

业务组件注入 `eventHandlers`，通过 `ctx.eventHandlers.register(handler)` 注册事件贡献；注册按原生调用方作用域退役。[事件运行时](../../core/src/runtime-events/index.ts) 在事件 SQL 提交后调用处理器的 `activations(event)`，通过 Core Activation 执行主体并等待物理完成。每个主体的副作用地址由提案、主体、Moment、driver 和 intent 身份共同确定，分支重演沿用相同逻辑地址。Social 行动从事件时钟的 `time` 读取精确十进制时间。 推荐感知要求 `PerceptionScope.logicalTime`，并转换为推荐输入的 `query.time`；直接调用 weighted 策略时显式提供 `query.time`。Social支持非负整数tick及事件 `time`，数字要求安全整数，超大整数使用十进制字符串，其它时标返回明确诊断。衰减先计算BigInt时间差，再按配置计算浮点分数。

所有领域变更接受已接纳的写租约和原生 SQL 事务。正文、查询页面和事务各自有预算；页面保留完整条目，超限会返回错误。

## 二、推荐与呈现

推荐准备、纯排序和业务呈现具有独立入口。

[Recommendations](social-recommendations/index.ts) 从具名 `RecommendationSource` 收集一致快照中的输入，关闭快照后执行可选的异步 `prepare`，随后复查来源版本。来源应在 `dependencies` 中声明其领域版本所有者；`versions(reader)` 使用窄 SQL 读取接口，同时服务于准备复查与呈现事务。

缓存键包含作用域、选定来源、来源版本、generation 和预算。准备结果保留原始条目、来源分页信息及版本；纯排名产生完整分数分解。[排名注册表](social-ranking/index.ts) 检查输出 ID 的完整性、唯一性及有限分数。

`socialPresentation.present(tx, request)` 按 `presentationId` 重放原请求，仅对 `itemIds` 指定的帖子记录曝光，并在同一事务复核相关来源版本。相同标识对应不同请求会失败。信息查询、排名预览、感知读取和排名比较均保持业务曝光不变。

类型化调用路径见[推荐准备示例](../../../examples/recommendation-preparation.ts)与[排名测量示例](../../../examples/recommendation-measurement.ts)。

## 三、语义与感知

[SocialSemantic](social-semantic/index.ts) 使用真实 `embeddings` 与 `vectorProviders` 服务。内容写入在原事务登记 pending；`embedPending` 关闭读取快照后请求向量，再以文本版本与预期修订写回 SQL。原始向量及物理 Attempt/item 来源随记录保存。

`syncIndex` 将有界 SQL 变更流应用到后端，再写入独立的投影进度。SQL 数据足以重建空后端；索引写入后、进度提交前的中断可重放。准备阶段核对完整投影前缀、向量身份和文本版本，过期状态显式返回 `stale`。

[Social 感知](social-perception/index.ts) 把个人资料、投递动态、通知和推荐注册为普通 Core 感知来源。Core 负责原文材料、Thread 关联与游标的共同提交。通知消费和业务呈现使用各自明确的行动入口。 `driver-rule` 通过 `sources` 选择这些来源，`driver-llm` 通过 `perceptions` 选择；两者把当前逻辑时钟传入感知。LLM 使用原生 ActorTools 的 `actions_execute` 调用社会行动，可用 `requiredActions` 声明完成条件，运行器按真实行动回执决定完整点。

## 四、测量

[Measurements](measurements/index.ts) 登记测量和访谈，采样由独立策略决定。成功结果进入[MeasurementRepository](measurement-repository/index.ts)，保留主体、逻辑时间、原始回答、来源与 required 标记；记录的 value/original/source 是 StoredValue，小值内联、大字段按引用保存，完整正文用 `measurementRepository.readPayload` 读取。required 失败使当前推进保持 incomplete。

访谈通过 run 目录之外的 Attempts authority 留存外部事实。大字段按引用保存；超过结果预算的完整 observation 保存在该 authority 的 artifact 中，并由已结算 Attempt 引用；世界事务的失败与恢复保留这些证据。重放不重新观察；ledger 结果未知（含 pending）时报 `MEASUREMENT_ATTEMPT_UNKNOWN`。[排名比较](social-measurement/index.ts) 使用同一份准备输入及已接纳写租约，通过 Core Results 保存策略配置和全部分数组成。

组合者可替换传播、拓扑、来源、排名和采样贡献；其数据归属与完整点规则保持一致。
