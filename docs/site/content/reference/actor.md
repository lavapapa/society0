# 主体目录与 ActorStore

`from society0.kernel.actors import ActorRecord, ActorDirectory, ActorStore` 用来读取主体的持久身份与主观资料，组合工厂也可以从 `society0.plugins` 导入。主体目录保存身份与主观资料，与具体驱动分开；`ActorStore` 在真正访问某位主体时才按记录里的驱动名称建立实例，因此同一份目录可以配规则主体，也可以配模型主体。驱动工厂由装配时的插件提供，工厂用到的连接池等资源也由那个插件持有。领域资产和行动资格留给环境机制维护。

## 目录、驱动与绑定关系

<!-- api: society0.kernel.actors.ActorRecord -->
<!-- api: society0.kernel.actors.ActorRecordView -->
<!-- api: society0.kernel.actors.ActorDirectory -->
<!-- api: society0.kernel.actors.ActorStore -->
<!-- api: society0.kernel.actors.actor_data_plugin -->
<!-- api: society0.kernel.actors.actor_plugin -->

`actor_data_plugin` 发布 `directory`，只装它就能得到一个纯数据环境。`actor_plugin` 发布 `actors`，默认带上自己的数据子插件；传入显式 data 引用则复用外部目录，这时初始 records 由那个外部目录声明。环境可以依赖目录，驱动依赖环境的输入服务，绑定这两者的 ActorStore 放在最后安装，资源依赖就不会成环。

ActorStore 实现了 `Mapping[str, Actor]`。`actors[id]` 会先取到一个惰性的 ActorRecordView，把它交给该标签对应的工厂，并作为 Actor.config；安装、排队和遍历 id 都不会创建 Driver。工厂可以复用插件已有的连接池，也可以只返回一个轻量门面。安装和恢复时会检查目录里全部持久驱动标签，包括 inactive 主体；标签解析不出来抛 `ValueError`，通过 ActorStore 新增主体或改成一个未知标签则抛 `KeyError`。

## 读取范围与更新方式

用什么方式读，决定了实际加载多少数据；取完整记录和按单键读取的成本不一样。

<!-- api: society0.kernel.actors.ActorDirectory.get_record -->
<!-- api: society0.kernel.actors.ActorDirectory.view -->
<!-- api: society0.kernel.actors.ActorRecordView.state_values -->
<!-- api: society0.kernel.actors.ActorDirectory.update -->
<!-- api: society0.kernel.actors.ActorDirectory.set_state -->

persona、config、state 返回的是各自解码出来的值，改动这些 Python 对象不会写回存储。要更新单个键用 `actors.set_state('alice', 'intent', value)`，要整体替换主观映射用 `actors.update('alice', state=mapping)`。state 必须是字符串键的字典；字段非法或 active 类型不对时抛 `TypeError`。deactivate 保留身份和历史，active 只影响筛选，能不能行动仍由机制判断。

view 的正文属性按需读取，每次都在一个短快照里核对该主体的 data_revision。主体资料变了以后，旧的 view 会抛 `ValueError`，需要重新取一个；其他主体的写入和 Thread 写入不会让它过期。`get_record` 会把人格、配置和全部状态一次加载出来，主体不存在抛 `KeyError`。单个特别大的状态值仍然整段编码和解码，常用字段和冷字段按需要拆开。

要在同一快照里跨字段读取，可以用目录的 `persona_in(view, actor)`、`config_in` 和 `state_values_in`，并声明相应的依赖属性；ActorStore 把这些方法委托给目录。自己实现外部目录时，要让激活视图和这些同快照入口读同一份来源。

## 按角色和活跃状态选择主体

选择直接在角色、活跃状态和登记顺序的索引上取数据。

<!-- api: society0.kernel.actors.ActorDirectory.select -->
<!-- api: society0.kernel.selection.select_ids -->
<!-- api: society0.kernel.selection.sample_ids -->

`select(active=None)` 会把停用主体一起选进来，不传参数时默认 active=True。返回的 Page 带精确的 total、revision 和 next_cursor。游标和运行、筛选条件以及选择表版本绑定；角色或活跃状态变化后，要从第一页重新读。总数来自同一个事务里维护的计数，单页正文的成本随请求的页长增长，角色更新的成本随该主体角色数增长。

`select_ids` 接受 predicate，对候选逐个用惰性视图做显式分析，成本随候选数量和实际读取的字段增长。`sample_ids` 只遍历候选一次，占用空间与样本数成比例，最后按输入顺序返回；同一序列配同一种子可以复现，样本数超过总体时返回全部。

见[主体教程](<../tutorial/actors.md>)及[外部目录指南](<../guide/actor-directory.md>)；源码见 [ActorStore](<https://github.com/lavapapa/society0/blob/@COMMIT@/src/society0/kernel/actors.py#L231>)。
