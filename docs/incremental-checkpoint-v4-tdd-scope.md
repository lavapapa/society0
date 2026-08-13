# Checkpoint v4 第一阶段：范围、风险与 TDD 场景

## 1. 第一阶段要闭合的问题

本阶段不以“已经能写一个增量文件”为完成条件。完成条件是先证明下面三件事有可执行且可测试的答案：

1. **哪些变化能够被观察**：所有受支持的 Tick 内持久化写入必须经过 StateProxy 或 canonical writer；保存函数不能再负责发现变化。
2. **哪些动作能够被控制**：字段声明决定允许 set/delete、append、临时写入还是向量元数据写入；不符合声明的写入在修改 canonical state 之前失败。
3. **系统保证什么**：完整 marker 之前的变化不可恢复；任意完整 Tick 恢复等价；分叉不污染来源；检查点热路径的时间、写入量和临时内存不依赖累计 append-only 历史。

若一个运行时写入仍能直接修改 `World.agents_data`、`World.environment_data` 或 Chroma 而不进入上述控制面，v4 就尚未成立。若一个测试通过保存前/后扫描完整 World 来建立 expected delta，该测试也不能证明目标复杂度。

## 2. Feature scope

### 2.1 必须修改的核心面

| 面 | 当前事实 | v4 要求 |
| --- | --- | --- |
| 状态声明 | JSON Schema 只描述类型、权限和默认值 | 每个持久化字段必须声明语义；未声明字段在 Tick 写入前失败 |
| canonical state | `agents_data` / `environment_data` 是公开可变 dict | bootstrap/restore 使用专用 writer；Tick 内只能通过代理或 canonical writer |
| 变化捕获 | StateProxy 生成审计事件，raw dict 可绕过 | 独立 journal 与写入同源；审计日志不能充当恢复 delta |
| Tick 生命周期 | Society0、SimEngine 各自推进并保存 | 统一 begin → mutate → seal/abort → publish；失败直接丢 runtime delta |
| checkpoint | v3 gzip 完整 World，并复制 Chroma 目录 | v4 replacement + append segment + manifest + marker；删除 v3 读写路径 |
| Thread | 独立 append-only 文件，checkpoint 每次发布清单 | 保持独立事实，只引用不可变清单；不进入 World 或 replacement |
| Chroma | 每 checkpoint 复制/恢复目录；多个集合可直接写 | 每条 checkpoint lineage 共用一个物理数据库；所有集合统一 Tick/分支元数据与查询视图 |
| 恢复与分叉 | 恢复完整 gzip；branch_id 仅有局部雏形 | 沿 v4 父链恢复任意 Tick；分叉引用源不可变段并建立向量谱系 |
| GC | 仅按失败路径清理局部临时文件 | 从 marker/branch root 标记可达组件；清理孤儿且不删共享段 |

### 2.2 明确包含

- Environment state、Agent state/properties/reminders，以及 World 固定身份字段。
- 内置 plain、round-robin、social-network 环境和外部 Environment schema。
- `Society0 + CodeSchedule` 主路径与仍受支持的 SimEngine 路径。
- Agent Memory collection、social-network post embedding collection，以及通过 PersistenceManager 取得的其他向量集合。
- 初始 step 0、周期 checkpoint、诊断 checkpoint、跨 run 恢复、任意 Tick 分叉。
- 失败、取消、线程写入、进程中断遗留文件、同一步替换和保存背压。

### 2.3 明确不包含

- 产业链 EconomicModel 或正式 runner 的业务字段迁移；它们只在 core API 稳定后单独适配。
- 为 v3 checkpoint 保留读取、迁移或兼容层。
- 自制通用压缩、保存时对象图 diff、全量哈希索引、通过重新扫描 World 兜底。
- 未被 Society0 支持输入和公开接口触达的极端文件系统攻击情景。

## 3. 状态声明与写入合同

### 3.1 声明规则

- JSON Schema property 使用 `persistence.kind`，取值为 `replaceable`、`append_only_map`、`append_only_list`、`transient`。
- replaceable map 默认按声明节点整体替换；若 map 无界增长，必须在 schema 中声明 `granularity: entry`，让每个 key 成为独立 replaceable 投影。保存时不得为了实现 entry 粒度遍历整张 map。
- append-only map 的 ID 在分支历史内唯一；恢复后的 journal 必须继承唯一性约束，不能只记住当前进程写过的 ID。
- transient 字段只能通过 schema default 重建；默认值本身必须是 JSON 可复制值或声明的 factory，不能从未来状态推导。
- 框架固定字段使用 core 自带声明，不把内部字段混入实验 schema。

### 3.2 写入阶段

| 阶段 | 允许的写入入口 | journal 行为 |
| --- | --- | --- |
| bootstrap | `WorldBootstrapWriter` | 建立 step 0 基点；校验所有字段已有声明 |
| Tick | StateProxy / canonical writer | 同步校验并写 runtime delta |
| seal 后 | 无业务写入 | 修改立即失败 |
| restore | `WorldRestoreWriter` | 不产生新 delta；恢复后建立下一 Tick journal |
| diagnostic | 只读已提交 World + 失败信息 | 不发布 recoverable marker |

`Environment.get_raw_data()` 不能继续返回可变 canonical dict。保留只读诊断用途时应返回递归只读视图；初始化和恢复代码改用专用 writer。StateProxy 的 `items()`、`values()`、`__iter__()` 也不能泄漏可变嵌套原对象。Agent/Environment 子类缓存的旧代理在 seal、abort、restore 后必须失效，防止跨 Tick 引用继续写入旧 journal。

step 0 必须由 bootstrap writer 发布一次 v4 根基点，否则从未在后续 Tick 改写的初始字段无法恢复。根基点允许一次 O(初始世界) 写入；后续 checkpoint 热路径不允许再次物化它。

`checkpoint_every=N` 时，每个 Tick 仍各自 begin/seal；成功的 sealed delta 追加到当前未发布 checkpoint epoch，直到第 N 个 Tick 才发布 marker。中间任一 Tick 失败会丢弃整个 epoch，恢复回到上一个完整 marker。复杂度写成 `O(上次完整 checkpoint 以来的新增量与替换投影)`；当 `N=1` 时就是本 Tick 增量。epoch 只能有一个，不能让 World 在前一个 epoch 发布未完成时继续推进。

## 4. 测试层次

1. **声明与 journal 单元测试**：不碰文件系统，验证每种操作、顺序、唯一性、seal/abort 和默认值。
2. **StateProxy/canonical writer 合同测试**：验证真实 Python 写法是否在修改底层对象前被允许或拒绝。
3. **v4 组件测试**：验证段、replacement、manifest、marker、哈希链和孤儿清理。
4. **PersistenceManager 恢复测试**：从 manifest 还原 World、Environment、Agent 和 schedule 绑定。
5. **运行器集成测试**：真实执行多个 Tick，注入失败/取消，验证 last-complete 与下一次恢复。
6. **Thread/Chroma 集成测试**：使用真实本地文件和真实 Chroma client；provider 调用仍可使用 fake。
7. **属性/状态机测试**：随机生成合法操作序列，以独立参考模型比较每个完整 Tick。
8. **性能门禁**：固定 delta、增长历史；测 writer 输入记录数/字节、阶段耗时和进程峰值 RSS。

## 4.1 内置状态迁移决定

| 状态 | 决定 | 原因 |
| --- | --- | --- |
| plain state | 内置环境保持空；用户 initial state 必须自行声明 | plain 的 schema 明确 `additionalProperties=False`，不能把任意字段隐式设为 replaceable |
| RoundRobin config/groups | 静态 metadata 或 transient | 能由配置重建，不应每 Tick 保存 |
| RoundRobin pairing | 拆为 current/total round 与 per-agent 当前配对 replaceable；completed pairs 追加 | 当前整个 `pairing_status` 同时含增长历史和当前投影，整图替换会退化 |
| RoundRobin conversation | per-agent 当前记录 replaceable；partner history 追加 | 同一对象混合两种语义 |
| RoundRobin messages | immutable message facts 追加；active cache transient；retention/index replaceable | `message_persistence=False` 会删除旧轮，原 `round_messages` 不能整体声明 append-only |
| Social posts | post creation facts 追加；likes/replies/events 追加；view/embedding metadata 为 per-post replaceable | 原 `posts[id]` 同时含不可变事实、追加互动和可替换计数 |
| Social author index | 动态 author entry + 子列表追加 | 需要 wildcard 声明或 canonical writer，不能只声明根 map |
| Social notifications | notification facts 追加；consumed/read 状态单独 replaceable | 原列表元素会原位修改 |
| recommended/trending | recommended 按 agent replaceable 或由实验选择持久化；trending transient | 当前列表可重算；推荐是否作为研究结果由 schema 明确 |
| Agent properties | per-key replaceable | 通常是有界当前投影 |
| Agent reminders | 默认 transient 消费队列 | 当前语义是 append 后在 prompt 消费并 clear，不符合 append-only |
| identity/persona/model | bootstrap 固定 metadata | Tick 内默认不可改；需要变更时另设显式 canonical API |

动态 ID 使用 schema wildcard matcher，例如 `posts.*.view_count`、`conversation_state.*`；path component 保留原始 JSON key 类型的 canonical 编码，不能把整数 round key静默改成字符串。对混合语义对象先拆状态，再接 journal，禁止用一个根级 `replaceable` 声明掩盖增长历史。

## 5. TDD 场景矩阵

### A. 声明解析与 fail-closed

| 场景 | 最坏故障 | 必须观察的断言 | 层级 |
| --- | --- | --- | --- |
| 合法四类声明 | 类型被错误归类 | 解析结果含完整 canonical path、kind、默认值、粒度 | unit |
| 缺失/未知声明 | 新字段触发隐式全量快照 | 初始化报出具体 schema path；没有 checkpoint 文件 | unit/integration |
| 动态 `additionalProperties` | 无界 map 无法判断语义 | 未声明 entry 规则时拒绝；声明后按 key 捕获 | unit |
| 相互冲突的父子声明 | 同一写入进入两个 journal | schema 构建失败并指出冲突路径 | unit |
| 非 JSON 值进入持久化字段 | 到保存线程才失败 | canonical writer 修改前拒绝；原 state 不变 | proxy |
| runtime raw dict 写入 | 变化绕过 journal | 公共 raw view 不可写；bootstrap/restore writer 之外无绕过 | integration |
| seal 后持有旧嵌套代理 | 下一 Tick 写进旧 delta | 旧引用写入失败，canonical state 不变 | proxy |

### B. replaceable

| 场景 | 最坏故障 | 必须观察的断言 | 层级 |
| --- | --- | --- | --- |
| scalar 多次 set | 重复写中间值或顺序错 | replacement 只含末值；每个 Tick 恢复正确 | journal/store |
| create→update→delete | tombstone 丢失 | 目标 Tick 无该字段；更早 Tick 保留 | store |
| delete→create | 错误保留 tombstone | 末值存在且序列确定 | journal |
| entry-granularity map | 保存整个无界 map | 只写本 Tick 改动 key；旧 key 不进入新 replacement | performance |
| replaceable 紧凑投影 | 投影与 append 事实不一致 | 同一 manifest 链摘要同时绑定两者；恢复一致 | integration |
| 大单值替换 | 隐藏 deepcopy 两次 | journal 只持有一次稳定值；峰值内存受本次值大小约束 | memory |

### C. append-only map/list

| 场景 | 最坏故障 | 必须观察的断言 | 层级 |
| --- | --- | --- | --- |
| 每 Tick 新增事实 | 旧事实被复制 | 新段只含本 Tick entries；旧段 inode/bytes 不变 | store |
| 同 Tick重复 ID | 后写覆盖不可变事实 | 第二次写入前失败；底层 map 保留第一次值 | proxy |
| 跨 Tick/恢复后重复 ID | 新进程忘记唯一性 | 从恢复 state/索引识别重复并在写前失败 | restore |
| append list `append/extend` | extend 部分成功 | 全部合法时保持输入顺序；非法时 state/delta 均不变 | proxy |
| insert/set/delete/sort/clear | 历史事实可变 | 每种操作在修改前失败 | proxy |
| 同 Tick map/list 混写 | 段内顺序不确定 | sequence 单调；相同输入产生相同 canonical bytes/hash | journal/store |
| replacement 与 append 交错 | 恢复时按文件类别重排 | 恢复严格按统一 sequence 应用，或证明两类路径互不重叠 | journal/store |
| 内容寻址复用 | 写出同 hash 不同内容 | 相同 hash 只接受字节一致组件，否则损坏错误 | store |

### D. transient/runtime scope

| 场景 | 最坏故障 | 必须观察的断言 | 层级 |
| --- | --- | --- | --- |
| Tick 内读写临时字段 | 被写入 replacement | manifest/段均无字段 | store |
| advance/abort 后旧引用 | 泄漏下一 Tick | 旧引用失效并清空 | runtime |
| 恢复带 default | 沿用失败 Tick 值 | 使用声明 default/factory | restore |
| 无 default | 伪造空值改变语义 | 字段缺省；首次读按 API 明确处理 | restore |
| runtime_scope namespace | checkpoint 携带 cursor/cache | 组件中不存在，恢复 scope 为 None | integration |

### E. Tick 生命周期与事务外失败

| 场景 | 最坏故障 | 必须观察的断言 | 层级 |
| --- | --- | --- | --- |
| begin 两次 | 两个 runtime delta 并存 | 第二次失败 | runtime |
| 正常 seal/publish | seal 后仍可写 | sealed delta 不可变；marker 后 parent 才推进 | integration |
| step 函数失败 | 部分 delta 被后续看到 | abort 丢弃 runtime delta；last-complete 不变 | E2E |
| after_tick 失败 | hooks 的部分状态泄漏 | 同上；诊断可记录失败但不可恢复 | E2E |
| checkpoint 失败 | World 已 advance 但未发布 | 当前运行终止；重启只见 last-complete | E2E |
| asyncio 取消 | worker 仍写临时文件 | 等待 writer 退出再清理；无 marker | integration |
| 进程中断遗留组件 | latest 误选孤儿 | resolver 只枚举 marker；GC 后孤儿消失 | store |
| 最新 marker 损坏 | 整个 run 无法恢复 | `latest` 跳过坏目标并返回更早完整点；显式 step 仍报错 | manager |

### F. 发布故障注入

对以下每个故障点分别测试“首次发布”和“同 Tick 替换已存在 marker”：replacement 临时文件创建、gzip 中途、fsync、rename 后；segment 写入/复用；Thread manifest；Chroma seal/view；v4 manifest 写入、fsync、rename 后；complete marker 临时写、fsync、atomic replace 前后。

共同断言：marker 前故障不可恢复且旧 marker 不变；marker atomic replace 已成功后，即使调用方随后收到异常，新 checkpoint 仍可恢复；清理只删除本次未发布组件，不删除共享段或旧 Thread。

### G. 恢复与完整性

| 场景 | 最坏故障 | 必须观察的断言 | 层级 |
| --- | --- | --- | --- |
| 恢复每个完整 Tick | 只支持 latest | World 与参考模型逐 Tick 等价 | state machine |
| parent 缺失/环/step 逆序 | 错链仍被接受 | 明确拒绝目标 checkpoint | store |
| replacement/segment/manifest 损坏 | 静默恢复错误状态 | 内容哈希和链摘要失败 | store |
| 缺段/错 entry_count | 部分历史恢复 | 目标不可恢复；latest 可回退更早完整点 | manager |
| Thread 清单错配 | World 可恢复但证据断裂 | resolver 拒绝 | Thread |
| memory view 错 branch/step | 看到未来记忆 | resolver 或 query 拒绝 | Chroma |
| Environment custom snapshot | 派生结构丢失或复制 canonical state | 只恢复显式声明的有界派生组件 | integration |
| source run 只读恢复 | 恢复时写源目录 | 源文件树字节/mtime 不变 | integration |

### H. 分叉

| 场景 | 最坏故障 | 必须观察的断言 | 层级 |
| --- | --- | --- | --- |
| 任意完整 Tick 建分叉 | 只能从 latest | 新根引用已验证源 checkpoint | integration |
| 段共享 | 分叉复制全部历史 | fork 创建时历史段写入字节为零 | performance |
| 分叉后 append | 污染源 manifest | 源 marker/manifest/segment 字节不变 | integration |
| replaceable 分歧 | 两支末值串线 | 各自恢复正确 | state machine |
| Thread 分支 | 新 Thread 进入源清单 | 清单按 branch/run 隔离 | Thread |
| Chroma 谱系 | 看到源 fork 后或兄弟分支记忆 | 只见祖先 cutoff + 当前分支目标 Tick | Chroma |
| 二级分叉 | 谱系 cutoff 计算错误 | 每一祖先可见上界取对应 fork step | Chroma |

### I. Chroma 单库多 Tick

单库需要额外的发布 epoch，单靠 `created_step <= target_step` 不足以隔离失败 Tick：一次失败写入和随后成功重跑可能拥有相同 Tick。每条向量记录还必须带 `write_epoch_id` 和单调 `epoch_seq`。恢复视图只查询 `epoch_seq <= marker.memory_view.published_epoch_seq`；下一 epoch 的遗留记录在旧视图中不可见，并在重试/恢复前按未发布 epoch 清理。运行中若需要读取当前 Tick 刚写入的记忆，查询显式加入当前 `write_epoch_id`。update/delete 通过关闭旧版本的可见区间并追加新版本完成。

| 场景 | 最坏故障 | 必须观察的断言 | 层级 |
| --- | --- | --- | --- |
| Memory add | 元数据缺 Tick/branch | 每条记录包含 `created_step`、branch、agent | real Chroma |
| 旧 Tick query | future memory 泄漏 | where filter 在 Chroma 查询阶段排除，而非取回后过滤 | real Chroma |
| memory update/delete | 破坏旧 Tick 视图 | 关闭旧版本的 `visible_until_step`，新版本追加 | real Chroma |
| pending background write | marker 先于向量落库 | seal 等待/取消所有受管写入，view watermark 原子发布 | integration |
| 失败后同 Tick 重跑 | 失败记录因 step 相同重新可见 | 新 epoch 与旧孤儿隔离；恢复清理后只见成功 epoch | real Chroma |
| receipt 重试/upsert | 同 ID 覆盖旧版本 | 稳定 versioned ID；相同 payload 幂等，不同 payload 冲突 | Chroma |
| post embedding collection | 只改 Agent Memory | social-network 旧 Tick 同样看不到未来帖子向量 | E2E |
| memoryless run | 仍复制空库 | marker 只记录空 view/watermark，不创建备份目录 | integration |
| 恢复目标 Tick | 物理回滚单库 | 数据库不复制不回滚，只切换 view | E2E |

### J. Agent Thread

| 场景 | 最坏故障 | 必须观察的断言 | 层级 |
| --- | --- | --- | --- |
| closed Thread 清单 | Thread 被塞回 World | replacement/segment 不含正文 | Thread/store |
| open Thread 遇失败 | recoverable marker 引用未闭合 Thread | 只进入 diagnostic；last-complete 不引用 | Thread |
| 同一 Thread 被多 Tick 引用 | 复制 Thread 文件 | manifest 重用不可变引用 | performance |
| Thread 文件/Blob 损坏 | World 恢复但证据不可读 | resolver 拒绝 | Thread |
| 分叉引用旧 Thread | 修改源清单 | 新 manifest 引用旧不可变 ref，源文件不变 | Thread |

Thread 自身也有复杂度门禁：`append_event()` 和 checkpoint manifest 不能每次重读、重哈希整条 JSONL。测试应给 Thread 持续追加事件并保持每 Tick 新事件数固定，断言追加和生成清单只读取尾部/增量索引；现有对完整 Thread 文件重新计算 SHA 的实现必须被替换为分段或可续接的哈希状态。

### K. 并发、背压和生命周期

| 场景 | 最坏故障 | 必须观察的断言 | 层级 |
| --- | --- | --- | --- |
| 两个 save 同时请求 | parent 相同、后者覆盖 | 后者等待；最多一个 sealed 未完成 delta | async integration |
| save 慢、下一 Tick 尝试 begin | 未发布状态继续演化 | begin 受背压，不允许复用 World | runtime |
| `checkpoint_every > 1` | 多个成功 Tick delta 丢失或提前可见 | epoch 内按 Tick 顺序累积；marker 前均不可恢复；失败丢整个 epoch | E2E |
| cancel 等待者 | 取消正在发布者 | 等待者取消不影响 writer | async |
| close 时仍有 save/memory write | 组件不完整 | close 等待或明确失败，不发布半成品 | integration |

### L. GC 与保留

| 场景 | 最坏故障 | 必须观察的断言 | 层级 |
| --- | --- | --- | --- |
| marker 前孤儿 | 永久增长 | GC 删除未引用 replacement/segment/manifest | store |
| 多分支共享段 | 删除仍可达历史 | 任一 branch root 可达即保留 | store |
| 同 Tick marker 替换 | 删除活动 reader 的旧组件 | 当前 GC 阶段只删启动前孤儿；已发布旧组件按保留策略处理 | integration |
| 损坏 marker | GC 把其组件当可达 | 先报告损坏，不猜测删除 | store |
| Thread/Chroma | 误删独立证据或向量 | Thread 按 manifest 可达；Chroma 由显式分支删除管理 | integration |

### M. 属性与状态机

固定随机种子生成 50–200 Tick 操作：replaceable set/delete、append map/list、transient、成功/abort、checkpoint failure、恢复和分叉。参考模型只应用成功 Tick 的合法操作。每次 complete marker 后比较：

- 目标 Tick World 等于参考模型；
- 早期 Tick 恢复结果保持不变；
- state hash chain 在相同操作序列下确定；
- 非法操作不会改变 World、journal 或磁盘；
- 分叉只继承 fork Tick 以前的事实与记忆。

测试生成器直接记录操作作为 oracle，不允许读取保存前后完整 World 来计算 diff。

### N. 复杂度与空间门禁

构造两组世界：累计历史分别为 `H` 和 `10H`，当前 Tick delta 字节完全相同。每组至少连续发布 100 次固定 delta，避免单次墙钟噪声。必须同时断言：

1. checkpoint writer 收到的 entry 数和未压缩字节相同；
2. 保存路径历史 entry 读取计数为零；
3. 新增磁盘字节只允许 manifest 中固定长度 ID/step 带来的有界差异；
4. 后 100 次中位耗时不得随 `H` 呈线性比例；
5. 峰值 RSS 与 delta/最大单条 replacement 有关，不与 `H` 成比例；
6. Chroma 保存只写 view/watermark 元数据，不复制数据库文件。

充分性来自写入路径只消费已封存 delta；下界为任何正确实现至少要读取并持久化本 Tick 的 `A_t + R_t` 字节，因此目标 `O(A_t + R_t + 1)` 与 `Ω(A_t + R_t)` 匹配。

## 6. 开发顺序与红灯门

1. **声明与受控写入**：先让所有 built-in schema 和 core 固定字段可声明；raw Tick 写入测试必须先红。此阶段不改 checkpoint 文件格式。
2. **journal 生命周期**：接入两套运行器 begin/seal/abort；失败 Tick、旧代理和背压测试先红。
3. **v4 PersistenceManager**：把独立 store 合并进 manager，删除 v3 World gzip和 Chroma backup；保存/恢复/故障注入先红。
4. **Thread 与向量视图**：Thread 引用进入 v4 manifest；所有向量集合统一元数据、watermark 和谱系。
5. **分叉与 GC**：先做不可变引用分叉，再做可达性 GC。
6. **状态机与性能**：只有语义测试闭合后才运行大规模基准；发现历史相关增长时回到具体热路径修复。

每一阶段必须在前一阶段端到端可运行后推进。v3 兼容测试应改写为“v3 明确拒绝”；所有断言 v3 文件名、World gzip 或每 Tick Chroma backup 的测试都应删除或改为 v4 manifest/单库视图合同。
