# 选择内置环境机制

先根据研究中的交互规则选择机制，再配置驱动。plain 适合空白基准，round-robin 提供轮次伙伴对话，social 提供开放社交网络；它们都接入同一共享运行。准备条件是[主体目录](actor-directory.md)、interaction 与[调度](schedule.md)。

## 一、从研究任务选择

| 研究需要 | 工厂 | 由研究计划决定 |
| --- | --- | --- |
| 规则或模型自身的判断基准 | `plain_plugin` | 输入、行动、测量和时间 |
| 固定成员的小组轮次交流 | `round_robin_plugin` | 成员顺序、组大小、何时开启轮次 |
| 帖子传播、关注与推荐 | `social_plugin` | 拓扑、策略、主体认知和曝光规则 |
| 大批原始资料 | `dataset_plugin` | 资料归属和读取授权，见[数据集](datasets.md) |

plain 不添加领域状态或行动。安装空白机制后，要在计划中明确主体可以读取和执行的内容。轮次对话和 social 使用共享的主体数据目录，多个机制可复用同一批身份。

## 二、装配两个机制

下面是接线片段，放入已经包含 actors、interaction、Runtime 和 Schedule 的插件列表。四位成员须已登记；使用自定义人口目录时，通过 `actors` 指定实际数据插件名。

```python
from society0.plugins import round_robin_plugin, social_plugin

members = ("alice", "bob", "cora", "dan")
mechanisms = (
    round_robin_plugin(members, group_size=4, name="dialogue"),
    social_plugin(members, name="forum", edges=(("alice", "bob"),)),
)
```

在领域阶段通过 `host.service("dialogue", "mechanism")` 或 `host.service("forum", "mechanism")` 取得服务。运行中的阶段从自己的服务接线取得这些对象；完整的共享主体、多机制工厂可参考[对话教程](../tutorial/conversation.md)。

round-robin 的成员必须唯一，分成完整的偶数组，组大小取 2 到 20。按传入顺序分组，再生成 circle 配对计划。由阶段调用 `start_round`，后续主体才通过 Actions 发送消息；相同轮次重入保留配对，向过去轮次回退会被拒绝。

## 三、让主体发现资料

轮次机制公开成员、本人当前与未来配对计划、伙伴历史、消息与完整历史。对于名为 dialogue 的实例，Information 路径从 `/dialogue` 开始，LLM 文件视图从 `/world/dialogue` 开始。先列目录，再读取具体数据集的元数据与分页内容；组内成员和计划按访问主体裁定可见性。

social 的帖子、公开资料、回复、通知和 feed 位于实例路径下。文件读取用于查阅；发布、点赞、关注或消费通知通过登记的领域行动完成。若希望推荐直接进入模型认知，增加对应 social_source_plugin，再由 cognition_plugin 组合，见[社交指南](social.md)。

## 四、验证一次短运行

先用规则主体验证两步：第一步开启轮次并发送一条已知消息；第二步读取收件箱和历史，核对发送者、接收者与完整正文。社交运行则发布一条帖子，再检查目录、行动结果、推荐预览及实际呈现的曝光变化。

正式 Runtime 收集机制登记的步骤回调；social 的曝光与语义任务在完整步骤收束时完成。直接消费底层服务时，需要承担相同收尾责任。语义推荐默认关闭；启用时同时接入 embedding 和 vector client，具体接线见[社交指南](social.md)。

使用不同名字注册多个实例后，逐项核对服务引用、成员及路径。公开工厂和信息接口分别见[轮次对话参考](../reference/round-robin.md)、[社交参考](../reference/social.md)，运行后的展示见[工作台](workbench.md)。
