# 内置机制迁移

新版内置机制进入同一个共享环境，通过 Plugin 声明自己的静态数据和依赖，再注册信息视图、行动模板及调度可调用的方法。迁移以有效领域行为和完整恢复为验收对象，研究者无需建立旧 World。主体身份、Thread 和私有工作区继续由共同服务持有。

## 一、空白基准

plain_plugin 提供零领域状态、零领域能力的基准声明。它可以与 Actor、模型和测量调度组合，不分配第二个 Environment 实例。tests/primary/test_plugin_plain.py 已验证正式组合入口的创建、完整步骤与恢复，记录在 research/core-next/plain-red.txt 和 plain-green.txt。

## 二、轮次对话

round_robin_plugin 的迁移范围是按成员输入顺序分组、偶数小组的 circle 配对、显式轮次启动和推进、当前伙伴与参与状态、配对历史、伙伴消息、小组广播、主体参与标记，以及对话和小组的信息视图。旧 tournament/custom 名称没有独立算法实现；本轮提供已存在的 standard 算法。旧 message_persistence 字段仅进入保留说明，原始消息事实始终保留。新版同样保留全部消息事实，当前收件箱与历史读取分别呈现。

每个实例具有独立表名前缀、行动名称前缀及信息挂载路径。全局 Actor 身份共享，一个主体可以同时参加两个对话机制。机制自己的成员与配对资格在真实执行事务中重验，行动发现反映当前候选资格。权限判定继续经过共享 Actions/Information 入口。

持久数据分为短轮次 head、成员当前投影、固定配对计划、唯一配对事实、主体伙伴历史，以及不可变消息正文与当前读取索引。伙伴/广播事实与收件计数同事务写入。原文正文使用 BLOB 范围读取，信息目录返回可继续读取的消息数据集与文档路径，避免列目录时物化全部消息正文。明确请求完整对话时可以依次取得全部原文。

调度直接调用 start_round 和 advance_round 方法；主体行动模板提供 send_message_to_partner、broadcast_to_group 与 mark_conversation_participant。规则主体和 LLM 主体消费同一信息与行动服务。轮次完成后恢复继续使用已保存的成员顺序、配对事实和消息身份，不重新初始化或扫描历史构建当前收件箱。

## 三、验收安排

轮次对话先验证四人三轮的每对一次、分组边界及双实例共享主体，再验证伙伴消息和广播的逐收件人身份、顺序、计数、空文本拒绝与跨组权限。恢复用完整步骤比较所有伙伴历史及原文消息；动态行动可用性和范围读取通过正式交互服务验收。固定活动轮次消息时扩大其他轮次历史，检查 SQL VM 与读出字节，保留完整数据获取通路。

轮次机制的广播部分失败与重新进入既有轮次已由独立消费者验证，修复和证据见 research/core-next/builtin-storage-review-green-20261004.txt。社交机制的接口、存储和成本合同见 [社交机制](social-contract.md)，范围覆盖拓扑、发布互动、关注、通知、用户详情、趋势、推荐与原向量恢复；作者用例、异步独立审查和后续调度自动收束共同构成验收。具体能力对照继续以 capability-parity.md 为依据。


## 四、轮次接口

`round_robin_plugin(members, group_size=..., name="conversation", session_duration_minutes=10)` 声明该实例的全部表结构并绑定共享 storage、actors 与 interaction 插件。配合 `interaction_plugin(allows)` 后，在主机中通过 `host.service(name, "mechanism")` 获取调度服务。start_round(number) 激活指定计划，advance_round() 推进并清空当前配对资格，initialize_round_messages(number) 清空当前收件可见范围同时保留原始历史。重复启动当前已激活轮次保留配对与唯一历史。

pairing(actor)、group_view(actor) 提供当前伙伴、历史伙伴、组员与未来计划；conversation_view(actor) 在同一读事务中返回全部当前消息原文及修订号。完整原文视图的成本随该主体当前消息正文增长，这是调用者明确取得完整上下文的路径。工具侧 `/实例/messages` 与 `/实例/history` 提供有界元数据分页、精确数量和继续游标，`/实例/content/id` 提供原文 BLOB 字节范围；正文可按返回 id 定位。内容访问按收件人判定。

主体通过 `/实例/participants` 得到自己的 Ref，再经统一行动发现取得 `实例.send_message_to_partner`、`实例.broadcast_to_group`、`实例.mark_conversation_participant`。可见候选与实际写入都检查当前配对资格；目标主体身份由 scope 确定。全部领域写入使用共同 StageStore 事务，广播的每名收件人消息和计数一起发布。时间字段通过可注入 clock 生成；这一字段保留旧消息时间意义，仿真调度时间仍由 Runtime 控制。

实际证据位于 tests/primary/test_plugin_round_robin.py 与 research/core-next/builtin-interaction-green.txt。同一个目录中 round-robin-red.txt、round-robin-retention-red.txt、round-robin-repeat-red.txt 保留对应先失败测试。规则主体和真实模型驱动的组合验收按全局 TODO 分别记录。
