# 轮次对话

`from society0.plugins import round_robin_plugin`。轮次对话把已登记主体按输入顺序分组，保存配对计划、伙伴历史和完整收件消息；每个实例提供 `mechanism` 服务。工厂借用你交给组合的共享存储、主体目录和交互服务，这几项资源由环境持有，关闭时机也由环境决定；每轮的启动、推进和重置要由你在业务阶段显式调用。

## 工厂与资料

工厂借用共享存储、主体目录和交互服务，注册主体绑定的资料及行动。

<!-- api: society0.plugins.round_robin.round_robin_plugin -->
<!-- api: society0.plugins.round_robin.RoundRobin -->
<!-- api: society0.plugins.round_robin.round_robin_information -->

成员身份须唯一，group_size 为 2 至 20 的偶数，成员数量须恰好组成完整组。建议对话时长在 1 至 120 分钟之间；它保存为资料字段，实际激活、阶段与时间推进由 Schedule 决定。clock 用于消息 timestamp，默认采用墙钟，研究需要可注入明确时钟。

每组计划包含 group_size−1 轮。Information 提供 group、members、partners、plan、participants、messages、history 与 content。成员及未来计划按小组与主体授权，消息原文按收件人授权；history 保留全部既有收件记录，content 提供字节范围正文。`round_robin_information(reader)` 只读，读的是与运行机制相同的那几张表，你需要提供读取连接。

## 轮次转换与行动

调用者在业务阶段显式启动、推进或重置当前轮可见起点。

<!-- api: society0.plugins.round_robin.RoundRobin.start_round -->
<!-- api: society0.plugins.round_robin.RoundRobin.advance_round -->
<!-- api: society0.plugins.round_robin.RoundRobin.initialize_round_messages -->
<!-- api: society0.plugins.round_robin.RoundRobin.pairing -->

start_round 可跳到未来合法轮，拒绝回退；同轮仍有活动配对时重入保留状态，返回空新增配对。启动事务建立伙伴与历史，已配对身份不重复登记。advance_round 更新轮号并清空当前活动配对，返回下一轮计划；调用者随后用 start_round 开启交流。无计划返回 idle，已到末轮返回 completed。

initialize_round_messages 仅接受当前正整数轮，把收件箱的可见位置移到当前消息末尾，保留历史原文。pairing 返回当前伙伴、是否可交流、轮次、总轮数和伙伴历史，未知成员抛 KeyError。

动作 send_message_to_partner 与 broadcast_to_group 绑定主体自身的 participants Ref，要求活动对话；空白内容或无活动伙伴返回 rejected。广播向同组其他成员分别生成完整消息。mark_conversation_participant 保存本主体 conversation_marker。消息事实、接收计数同事务写入，调用返回 completed 及送达记录。

## 使用与成本

配对计划初始化随成员和轮数增长；发送成本取决于接收人数与完整正文大小。分页资料使用对应索引，直接 pairing、group_view、conversation_view 会物化所请求的完整历史或当前视图。服务注册在主机退出时撤销，状态恢复沿共享完整步骤进行。

```python
from society0.plugins import round_robin_plugin

conversation = round_robin_plugin(
    ["alice", "bob", "carol", "dan"], group_size=2, name="conversation"
)
# 加入已有主体目录与交互服务的组合，在计划阶段调用 mechanism.start_round(1)。
```

串联两个机制见[对话教程](../tutorial/conversation.md)，时间安排见[调度指南](../guide/schedule.md)。源码入口为 [RoundRobin.start_round](https://github.com/lavapapa/society0/blob/@COMMIT@/src/society0/plugins/round_robin.py#L93)。
