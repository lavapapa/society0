# 接入社交推荐与实际曝光

要让主体在论坛里发帖、关注别人，还要把推荐内容送进模型输入，就得把社交事实、排序策略和认知来源接到一起。先安装 social 可选依赖，备好成员目录和 interaction；要接模型还需要 llm 依赖与 threads。先用固定关系边、不开语义推荐跑一遍领域行为，再接外部嵌入服务。

## 换一个排序策略

把下面这段装配片段加进已有计划的插件列表。`members` 是已经登记的主体 ID 序列。

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

默认配方用加权排序，这里明确换成 chronological 策略。模型通过共享 Actions 发现、描述并调用发布、点赞、评论、转发和关注这些行动。先发两条已知帖子，再在同一模拟时点取推荐结果，检查返回的候选身份、次序和完整总数。

替换进来的策略接收不可变的 `RankingInput`，返回的 `RankingOutput` 要保留全部合格候选和输入的 revision。策略只碰排名值，点赞、关系和帖子这些领域事实仍然由原机制保存。要比较几个策略，可以复用[独立推荐观测插件](https://github.com/lavapapa/society0/blob/@COMMIT@/examples/core_next/recommendation_observer.py)，把同一份输入交给多个 rank 服务。

## 把推荐接进主体认知

名为 forum 的实例，每个叶服务都要逐项引用。下面这段装配片段放在同一计划里，另外需要 threads。

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

驱动绑定 `input_builder=("cognition", "input_builder")`。新帖第一次出现时来源交付完整内容；见过的帖子只更新元数据，排序变了就给出新的 ID 序列。每个来源自己的位置分别保存 seen、visible 和 recommended，完整 Thread 里已有的原文继续留着。

## 分清预览和真正呈现

`/forum/feed` 的分页查询只是只读的排名预览；`recommendation_input` 和 `recommendation` 也不登记曝光。模型输入走 social_source_plugin 时，推荐原文写进 Thread 之后才确认实际呈现，曝光叶在步骤末尾提交相关事实。

直接调用 `recommended_feed` 时默认登记呈现；如果只是研究者自己预览，显式传 `record_impressions=False`。同一次呈现只登记一次，自定义代码和来源插件不要重复登记。验证时先看预览前后的曝光记录是否不变，再看一次实际输入、以及完整步骤发布后对应记录的变化。

## 开启语义推荐

语义推荐默认关闭。开启时要把嵌入 profile 和向量客户端两个服务引用一起提供。下面的装配片段替换第一节里的 social_plugin；两个资源的拥有者必须已经加进同一计划，实际配置见[模型与嵌入](models.md)。

```python
forum = social_plugin(
    members, name="forum", edges=(("alice", "bob"),),
    config={"social_media": {"recommendation": {"use_embedding_similarity": True}}},
    embedding=("embeddings", "semantic"),
    vector_client=("vectors", "client"),
)
```

social 的 embedding 参数用 `(插件名, profile名)` 两段，例子取的是 embeddings 插件的 semantic profile；vector_client 用 `(插件名, 服务名)`。语义开关关着就把这两个资源省掉；开着却少任何一个，装配时会报错。嵌入和向量索引要匹配实际模型维数，原始向量由 SQL 保存，索引可以从权威数据重建。等网络期间不持有长时间的 SQL 读取事务，返回后会核对来源版本；来源已经变了，就按新的快照重新请求。

## 核对组合边界

配方用 includes 展开成 data、content、relations、notifications、exposure、recommendation 这些叶。要换成外部候选源或关系源，用具名引用接入，并把实际读到的表全部声明出来，同时使用同一个 StageStore；这些依赖决定缓存和分页对哪个版本有效。一次运行里的策略、来源和配置保持不变，要改就从可信的完整步骤另建一次新运行。

打开运行清单确认实际的叶和引用，再检查帖子原文、动作结果、推荐次序和曝光归属。精确参数见[社交参考](../reference/social.md)，叶配方见 [social_plugin](https://github.com/lavapapa/society0/blob/@COMMIT@/src/society0/plugins/social.py#L393-L416)，来源确认流程见 [SocialPerception](https://github.com/lavapapa/society0/blob/@COMMIT@/src/society0/plugins/social_cognition.py#L14-L53)。
