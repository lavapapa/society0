# 社交机制

社交插件在共享环境中提供帖子、关系、通知与推荐。主体资料借用 ActorDirectory；同类平台使用显式实例名、服务引用和独立表前缀。领域事实由共享 SQL 保存，语义索引作为可选的可重建资源。

## 一、组合

`social_plugin(members, name='social', config=..., edges=..., seed=..., ranking=None, cache_capacity=128)` 是静态配方，通过 `Plugin.includes` 装配叶插件。工厂从 `society0.plugins` 按需导入；依赖图合同见 [插件合同](plugin-contract.md)。

| 叶插件后缀 | 服务与责任 |
|---|---|
| data | `data`：领域 schema、成员、配置和初始关系，借用 `actors.data.directory` |
| notifications | `notifications`：通知事实与消费 |
| relations | `relations`：关系写入与通知 |
| engagement | `engagement`：配置化互动计分投影 |
| content | `content`：帖子、点赞、回复、转发及事务内关联写入 |
| candidates | `candidates`：活动推荐池与全局热门索引查询 |
| exposure | `exposure`：实际呈现登记与步骤末提交 |
| semantic | `semantic`：可选嵌入任务、原始向量与派生索引 |
| recommendation | `recommendation`：公开推荐输入、策略调用与版本缓存 |
| presentation | `presentation`、`information`：Information 路由与行动注册 |

根配方的 `mechanism` 服务指向 presentation。各叶可以独立安装；数据初始化先决使用 `schema_requires`，运行借用使用 `requires`。成员目录由 [actor_data_plugin](actor-contract.md) 提供，驱动装配可再依赖机制输入，避免将运行映射加入数据依赖链。

`config.social_media.recommendation.use_embedding_similarity` 是语义功能的唯一开关，默认 False。True 要求同时绑定 `embedding=('embeddings','profile')` 与 `vector_client=('vectors','client')`；False 时传入任一资源会报错。无语义配方不安装 semantic 叶或向量表。关系生成支持既有拓扑模型，也可显式提供 edges；seed 固定初始化随机源，恢复读取持久关系与配置。正文长度限制为 -1 时不设业务上限，与模型预算及 Thread 历史无关。

## 二、推荐策略

默认配方绑定 `weighted_ranking_plugin` 的 `rank` 服务。替换策略通过具名引用完成，例如：

```python
from society0.plugins import chronological_ranking_plugin, social_plugin

plugins = [
    chronological_ranking_plugin(name='timeline'),
    social_plugin(members, ranking=('timeline', 'rank')),
]
```

上述片段加入已有主体目录与 interaction 的组合。外部策略提供 `rank(RankingInput) -> RankingOutput`。输入包含主体、时点、候选元数据、关注集合、可选语义分数及 revision；不携带帖子全文或服务定位器。候选与输出序列不可变，similarities、by_id 和评分 components 为只读映射。输出保留全部合格候选身份、精确 total 与输入 revision，推荐服务核验这些条件。策略切换保留点赞、回复、转发等事实；加权计分与 chronological 的稳定排序规则见 [公开值对象及策略](../../src/society0/plugins/social_ranking.py)。

[独立推荐观测插件](../../examples/core_next/recommendation_observer.py) 通过 `source=('social.recommendation','recommendation')` 消费 `await recommendation_input(actor, tick, query=None)`，对同一个完整输入比较多个具名纯值 rank 服务，并写入自己的数据表。`await recommendation(...)` 返回 `(RankingInput, RankingOutput)`，复用按主体的完整排名缓存；两个入口都不登记曝光。

### 2.1 SQL 来源

推荐叶的 `candidates` 与 `relations` 通过具名插件服务引用绑定。[SQLCandidates / SQLRelations](../../src/society0/plugins/social_recommendation.py) 定义同一 StageStore 的结构接口：两者均有只读 `dependencies: tuple[str, ...]` 属性与 `store`；前者提供同步 `active_pool_in(view, tick) -> tuple[Candidate, ...]`，后者提供 `following_in(view, actor) -> frozenset[str]`。来源声明全部实际读取的表，包括外部选择/关系表、联结元数据和筛选条件；在传入短 ReadView 内物化结果。安装时核对同一 store、接口和表存在，拒绝错误声明。候选输出须为完整、唯一身份的元组。

原生 ActivePool 声明 head/posts，Relations 声明 edges，成员资格声明 members。语义启用时再合并人格门面、近期原帖与正文、互动与回复、原生关注、vectors/embedding_pending。默认偏好文本中的关注仍取原生 edges；外部 relations 改变排名的 following。需要改变偏好语义的插件同步提供相应文本入口与依赖。

推荐首先做声明依赖的轻量版本与成员检查；未命中时先完成正文向量准备，再在一个 ReadView 内读取成员、版本、候选、关系和完整偏好。向量查询使用该不可变输入，SQL 视图在网络等待前释放，等待返回后核对完整依赖版本与派生集合水位。来源变化抛 ValueError，调用者显式请求下一次读取。ranker 同步消费纯值输入，成功结果保留完整候选、评分和原始正文入口。

### 2.2 静态配置

一次 Run 的来源、依赖与策略绑定保持固定。weighted 在构造时复制有效 RecommendationConfig，公开配置字段只读；Recommendations.ranker 是只读绑定。调用者修改原配置副本不影响当前服务。切换策略或配置通过新 Run/compose 装配，可从可信完整点恢复，缓存从空开始。输入与参数的来源身份记录在 RunContract.configuration，源码身份与依赖由运行合同声明。

## 三、信息与呈现

主体通过动态行动发现发布、点赞、评论、转发、关注与取消关注，执行时重验成员资格及目标。读取帖子、公开资料、热门帖子与消费通知有显式行动入口。公开资料保留类型、架构、兴趣、心情、社交统计与近期原文预览；私有人格留在主体认知范围。

`/social/posts`、`participants`、`replies` 支持结构化查询；posts 元数据携带 `content_path`。`content/<id>`、`reply_content/<id>` 提供完整原文字节范围，`notifications` 提供未读条目，`notification_data/<id>` 核验收件人并保留消费后的原始内容。热门查询使用全局热门索引，与活动推荐候选池分别取数。

`/social/feed` 是只读推荐预览，按排名分页返回候选元数据、完整分数、Ref、正文路径及精确总数。游标以 JSON 明文绑定运行、主体、Moment、路径、实际排名版本与权限版本。Page.revision 为 `[ranking_revision, access_revision]`；每一页核对 source/output/页面排名版本一致，并核对请求前后权限版本。首屏允许正文向量准备产生的新排名版本，已有续页遇相关变化明确过期。无关 Thread 诊断与 observer 写入保持旧页有效。feed 和平台根目录的 search_revision 同样反映实际外部来源版本。

页面查询不登记曝光。`recommended_feed` 按配置 post_count 取得完整帖子并默认登记曝光；预览调用可显式关闭这一效果。传输页大小、预览字符配置均不改变可继续取得的完整集合、原文字节与九项 weighted 评分。

`social_cognition_plugin` 提供 `input_builder`，包装 CognitiveInput，供 LLM 驱动显式绑定。Thread 中的感知位置保存 `seen`、`visible` 和 `recommended`：新帖提供完整详情，已见帖提供变化的元数据，推荐顺序变化保留新 id 序列。同 Moment 重激活与完整点恢复延续该位置，既有原文继续保留在完整 Thread。曝光及推荐 id 更新通过批次 `effects` 延后到实际追加成功之后，见 [认知合同](cognition-contract.md)。直接调用其 `perception` 服务生成材料保持无呈现效果。

## 四、完整步骤与资源

exposure 叶在 after-step 提交曝光计数和最后推荐 id；semantic 叶在 after-step 完成待写嵌入并推进派生索引。Runtime 收束已登记回调后发布完整步骤。直接使用底层服务的消费者须显式执行等价收束。单次内容行动在共享事务内维护事实、计数与通知；步骤失败从此前可信完整点恢复。

原始向量保存在 SQL，恢复重建派生集合。semantic 借用推荐已物化的输入，提供 `dependencies`、`query_text_in` 和 `similarities(source, text=..., dependencies=...)`，不独立枚举候选。派生集合的 through 与 SQL 最大向量 ordinal 对应，查询前后核对来源与向量版本；原始向量追加是正式生产路径。

Host 先等待 Runtime idle，再执行 semantic 的 worker 阶段停止，最后关闭依赖资源。presentation 在创建 provider 后立即托管关闭，并逐项托管 Actions/Information 返回的注销函数；安装中途失败时撤回已成功的贡献。Information 挂载借用该资源。

## 五、工作量与证据

拓扑初始化成本随所选算法及成员规模变化；常规计数、关系与内容写入使用当前索引，正文冷表独立保存。范围读取按实际请求字节物化，显式完整详情保留完整读取成本。

推荐及语义分数使用按主体的有界 LRU，`cache_capacity` 默认 128，可通过配方或叶工厂配置。版本相同的排序快照保存候选元数据和排名，不缓存帖子全文；命中后分页为 O(page) 的元数据构造，加上依赖版本核验。冷查询、版本变更或淘汰后需要重建活动候选、评分和排序；语义路径另含完整偏好读取、嵌入与向量查询。冷恢复仍包含历史状态和派生索引重建，成本未因此改善。

显式 `intervene` 扫描需要检查的正文，成本属于采用该规则的步骤。`update_trending_topics` 通过当前热门索引更新短投影。

逐笔对照范围与合同差异见 [旧新对照报告](../../research/core-next/composable-mechanisms/oracle-report.md)；性能口径、冷恢复限制与复现入口见 [分项成本报告](../../research/core-next/composable-mechanisms/cost-report.md)。这些报告分别限定确定性语义与资源测量的证据范围。
