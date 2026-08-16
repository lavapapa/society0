# 普通读取与显式状态事务设计

状态：已实现并完成分层验证

基线：Society0 `b3ba8e4`；产业链环境 `4bce5ee`

## 目标与模式边界

Society0 增加一个独立、显式选择的状态访问模式：

- `transparent_proxy`：当前默认模式。`Environment.state`、`Agent.state`、
  `Agent.properties` 和 `Agent.reminders` 继续返回 `DictProxy/ListProxy`，每次写入
  立即修改 canonical World 并生成增量；行为保持不变。
- `explicit_transactions`：普通读取返回当下 canonical state 的只读视图；业务写入
  必须进入显式状态事务。事务先修改隔离的暂存状态，提交时才一次更新 canonical
  World 和当前 Tick journal。

这两个模式长期并存。选择发生在 Society0 引擎 API，并随恢复身份保存；产业链环境
只消费通用能力，不在自身内部模拟第三种模式。

## 已核实的现状

### Society0 与内置环境

World 中的 `agents_data` 和 `environment_data` 是唯一 canonical state。当前公开状态
入口返回深层代理；读取嵌套字典或列表也会继续创建代理。代理写入先做持久化声明和
类型预检，随后立即修改 canonical 容器，再把操作交给 `StateDeltaJournal`。因此单次
`update`/`extend` 可以整体预检，但一连串独立写入并不是业务事务。

一个 Tick 只允许一个 active journal。`seal_persistence_tick()` 固化本 Tick 增量，
complete marker 才是可恢复提交点；`abort_persistence_tick()` 清除 journal、未发布的
Memory epoch 和代理租约，却不会撤销已经写入当前 World 的数据。现有
`NodeTransaction` 只批量记录事件，也不回滚内存状态。

内置环境共三个：

- Plain 没有业务状态和写入；
- Round Robin 同时维护可替换的当前配对投影、只追加的消息/配对事实以及 transient
  活跃消息；一次配对或广播会连续写多个字段；
- Social Network 同时维护只追加的帖子、互动、通知事实与可替换投影，`after_tick`
  还会集中写推荐结果。进程内图、embedding 和推荐缓存不进入 World。

Agent state/properties/reminders 使用同一 proxy/journal/lease 机制。不同 Agent 的 LLM
推理可以并发，当前 World 和 journal 没有通用写锁；EventLogger 的线程锁和 checkpoint
发布锁不提供业务状态串行化。

### 通用产业链环境

领域查询大多接收 `Mapping`，但实际 `self.state` 是深层 proxy；少量名为读取的 helper
会补 plan、inbox 或 daily-market 容器，迁移时必须拆成纯读取和显式初始化写入。

库存、分配、生产、运输、转移、付款、会计、税务、交易和市场清算都跨多个状态根
连续写入。例如一次即时 Trade 会依次扣库存、建 Transfer、记双方会计分录、付款、
消费采购需求、建 Trade 和更新多个投影。任一后置校验或 callback 失败，当前 World
可能已经留下前半段。daily cycle 直接使用 `working = state`，其失败诊断也会保留此前
自然日和阶段的内存结果，最终恢复依靠上一 complete checkpoint。

现有 v4 持久化已经把可替换状态限制到单条实体/记录，把历史事实声明为只追加；seal
只读取本 Tick 被修改的有界锚点，不扫描历史。这一边界必须继续保持。

### 锂电实验

`run_long_experiment.py` 的 automatic runner 只使用普通 dict，不经过 Society0 Tick、
V4 checkpoint、Memory 或 Agent Thread，适合机制候选，不能证明新模式的持久化语义。
`run_provider_experiment.py` 才经过 Society0、真实 provider、Thread、Memory、恢复和
分叉。正式运行目录与当前活动实验保持只读；本工作只使用新的候选输出目录。

## 明确 API

```python
from society0 import StateAccessMode

simulation = Society0(
    save_dir=...,
    state_access_mode=StateAccessMode.EXPLICIT_TRANSACTIONS,
)

state = ctx.env.state                 # 深层只读视图，读取不代理写、不复制记录

with ctx.env.write_transaction() as tx:
    writable = tx.state               # 事务私有、支持普通 MutableMapping 写法
    writable["trades"][trade_id] = trade
    writable["accounts"][seller_id]["balance"] = next_balance
    observed = writable["accounts"][seller_id]  # 读到本事务刚写入的值
# 正常退出提交；异常退出丢弃全部暂存修改
```

World 同时提供 `write_environment_transaction()` 和受 Agent 权限约束的
`write_agent_transaction(agent_id, state_key)`；Environment/Agent 上的方法是面向 Env
作者的窄入口。事务不嵌套，同一事务不能跨 Tick 保存，提交或回滚后所有事务视图失效。
普通只读视图与现有透明代理使用相同的 Tick lease：Tick seal、abort、恢复或 journal
更换后失效。根视图零复制地指向 canonical 根，重新沿根读取会识别已替换的子记录；调用方
不应跨业务事务长期保留已经取得的深层子视图，这与现有嵌套代理的引用边界一致。

模式名是稳定的引擎能力，不使用 feature flag 或产业链专用开关。恢复目标必须使用与
来源 checkpoint 相同的模式；该值进入 resume identity。

## 事务实现

事务采用按持久化锚点的 copy-on-write：

1. 读取优先从事务 overlay 命中；未修改路径直接读 canonical 容器；
2. 第一次修改一条 `replaceable` 记录时，只复制该有界锚点；
3. `append_only_map/list` 的新事实进入独立追加缓冲，不复制已有历史；
4. `transient` 记录按自己的有界根暂存，提交后不进入 delta；
5. 同一事务多次修改同一记录只保留一个最终候选。

提交分为两个无 `await` 的阶段：

1. 在不改 canonical state 的前提下，按完整业务实体/记录统一校验候选值、追加 ID、
   append-only 不可变性和事务开始后的 record version；生成 journal tokens 与 canonical
   patch；
2. 在 World 的提交锁内再次核对受影响记录版本，应用全部 patch，把已准备好的增量一次
   合入当前 Tick journal，最后统一递增 state version/记录状态事件。

预检结束后，第二阶段只执行确定性的字典/列表写入和 journal 合并。若预检、版本检查
或事务体失败，canonical state 与 journal 都不变化。不同 Agent 的事务可以并发准备，
提交由同一 World 串行化；修改同一记录的后提交者收到冲突错误并由 action 调用方重试或
返回失败，不能覆盖已经提交的新值。

### 原子性的充分性与必要条件

充分性来自三个条件同时成立：事务体只改隔离 overlay；所有可能失败的 schema、追加
不可变性、引用/业务规则和并发版本检查都在提交锁内第一次 canonical 写入之前完成；
提交阶段只应用已经冻结的 patch 和 journal token。于是事务体或预检失败时没有 canonical
写入可见，提交阶段完成后所有目标记录和对应 delta 同时可见。事务读取先查 overlay，因而
满足 read-your-writes。

必要条件也很直接：只要一次业务操作在仍可能失败时已经修改 live canonical state，后续
失败就必须依赖完整 undo log 才能撤销；当前 proxy/journal 没有 undo，Memory、Thread 和
运行时缓存也不可能由字段级补偿完整恢复。因此要保证“失败不留下半次修改”，至少需要
隔离暂存或等价的完整撤销信息。这里选择按有界持久化锚点暂存，是同时满足原子性和历史
规模无关写成本的最小方案；整态副本虽然也能隔离，却会让成本随历史增长，不满足要求。

## 持久化、恢复和分叉

显式事务仍写入现有 `StateDeltaJournal`，不建立第二套 checkpoint 格式：

- 新增事实继续产生 append segment；
- 被修改记录继续产生有界 replacement；
- 一个 Tick 内同一锚点仍只保存最终值；
- root checkpoint、complete marker、损坏 marker 回退、恢复重放与 fork 的不可变共享
  规则保持不变。

Tick seal 前必须没有打开的状态事务。Tick abort 会使尚未提交的事务和只读/写入视图
失效；已提交到失败 World 的事务结果仍由上一 complete checkpoint 重建，这与现有
完整恢复边界一致。

## Memory 与 Agent Thread

状态事务只覆盖 World JSON。Memory/Chroma、Agent Thread、provider 调用、文件和网络
不是事务资源，不允许在状态事务体内执行。产业链 canonical writer 在事务中只生成
World facts/projections；需要发送通知、记录 Thread receipt 或触发 Memory 的工作放在
状态提交成功之后，并最终由现有 complete marker 把 World、Memory view 和已闭合 Thread
绑定到同一 checkpoint。

Memory 的 branch、write epoch、failed-epoch discard、fork shadowing 不改。Agent Thread
仍是独立追加文件，只有 closed threads 进入 recoverable manifest。显式事务提交失败时
不得先写 Memory 或 Thread；Tick 后续失败时，现有 epoch/manifest 边界继续负责整步恢复。

## 产业链迁移边界

按共同 canonical writer 从内到外迁移：

1. Inventory、Allocation、Accounting/Payment；
2. Production、Shipment、Transfer、Tax；
3. Trade 与按产品市场清算；
4. purchase-request refresh、daily phase 与 Agent action 入口。

组合调用复用当前事务；最外层公共写操作在显式模式下自动建立事务，在代理模式下继续
走原路径。事务提交后再发布 Inbox、Agenda、DM 或 action receipt。进程级 cache、FoV
cursor 和 step runtime scope 不进入事务。

## 验证门槛

验证严格按下列顺序推进，前一层失败时停止后续结论：

1. Society0：事务读己写、跨字段批量提交、异常零修改、append-only、冲突、过期视图，
   以及 Plain、Round Robin、Social Network 与 Agent state；
2. 通用产业链：真实复杂初始世界下，生产、库存、交易、运输、税务、会计及投影不变量；
3. 同一 checkpoint 三路：普通 dict（只作机制上限）、`transparent_proxy`、
   `explicit_transactions`，比较最终状态、业务计数、delta、墙钟、写入规模和 RSS；
4. 恢复、fork、长跑 RSS 与固定增量/增长历史两组测试；
5. 授权的真实 LLM + embedding、Memory 开启 E2E；
6. 新目录中的锂电消费税候选运行。

每层保存命令、基线提交、开始/结束时间、各阶段耗时、写入记录/字节、RSS、结果一致性
和残余风险。普通 dict 没有 checkpoint/rollback 能力，其结果只作为业务状态与读取开销
参照，不将它描述为可恢复模式。

## 已知影响与风险

- mixed entity（如 Account 内的可替换余额、只追加分录和 ledger index）不能复制整个实体；
  overlay 必须沿声明选择最小锚点，并分别缓冲追加子树。
- Python 普通可变容器允许取得深层引用后原地修改；只读入口和事务视图必须封住 canonical
  可变引用，同时避免在普通读取上深拷贝。
- 当前少量 reader 会隐式补默认容器，迁移后必须显式初始化，否则会在只读模式下失败。
- 状态事件由“每个代理写操作”变成“每个提交锚点”后，事件粒度会改变。checkpoint 结果
  和 state_version/FoV 失效语义必须一致；逐字段审计日志不作为新模式的兼容目标。
- explicit mode 的并发保证是乐观 prepare + 串行 commit，不承诺把 provider、Memory、Thread
  或任意外部 I/O 纳入 ACID。
