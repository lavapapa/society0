# 社交机制与推荐

`from society0.plugins import social_plugin`。社交机制提供帖子、关系、通知、推荐和实际呈现记录；主体资料来自 ActorDirectory。多平台用显式实例名和服务引用隔离领域表与能力。工厂借用你传入的存储、主体目录与交互服务，这些资源由环境持有；命名空间下的叶服务和向量表由实例自己登记，运行结束后随主机退出撤销。

## 工厂与叶服务

social_plugin 返回包含叶插件的普通 Plugin，根 `mechanism` 服务指向 presentation。

<!-- api: society0.plugins.social.social_plugin -->
<!-- api: society0.plugins.social_models.SocialNetworkConfig -->
<!-- api: society0.plugins.social_models.RecommendationConfig -->

叶服务按职责拆分为 data、notifications、relations、engagement、content、candidates、exposure、recommendation、presentation，以及可选 semantic。schema_requires 定义数据前提，requires 定义运行借用关系。独立替换叶服务时应保持真实依赖和资源归属。

`config.social_media.recommendation.use_embedding_similarity` 默认 False。启用时必须同时提供 embedding 与 vector_client 引用；关闭时传入任一语义资源会失败。无语义配方不安装 semantic 叶及向量表。初始化可传 edges 或使用配置拓扑，seed 固定初始化随机源，恢复使用持久配置及关系。

## 推荐与呈现

推荐策略消费纯值输入，结果保持完整合格候选、精确总数及同一 revision。

<!-- api: society0.plugins.social_ranking.RankingInput -->
<!-- api: society0.plugins.social_ranking.RankingOutput -->
<!-- api: society0.plugins.social_ranking.WeightedRanker.rank -->
<!-- api: society0.plugins.social_ranking.ChronologicalRanker.rank -->
<!-- api: society0.plugins.social.Social.recommendation -->
<!-- api: society0.plugins.social.Social.recommended_feed -->

recommendation 返回 `(RankingInput, RankingOutput)`；recommendation_input 可供独立策略比较，两者均不登记曝光。默认 weighted 综合时间、互动、关系和可选语义得分；chronological 按创建 tick 与帖子 ID 降序排列。策略与配置绑定在一次运行内固定，改变绑定通过新 Run 装配。

`/world/<实例名>/feed` 为只读预览，分页保留得分、Ref、正文路径及精确总数。recommended_feed 返回配置数量的完整帖子，默认登记曝光，预览可传 record_impressions=False。`present` 接收消费者确认实际呈现的 ID，曝光在此时登记。

<!-- api: society0.plugins.social_cognition.social_source_plugin -->
<!-- api: society0.plugins.social_cognition.SocialPerception -->

社交认知来源维护 seen、visible、recommended：新帖提供完整详情，已见帖提供变化元数据，排序变化保留新 ID 序列。SourceBatch 构建保持只读；输入与消费位置成功保存后执行 effects，登记新呈现帖并更新推荐 ID。重复激活仍保留原 Thread 和各来源位置。

## 行动、时间与资源

领域动作经发现与参数描述进入发布、点赞、评论、转发、关注、通知消费等入口，执行时重验成员和目标。正文通过 content、reply_content 等路径范围读取。通知查询与消费动作分开，消费后原文仍可追溯。社交 tick 来自领域调用时间；WeightedRanker 的时间分量按 `exp(-max(tick-created_tick, 0)/time_decay_hours)` 计算，参数名含 hours，实际单位取决于传入 tick 的研究定义。

内容写入同事务维护事实、计数及通知。exposure 在步骤末提交计数，semantic 完成待写嵌入并推进索引，随后 Runtime 发布完整步骤。直接使用底层服务须执行等价收束。SQL 原始向量支持恢复重建派生索引，Host 在关闭依赖前等待运行与语义 worker 收束。

推荐缓存按主体有界保存候选元数据和排序；命中后分页成本取决于本页和版本核验。冷查询重建候选、评分与排序，语义开启后另需偏好原文、嵌入和向量检索。来源在网络等待期间改变时明确失败，调用方重新请求；feed 续页版本变化同样拒绝混读。

```python
from society0.plugins import social_plugin, chronological_ranking_plugin

plugins = [
    chronological_ranking_plugin(name="timeline"),
    social_plugin(["alice", "bob"], edges=[], ranking=("timeline", "rank")),
]
# 与已含这两个主体的目录、storage、interaction 一起装配。
```

完整装配见[社交指南](../guide/social.md)，来源组合见[认知接口](cognition.md)。源码入口为 [social_plugin](https://github.com/lavapapa/society0/blob/@COMMIT@/src/society0/plugins/social.py#L393)。
