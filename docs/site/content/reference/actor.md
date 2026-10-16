# 主体目录与 ActorStore

`from society0.kernel.actors import ActorRecord, ActorDirectory, ActorStore` 访问持久身份与主观资料；组合工厂也可从 `society0.plugins` 导入。领域资产与行动资格由环境机制维护。

## 一、数据与绑定

目录保存数据，运行映射在实际访问主体时解析驱动。

<!-- api: society0.kernel.actors.ActorRecord -->
<!-- api: society0.kernel.actors.ActorRecordView -->
<!-- api: society0.kernel.actors.ActorDirectory -->
<!-- api: society0.kernel.actors.ActorStore -->
<!-- api: society0.kernel.actors.actor_data_plugin -->
<!-- api: society0.kernel.actors.actor_plugin -->

`actor_data_plugin` 发布 `directory`，可单独装配纯数据环境。`actor_plugin` 发布 `actors`，默认包含自己的数据子插件；显式 data 引用复用外部目录，此时初始 records 交给外部目录声明。环境可以依赖目录，驱动依赖环境输入服务，ActorStore 最后将二者绑定，避免资源依赖环。

ActorStore 是 `Mapping[str, Actor]`。`actors[id]` 取得惰性 ActorRecordView，交给该标签的工厂，并作为 Actor.config；安装、排队和迭代 id 均不创建 Driver。工厂可复用插件拥有的连接池或返回轻量门面。安装和恢复检查包括 inactive 主体在内的全部持久驱动标签，无法解析抛 `ValueError`；通过 ActorStore 新增或修改为未知标签抛 `KeyError`。

## 二、读取与更新

读取方式决定实际加载量；获取完整记录与按键读取是不同成本的操作。

<!-- api: society0.kernel.actors.ActorDirectory.get_record -->
<!-- api: society0.kernel.actors.ActorDirectory.view -->
<!-- api: society0.kernel.actors.ActorRecordView.state_values -->
<!-- api: society0.kernel.actors.ActorDirectory.update -->
<!-- api: society0.kernel.actors.ActorDirectory.set_state -->

persona、config、state 返回独立解码值。修改这些 Python 值不会写回，使用 `actors.set_state('alice', 'intent', value)` 更新单键，或 `actors.update('alice', state=mapping)` 替换整个主观映射。state 要求字符串键字典；非法字段或 active 类型抛 `TypeError`。deactivate 保留身份和历史，active 是选择属性，行动授权仍需机制判断。

view 的正文属性按需读取，每次在短快照内核对该主体 data_revision。主体资料变化使旧 view 抛 `ValueError`，应重新取得；无关主体及 Thread 写入不使它过期。完整 get_record 会加载人格、配置和全部状态，缺主体抛 `KeyError`。单个巨型状态值仍完整编码、解码，冷热字段应按需要拆分。

跨字段同快照读取可使用目录的 `persona_in(view, actor)`、`config_in` 和 `state_values_in`，并声明相应依赖属性；ActorStore 显式委托这些方法。外部目录须确保激活视图和同快照入口来源一致。

## 三、选择

选择页直接使用角色、活跃状态及登记顺序索引。

<!-- api: society0.kernel.actors.ActorDirectory.select -->
<!-- api: society0.kernel.selection.select_ids -->
<!-- api: society0.kernel.selection.sample_ids -->

`select(active=None)` 包含停用主体；默认 active=True。返回 Page 含精确 total、revision 和 next_cursor。游标绑定运行、筛选与选择表版本；角色或活跃状态变化后重新从第一页读取。总数来自同事务维护的计数，单页正文成本随请求页长增长，角色更新成本随该主体角色数增长。

`select_ids` 可按 predicate 对候选惰性视图做显式分析，成本随候选数和所读取字段增长。`sample_ids` 一次遍历候选，保留与样本数成比例的空间，最后按输入顺序返回；同序列与种子可复现，样本数超过总体返回全部。

见[主体教程](<../tutorial/actors.md>)及[外部目录指南](<../guide/actor-directory.md>)；源码见 [ActorStore](<https://github.com/lavapapa/society0/blob/@COMMIT@/src/society0/kernel/actors.py#L231>)。
