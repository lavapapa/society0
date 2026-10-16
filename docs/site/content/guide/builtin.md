# 选择内置环境机制

研究里主体之间怎么互动，决定该装哪个内置机制：plain 是空白基准，round-robin 提供轮次伙伴对话，social 提供开放社交网络，三者都接入同一次共享运行。开始之前先备好[主体目录](actor-directory.md)、interaction 和[调度](schedule.md)。

## 按研究任务挑工厂

| 研究需要 | 工厂 | 由研究计划决定 |
| --- | --- | --- |
| 规则或模型自身的判断基准 | `plain_plugin` | 输入、行动、测量和时间 |
| 固定成员的小组轮次交流 | `round_robin_plugin` | 成员顺序、组大小、何时开启轮次 |
| 帖子传播、关注与推荐 | `social_plugin` | 拓扑、策略、主体认知和曝光规则 |
| 大批原始资料 | `dataset_plugin` | 资料归属和读取授权，见[数据集](datasets.md) |

plain 不添加任何领域状态或行动，所以装上它之后，主体能读什么、能做什么都要在自己的计划里写清楚。轮次对话和 social 用的是同一份主体数据目录，多个机制可以共用一批身份。

## 同时装两个机制

下面这段装配片段放进已经包含 actors、interaction、Runtime 和 Schedule 的插件列表。四位成员必须已经登记；用自定义人口目录时，通过 `actors` 指定实际的数据插件名。

```python
from society0.plugins import round_robin_plugin, social_plugin

members = ("alice", "bob", "cora", "dan")
mechanisms = (
    round_robin_plugin(members, group_size=4, name="dialogue"),
    social_plugin(members, name="forum", edges=(("alice", "bob"),)),
)
```

领域阶段里用 `host.service("dialogue", "mechanism")` 或 `host.service("forum", "mechanism")` 取服务。运行中的阶段都从自己的服务引用拿到这些对象；共享主体、多机制工厂的完整写法见[对话教程](../tutorial/conversation.md)。

round-robin 的成员要不重复，能分成完整的偶数组，组大小在 2 到 20 之间。分组按传入顺序进行，然后生成 circle 配对计划。阶段调用 `start_round` 之后，主体才能通过 Actions 发消息；同一轮次重新进入会保留原来的配对，往已经过去的轮次回退会被拒绝。

## 主体在哪里找到资料

轮次机制对外公开成员、本人当前和未来的配对计划、伙伴历史、消息与完整历史。实例名为 dialogue 时，Information 路径从 `/dialogue` 开始，LLM 文件视图从 `/world/dialogue` 开始。先列目录，再读具体数据集的元数据和分页内容；组内成员和计划按访问的主体判断哪些可见。

social 的帖子、公开资料、回复、通知和 feed 都在实例路径下面。文件读取用来查阅，发布、点赞、关注或消费通知要走已经登记的领域行动。想让推荐直接进入模型认知，就加上对应的 social_source_plugin，再交给 cognition_plugin 组合，见[社交指南](social.md)。

## 跑两步验证一下

先用规则主体跑两步验证：第一步开启轮次、发一条已知消息，第二步读收件箱和历史，核对发送者、接收者和完整正文。社交运行则发一条帖子，再看目录、行动结果、推荐预览和实际呈现带来的曝光变化。

正式 Runtime 会收集机制登记的步骤回调；social 的曝光和语义任务在 after 钩子里完成，早于完整步骤发布。直接调用底层服务，就要自己承担同样的收尾工作。语义推荐默认关闭；启用时把 embedding 和 vector client 一起接上，具体接线见[社交指南](social.md)。

用不同名字注册多个实例之后，逐个核对服务引用、成员和路径。公开工厂和信息接口见[轮次对话参考](../reference/round-robin.md)、[社交参考](../reference/social.md)，运行后的展示见[工作台](workbench.md)。
