# 接入社交推荐与实际曝光

本页将社交事实、推荐排序和模型认知连接起来。先安装 social 可选依赖，准备成员目录和 interaction；接入模型还需 llm 依赖与 threads。先用固定关系边和无语义推荐检查领域行为，再接外部嵌入服务。

## 一、选择排序策略

在已有计划的插件列表中加入以下接线片段。`members` 是已登记的主体 ID 序列。

```python
from society0.plugins import social_plugin, chronological_ranking_plugin

social_plugins = (
    chronological_ranking_plugin(name="timeline"),
    social_plugin(
        members, name="forum", edges=(("alice", "bob"),),
        ranking=("timeline", "rank"),
    ),
)
```

默认配方使用加权排序；这里明确替换成 chronological 策略。模型按共享 Actions 发现、描述并调用发布、点赞、评论、转发和关注等行动。先发布两条已知帖子，再在同一模拟时点取得推荐结果，检查返回的候选身份、次序和完整总数。

替换策略接收不可变 `RankingInput`，返回保留全部合格候选与输入 revision 的 `RankingOutput`。策略只处理排名值，领域的点赞、关系和帖子事实继续由原机制保存。需要比较多个策略时，可复用[独立推荐观测插件](https://github.com/lavapapa/society0/blob/@COMMIT@/examples/core_next/recommendation_observer.py)，将同一输入交给多个 rank 服务。

## 二、接入主体认知

为名为 forum 的实例逐项引用它的叶服务。以下为同一计划中的接线片段，还需 threads：

```python
from society0.plugins import social_source_plugin, cognition_plugin
from society0.plugins.cognition import SourceSpec

cognitive_plugins = (
    social_source_plugin(
        name="forum.source",
        recommendations=("forum.recommendation", "recommendation"),
        content=("forum.content", "content"),
        exposure=("forum.exposure", "exposure"),
        data=("forum.data", "data"),
    ),
    cognition_plugin(
        sources=(SourceSpec("forum.v1", ("forum.source", "source")),),
        environment="在论坛中讨论资料并核对来源。",
    ),
)
```

驱动使用 `input_builder=("cognition", "input_builder")`。来源首次交付完整新帖；已见帖更新元数据，排序变化提供新的 ID 序列。独立来源位置保存 seen、visible 和 recommended，完整 Thread 中已有原文继续保留。

## 三、区分预览与呈现

`/forum/feed` 的分页查询用于只读排名预览；`recommendation_input` 与 `recommendation` 也不登记曝光。模型输入使用 social_source_plugin 时，推荐原文写入 Thread 后确认实际呈现；曝光叶在步骤末提交相关事实。

直接调用 `recommended_feed` 默认登记呈现。若只是研究者预览，显式传 `record_impressions=False`。不要同时让自定义代码和来源插件重复登记同一呈现。验证时，先比较预览前后的曝光记录应保持不变，再检查一次实际输入与完成步骤后对应记录的变化。

## 四、增加语义推荐

语义推荐默认关闭。开启时，同时提供嵌入 profile 和向量客户端服务引用。以下接线片段替换第一节中的 social_plugin；两个资源拥有者须已经加入同一计划，实际配置见[模型与嵌入](models.md)。

```python
forum = social_plugin(
    members, name="forum", edges=(("alice", "bob"),),
    config={"social_media": {"recommendation": {"use_embedding_similarity": True}}},
    embedding=("embeddings", "semantic"),
    vector_client=("vectors", "client"),
)
```

social 的 embedding 参数使用 `(插件名, profile名)`，上例取得 embeddings 插件的 semantic profile；vector_client 使用 `(插件名, 服务名)`。关闭语义开关时省略这两个资源；开启却缺失任一个会在装配时报错。嵌入与向量索引须匹配实际模型维数，原始向量由 SQL 保存，索引可从权威数据重建。网络等待期间不持有长 SQL 读取事务，返回后会核对来源版本；来源已经变化时，按新快照重新请求。

## 五、检查组合边界

配方用 includes 展开为 data、content、relations、notifications、exposure、recommendation 等叶。外部候选源或关系源通过具名引用替换时，声明全部实际读取的表，并使用同一 StageStore；这些依赖控制缓存和分页的版本有效性。一次运行中的策略、来源与配置保持固定，改变它们时从可信完整点创建新运行。

查看运行清单确认实际叶与引用，再检查帖子原文、动作结果、推荐次序和曝光归属。精确合同见[社交参考](../reference/social.md)，叶配方见 [social_plugin](https://github.com/lavapapa/society0/blob/@COMMIT@/src/society0/plugins/social.py#L393-L416)，来源确认流程见 [SocialPerception](https://github.com/lavapapa/society0/blob/@COMMIT@/src/society0/plugins/social_cognition.py#L14-L53)。
