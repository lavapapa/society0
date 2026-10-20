# 社交字段消费者矩阵

本表将公开配置与实际消费者、确定性断言放在一起。配置语义以[社交合同](social-contract.md)和 [social_models](../../src/society0/plugins/social_models.py) 为准；新增字段须同步真实消费者及对应测试。测试直接检查领域服务、来源和实际 InputBatch，全程可使用本地确定性资源。

## 一、字段与断言

以下路径以 `SocialNetworkConfig` 为根；`recommendation.*`、`trending.*` 均位于 `social_media`。新增白盒用例集中在 [test_social_field_contracts](../../tests/primary/test_social_field_contracts.py)。`test_public_social_fields_match_reviewed_consumer_scope` 固定已审查字段集合，新增字段将触发该检查；它与具体行为断言共同维护本矩阵。

| 字段 | 实际消费者 | 明确断言 |
|---|---|---|
| `recommendation.feed_content_preview_chars` | `social_source_plugin → SocialPerception._new_posts → CognitiveInput/InputBatch` | None 默认完整；显式 3 字符截取中文/emoji；0 保留空正文及完整原文路径、总长度 |
| `recommendation.feed_max_chars` | 同上，按新帖呈现次序分配 content 字符预算 | 总预算 10 返回 7/3/0 字符；每帖 4、总预算 6 返回 4/2/0；等于全文长度时无截断 |
| `trending.enabled` | `_trending_ids`，供 `Social.trending`、内容投影更新和认知注入共用 | False 返回空热门；动作返回空帖子；投影更新为空 |
| `trending.calculation_window_ticks` | `_trending_ids` 创建时间索引范围查询 | tick=9，窗口 5 保留 tick=5/8；窗口 2 保留 tick=8；旧帖新点赞仍排除；`update_trending_topics(9)` 与动作一致 |
| `trending.inject_into_feed` | `Content.feed_ids → SocialPerception / Social.recommended_feed` | False 保持时间序前两条；True 优先热门，实际 InputBatch 与完整 feed 返回同一 id 次序 |
| `trending.injection_count` | 同上 | 0/1/2 分别注入 0/1/2 条；可呈现活动池外热门；总数保持 post_count |
| `social_media.enabled` | `Content` 写入资格、`Recommendations._input`、`ActivePool`、presentation 注册、来源选帖 | False 拒绝直接/动作发帖，推荐与热门为空；媒体查询不可用；已有网络关系及 follow 保留 |
| `recommendation.candidate_count` | `Semantic.similarities → collection.query(n_results=...)` | 大池 candidate_count=2、倍数3请求6条；4×2请求8条；post_count=7作为下限；小池忽略该基数 |
| `recommendation.recall_multiplier` | 同上 | 通过真实 collection.query 参数核对乘法和 post_count 下限 |
| `recommendation.time_decay_hours` | `WeightedRanker.rank` | 尺度 0/-1/2/12 对比 `exp(-12/(scale if scale>0 else 1))`，字段说明与公式一致 |
| `recommendation.post_count` | 来源与 `recommended_feed` 选帖；语义请求下限 | 注入维持 2 条上限；向量请求下限为 7；正文预算保持 id 集合 |

预览用例直接保存 InputBatch 到真实 Thread 后执行 effects，再次构建同 Moment 输入，核对无重复新帖正文、旧消息完整、曝光恰为一次。每条预算耗尽的帖子仍由 Information 完整读取原文。排名字段及动作集合保持独立。

## 二、既有消费者

本批保留已有机制及其确定性证据；下表指明字段最终读取点，便于变更时定位消费者边界。

| 字段 | 实际消费者 | 既有断言入口 |
|---|---|---|
| `recommendation.chronological_weight / engagement_weight / network_weight / follow_bonus` | `WeightedRanker.rank` 各分量 | [test_plugin_social](../../tests/primary/test_plugin_social.py) 的 `test_social_active_pool_and_explicit_weighted_scores_without_body_scan` 核对完整分量与总分 |
| `recommendation.similarity_weight / use_embedding_similarity` | 配方语义资源装配、`Semantic.similarities`、`WeightedRanker.rank` | [test_plugin_social](../../tests/primary/test_plugin_social.py) 的嵌入批次/恢复测试及 [test_social_composition_ranking](../../tests/primary/test_social_composition_ranking.py) 的策略消费者测试 |
| `recommendation.like_score / reply_score / repost_score` | `Engagement.update_in` 原子更新 posts.engagement | `test_scoring_fields_reach_transaction_projection_and_pure_ranker` 配置 2/3/4 分，点赞+评论+转发后 SQL 候选投影为 9 |
| `recommendation.full_scan_until / recent_keep_count / top_engagement_keep_count / min_lifetime_ticks` | `ActivePool.active_pool_in` 全量/近期/互动/生命周期候选并集 | [test_plugin_social](../../tests/primary/test_plugin_social.py) 的活动池测试固定候选 id；本批语义测试区分 full_scan_until 两侧 |
| `recommendation.include_recent_posts_in_query / include_following_in_query / recent_post_limit / interaction_limit` | `SocialData.preference_text_in` 构建真实嵌入查询文本 | `test_query_fields_reach_actual_preference_text` 核对关闭、近期帖子上限1、互动上限1、关注文本；[test_plugin_social](../../tests/primary/test_plugin_social.py) 再核对同 tick 互动顺序 |
| `social_media.content_length_limit` | `Content._content_valid` 发帖、评论、转发写入资格 | [test_plugin_social](../../tests/primary/test_plugin_social.py) 核对 251 字符拒绝、无限原文与恢复 |
| `distribution.type / is_directed`、各拓扑参数 | `social_data_plugin.initialize` 原生 random/complete，其他转 `generate_topology` | [test_plugin_social](../../tests/primary/test_plugin_social.py) 五种分布固定种子、规范边、方向及恢复不重建 |
| `distribution.params.connection_probability` | 原生随机边或 `generate_topology` 随机图 | 0/1 概率的明确边集合 |
| `distribution.params.k_neighbors / rewiring_probability` | 小世界生成 | 固定种子拓扑、零重连的明确边集合 |
| `distribution.params.m_edges` | 无标度生成 | 固定种子边数及两节点明确边集合 |
| `distribution.params.target_cv_mean / base_algorithm / base_params / max_iterations / convergence_threshold` | `generate_topology → _traditional` 及 CV 均值调整循环 | CV 固定种子及目标0/1的明确边集合；各调整参数的独立敏感性仍属后续拓扑白盒细化范围 |

`CVTargetedParams.target_cv_std` 已删除：当前 CV 算法优化均值，源码未读取标准差目标；其余 CV 参数继续由实际初始化和调整循环消费。

该矩阵与字段集合检查留在测试和文档层，运行期继续使用现有模型与服务。尚未覆盖的研究效果、真实模型响应及拓扑每一参数的单独敏感性，与本批字段消费者的确定性验收分别记录。
