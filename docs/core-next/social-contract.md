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

[独立推荐观测插件](../../examples/core_next/recommendation_observer.py) 通过 `source=('social.recommendation','recommendation')` 消费输入，将多个具名 rank 服务的结果写入自己的纯数据表。该消费者无须安装 presentation、content 或 exposure，也不会登记曝光。

## 三、信息与呈现

主体通过动态行动发现发布、点赞、评论、转发、关注与取消关注，执行时重验成员资格及目标。读取帖子、公开资料、热门帖子与消费通知有显式行动入口。公开资料保留类型、架构、兴趣、心情、社交统计与近期原文预览；私有人格留在主体认知范围。

`/social/posts`、`participants`、`replies` 支持结构化查询；posts 元数据携带 `content_path`。`content/<id>`、`reply_content/<id>` 提供完整原文字节范围，`notifications` 提供未读条目，`notification_data/<id>` 核验收件人并保留消费后的原始内容。热门查询使用全局热门索引，与活动推荐候选池分别取数。

`/social/feed` 是只读推荐预览，按排名分页返回候选元数据、分数、Ref、正文路径及精确总数。游标绑定运行、主体、Moment、相关领域和权限版本；Thread 诊断写入不使其过期。页面查询不登记曝光。`recommended_feed` 是显式程序呈现入口，按配置 post_count 取得完整帖子并默认登记曝光。

`social_cognition_plugin` 提供 `input_builder`，包装 CognitiveInput，供 LLM 驱动显式绑定。Thread 中的感知位置保存 `seen`、`visible` 和 `recommended`：新帖提供完整详情，已见帖提供变化的元数据，推荐顺序变化保留新 id 序列。同 Moment 重激活与完整点恢复延续该位置，既有原文继续保留在完整 Thread。曝光及推荐 id 更新通过批次 `effects` 延后到实际追加成功之后，见 [认知合同](cognition-contract.md)。直接调用其 `perception` 服务生成材料保持无呈现效果。

## 四、完整步骤与资源

exposure 叶在 after-step 提交曝光计数和最后推荐 id；semantic 叶在 after-step 完成待写嵌入并推进派生索引。Runtime 收束已登记回调后发布完整步骤。直接使用底层服务的消费者须显式执行等价收束。单次内容行动在共享事务内维护事实、计数与通知；步骤失败从此前可信完整点恢复。

原始向量保存在 SQL，恢复重建派生集合。semantic 拥有异步任务，通过 on_quiesce 取消并等待自有调用，然后由依赖插件关闭外部资源。嵌入等待期间偏好或候选版本改变会拒绝过期结果。presentation 拥有并关闭其信息 provider，Information 挂载借用该资源。

## 五、工作量与证据

拓扑初始化成本随所选算法及成员规模变化；常规计数、关系与内容写入使用当前索引，正文冷表独立保存。范围读取按实际请求字节物化，显式完整详情保留完整读取成本。

推荐及语义分数使用按主体的有界 LRU，`cache_capacity` 默认 128，可通过配方或叶工厂配置。版本相同的排序快照保存候选元数据和排名，不缓存帖子全文；命中后分页为 O(page) 的元数据构造，加上依赖版本核验。冷查询、版本变更或淘汰后需要重建活动候选、评分和排序；语义路径另含完整偏好读取、嵌入与向量查询。冷恢复仍包含历史状态和派生索引重建，成本未因此改善。

显式 `intervene` 扫描需要检查的正文，成本属于采用该规则的步骤。`update_trending_topics` 通过当前热门索引更新短投影。

逐笔对照范围与合同差异见 [旧新对照报告](../../research/core-next/composable-mechanisms/oracle-report.md)；性能口径、冷恢复限制与复现入口见 [分项成本报告](../../research/core-next/composable-mechanisms/cost-report.md)。这些报告分别限定确定性语义与资源测量的证据范围。
