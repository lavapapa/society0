# 持久主体目录

主体身份及其主观资料由 ActorStore 保存，运行时按实际激活读取该主体并取得 Driver。角色和配置用于主体选择与驱动配置；领域对象、资产和法律资格由相应机制表达。主体目录属于同一共享运行，私有工作区按主体归属跨仿真时点保留。

## 一、数据

`ActorRecord(id, driver, persona='', state={}, config={}, roles=(), active=True)` 保存驱动名称、原始 persona、主观 state、配置与角色集合。persona 和 config 各自独立存储，主体短 head 保存驱动名与 active。主观 state 为字符串键映射，按一级键存为独立记录并保持插入顺序；`set_state(actor,key,value)` 更新一项，`update(state=...)` 明确替换整个主观映射。巨大的单个 state 值仍承担该值自身的 JSON 编码成本。JSON 值保持原文内容；读取返回独立的 Python 值，修改通过 ActorStore.update 或 set_state 明确写入。角色读取采用稳定名称顺序。运行时临时提醒通过 Session.signals 或输入构建器提供，目录没有隐式提醒累积字段。

`actor_plugin(drivers, records=(), name='actors')` 提供 actors 服务并声明自身 schema 与初始化。drivers 是驱动名到工厂的注册表，工厂接收一个按需读取的 ActorRecordView。初始化不会构建 Driver。数据库、模型连接池等昂贵共享资源由已有插件生命周期持有，工厂可以复用已安装 Driver，或按该主体配置构建轻量门面。

## 二、访问

ActorStore 实现 Mapping[str, Actor]。按 id 获取时读取主体短头并调用对应驱动工厂；返回 Actor.config 为 ActorRecordView，persona、state 与 config 在访问对应属性时读取，state_ref 为该主体状态引用。显式迭代按首次登记顺序返回 id，分批读取数据库，不构建 Driver。Runtime 可以保留该 Mapping，在实际执行激活时取所需 Actor。

`select(role=None, active=True, limit=100, cursor=None)` 返回主体 id 的 Page，包含精确 total、revision 与继续游标。筛选直接使用角色、active 与登记序号的复合索引，并读取同一规范写入事务维护的计数；角色选择无需遍历其他角色的活跃主体。active=None 显式选择所有主体，允许获取全部人口。调用者可以按业务语义直接选择其他主体；active 是调度查询属性，不代表领域行动的授权。

游标绑定运行身份、角色、活跃选择和 actor_selection/actor_counts 的依赖 revision。角色或活跃选择投影更新后旧游标明确失效，调用者可以重新查询当前范围。主体激活的 Thread 留证与无关状态写入保持选择页有效。此合同尚未提供跨相关更新的长期快照。数量与分页读取分别使用计数投影和定位索引；单页正文工作量随请求页大小增长。主体增删角色或切换 active 时同步维护该主体的角色投影及计数，写入成本随该主体角色数增长。

## 三、工作区

`workspace_plugin` 提供独立 WorkspaceStore，仍使用同一共享 StageStore 并通过 actor 外键关联主体。ShellSession 按当前主体开启短 lease，文件索引按 actor/path 保存，正文作为文件工件登记；完整步骤恢复保留跨 Moment 的私有文件。ActorStore 负责身份和主观资料，工作区读写见 [工作区合同](workspace-contract.md)。

Bashkit 当前恢复接口需要完整快照，load_workspace 会物化该主体的整个私有工作区。重复保存相同内容仍产生新工件；未变化工作区的长期复制成本待专门实验，不以此接口宣称增量文件系统。运行中的共享 World 继续通过信息服务读取，不被纳入私有快照。

## 四、验收

tests/primary/test_kernel_actors.py 验证千主体初始化不构建 Driver、单主体读取、角色与 persona/state/config 恢复、游标水位、工作区脱源恢复、错误更新原子性及固定活跃集合下停用人口增长的 SQL 工作量。首轮缺实现失败在 research/core-next/actors-red.txt。`tests/primary/test_kernel_llm_workspace.py` 通过真实 LLMDriver、Bashkit、Runtime 与 ActorStore 验证完整恢复后下一 Moment 的文件、变量和 cwd；提供方为确定性替身。性能红测保留在 actors-storage-cost-red.txt：冷 persona/config 同行捕获和角色查询扫描已分别改为分表与直接索引计数。固定三名角色成员、无关活跃主体由 100 增至 10000 时，SQL VM 为 108/108；单个小值热点更新不复制相邻巨正文。未变工作区与目录规模试验见工作区合同及相应原始结果。

## 五、字段读取

`ActorStore[id]` 为实际激活创建短 `ActorRecordView`，包含身份、驱动、角色、活动状态和该主体数据版本。驱动工厂使用身份即可工作；访问 `persona`、`config`、`state` 时才读取相应正文。`state_values(keys)` 在一次短查询中读取指定主观状态键，`state_values()` 和 `state` 取得全部主观状态，`get_record(id)` 仍取得完整 ActorRecord。

每次惰性属性读取在同一个 SQL 快照中核对主体 head 的 `data_revision` 并读取字段。该主体内容变化后旧视图明确过期，调用方重新取得视图；其他主体或 Thread 留证的写入不改变它。规范写入器 `update`、`set_state` 在同一事务内推进该主体版本，事务失败保留原版本。插件通过 ActorStore 写入主体数据。

`deactivate(id)` 使常用 active 选择器停止调度该主体，保留身份、主观状态、角色及历史关联。`update(driver=..., roles=..., active=...)` 可明确改变驱动、角色和重新激活状态。完整恢复保留这些选择。

LLM 的 CognitiveInput 继续请求完整人格和主观状态，因此获得的信息范围保持不变。字段按需接口使规则主体可以处理小任务而不加载巨型冷资料；单个被请求的主观键仍完整解码，其成本取决于该值本身。需要巨型独立材料时使用信息数据集和正文引用。
