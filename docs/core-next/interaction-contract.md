# 共享环境交互合同

本模块提供运行时绑定主体的信息访问与动作模板入口。机制提供者仍拥有世界事实、授权子集和业务规则；这里的两个机制贯穿例以订单购买产生另一主体的消息，验证它们访问同一个世界。实现位于 `src/society0/kernel/interaction.py`，由 Runtime 的 Session 绑定主体，SQLInformation、规则与 LLM Driver 共用该交互入口。

## 一、身份与作用域

`Ref(namespace, kind, key)` 表示资源，`Moment(time, phase)` 表示仿真时点。相同类型的两个机制实例通过 namespace 区分。`InteractionScope(actor, moment, revision=None)` 由运行时创建，其三个公开字段不可重新赋值。工具适配器从 `information.bound(scope)` 与 `actions.bound(scope)` 取得，公开方法不接受模型传入 actor。

scope 支持同步上下文管理与 `close()`。关闭后调用抛出 `ScopeClosed`；异步权限检查、候选检查及信息提供者返回后会再次检查有效性。已经开始的 handler 由运行时等待或取消，关闭 scope 不会撤销其中已发生的业务变化。资源引用和 scope 属于 Python 运行接口；LLM 工具、Shell 与 Observation 适配器负责各自公开参数的显式序列化，并保持绑定主体的作用域。

revision 为 None 表示由提供者读取当前版本。它不建立数据库快照；每次信息响应携带实际 revision。需要固定多次读取版本的机制应实现固定视图及游标验证。相同时点重新激活可取得更新后的信息，Moment 本身保持不变。

## 二、信息

信息入口将绝对路径按段匹配到最长挂载前缀。注册不读取正文、不列举对象；`provider.ref(path)` 只负责轻量资源定位。权限函数 `allows(scope, operation, ref)` 可同步或异步，operation 分别为 discover、read、invoke。目录发现、正文读取和动作调用分别判断。

```python
information = Information(allows)
information.mount('/market', market_provider)
tools = information.bound(scope)
page = await tools.list('/market', limit=100, cursor=None)
chunk = await tools.read('/market/orders/one', offset=0, size=65536)
page = await tools.query('/market/orders', Query(
    fields=('id', 'price'), filters=(('price', 'le', 100),), limit=100))
```

提供者实现 `ref(path)`、`list(scope,path,*,limit,cursor)`、`read(scope,path,*,offset,size)` 和 `query(scope,path,query)`；按其资源类型实现实际需要的操作。list 返回 `Page(items,total,next_cursor,revision)`。read 返回 `DocumentChunk(data,total_bytes,next_offset,revision,source)`，data 是 bytes，按字节读取可重新拼接全部 UTF-8 原文。

`Query(fields,filters,order,limit,cursor,sample_seed)` 是请求载体，查询能力和运算定义由提供者确定。Core 将请求原样传递，不执行通用过滤、扫描或查询语言。提供者负责授权行和字段、授权后的 total、继续读取、固定版本与游标绑定。未知查询操作须由提供者明确拒绝。Core 对目录本身的授权不代表目录下每个对象都可见。SQLInformation 的 list_authorized 入口使用同一 allows 对各静态子路由执行 discover 判断，list 与 list_files 返回可见路由的精确 total。游标绑定主体、时点、运行身份、声明权限依赖版本和可见路由集合；授权判断期间版本改变会拒绝本次页面。行级授权继续由 SQL predicate 下推，不通过全表 Python 过滤完成。

资源未挂载和权限不满足统一抛出 `Unavailable`，不暴露不同的目标细节。已有信息的读取由 read/query 完成；需要时间、费用或改变世界的调查由动作受理，不能藏在信息读取中。

## 三、动作

注册表按 `(namespace,kind)` 保存动作模板。发现作用于传入的具体 target；注册量随能力类型增加，不生成所有对象和动作的笛卡尔积。

```python
actions.register(Action(
    name='market.purchase', target_kind=('market', 'order'),
    description='购买订单中的商品',
    parameters={'type': 'object', 'properties': {
        'quantity': {'type': 'integer', 'minimum': 1}},
        'required': ['quantity'], 'additionalProperties': False},
    handler=purchase, available=has_stock, terminal=True))
tools = actions.bound(scope)
page = await tools.find(Ref('market', 'order', 'one'), limit=100)
result = await tools.invoke('market.purchase', Ref('market', 'order', 'one'),
                            {'quantity': 1})
```

`find(scope,target,*,query='',limit=100,cursor=None)` 搜索模板名称与描述并返回轻量 ActionSummary(name,description,terminal,tags) 页面；`describe(scope,name,target)` 返回不含 handler 的 ActionDescription。两者与 invoke 共用 discover、invoke、available 筛选。available 是轻量前置判断，invoke 每次重查。真正的库存、价格、资格等条件仍需 handler 在业务写入边界重新判定。

Action 支持不可变 tags、strict 与 read_only 元数据，describe 返回完整配置。read_only 是机制声明，由执行机制保障；它不隐式修改访问权限。Driver 的 names/tags 研究策略应在绑定门面对 find、describe、invoke 一致应用，None 与空集合保持不同含义。

注册时采用现有 jsonschema 检查 schema，隔离注册参数及描述返回值的可变引用。strict=True 复用已有严格工具 schema 校验，要求显式闭合对象与必需字段；执行前保留旧 ActionSet 的 nullable 字符串归一化，再按原 schema 验证。显式枚举中的字符串 null 保持原值，参数不被原地修改，也不为注册自动放宽 schema。执行参数不符合 schema 时返回 rejected/invalid_arguments，handler 不执行。未知动作、类型不匹配和不可调用均返回 rejected/unavailable。动作函数返回 `ActionResult(status,value=None,process=None,terminal=False)`；status 为 completed、accepted 或 rejected。accepted 可携带后续过程 Ref。只有注册为 terminal 且完成的动作具有 terminal=True，handler 自行设置的 terminal 不会改变该合同。

正式共享环境使用 `interaction_plugin(allows, access_dependencies=())`，统一声明访问规则读取的表。业务插件 `Actions.register(action, dependencies=())` 按目标类型合并资格相关表；表版本由写入器维护。发现页与游标绑定运行身份和这些依赖版本，Thread 留证保持旧页有效，相关业务或权限变化明确过期。异步候选检查前后都核对版本。注册时应列全跨插件资格依赖，例如 Actor 角色选择表；缺省依赖为空，仍逐次核对实际可用候选序列。Information.mount 将统一访问依赖传给支持该声明的 SQLInformation。

独立非 SQL Actions 可注入 `revision(scope)`，或采用 scope.revision 与候选序列校验。发现游标同时绑定主体、时点、target、搜索串、注册代次和当前可用模板序列，支持 JSON 往返。执行始终重新判断当前资格。SQL Page/DocumentChunk.revision 描述相关数据版本；显式 scope.revision 继续约束全局数据库 live_revision，两者用途不同。

## 四、故障与成本

预期业务拒绝通过 rejected 表达。handler 的异常向上传递，包含无法完成原子撤销的部分写故障；本模块不把异常改成成功或自动重试。处理器开始执行后的异常、取消和非法返回会使本步骤共享交互作用域失效，并通知 Runtime 终止阶段；Driver 捕获异常也无法发布完整点。后续交互拒绝继续，运行从此前完整点恢复。schema、访问条件与预期业务拒绝在处理器调用前或通过 rejected 明确表达。

信息路由耗时由路径深度决定，数据工作量由提供者的当前索引与请求范围决定。动作发现按该类型模板数 T 扫描，返回元数据由页长约束；游标保留可用模板名称，空间 O(T)，不随世界对象数或业务历史增长。读取字节预算由提供者执行，Core 当前不替提供者读取、截短或复制全部正文。

## 五、验证

作者测试位于 `tests/primary/test_kernel_interaction.py`。缺少模块时的首轮失败在 `research/core-next/interaction-red.txt`，current 候选集变动的失败在 `interaction-cursor-red.txt`，轻量摘要、JSON 游标、实际版本和读取期失效有对应作者回归测试；原始红测日志在后续同名写入中被覆盖，不再作为证据引用。负游标的非作者红测在 `interaction-independent-red.txt`，发现过程中版本变化的失败在 `interaction-revision-red.txt`，修复后结果在 `interaction-green.txt`。覆盖两主体订单与消息联动、独立权限、主体绑定、失效作用域、异步提供者、原文字节继续读取、十亿条逻辑集合惰性请求、schema、动作状态、终止判定、异常透传及发现游标。

这些测试验证交互边界。真实存储查询、网络序列化、同阶段一致视图和完整 Driver 集成仍需相应阶段测试；提供者授权分页和版本合同须在每种实际机制中落实。

### 发现策略与分页成本

`Actions.find` 与绑定门面接受 `names=None, tags=None`。名称集合与标签集合共同过滤；标签取交集，空集合表示无候选，`None` 表示该维度不限制。一次请求枚举该对象类型的模板一次，计算完整授权总数并返回所需页。Driver 将研究策略下推到这个入口，实际 describe/invoke 仍由 Driver 策略门面及当前世界资格检查。

提供实际数据版本时，游标绑定主体、时点、对象、查询、策略、注册代次与相关版本，保持小型引用。无版本的内存提供者仍携带候选集合，以检查可用性变化；其游标大小随候选数增长。分页改善取用方式，完整总数仍需检查全部相关模板。

普通 `Information.list` 在根目录返回虚拟挂载目录，项目含 `path`、`ref`、`kind="directory"`；挂载前缀代表提供者的命名空间目录，轻量提供者不必为该标记新增 `stat` 实现。SQL 提供者列出的静态资料路由同样标为目录，与 `list_files`、`stat` 一致。进入路由后，`list` 返回原有数据行，不向业务行追加或覆盖 `kind`。`list_files` 则继续返回可读取的具体文件项。外部 Observation 仍通过显式注册的查询与正文接口消费这些资料。


## 行动检索

Actions 每个服务使用一份可重建的内存 SQLite 模板目录。注册时同步更新类型、标签和 FTS5 trigram 索引；模板数量取决于行动类别，不按主体或对象展开。三字符及以上的字面子串查询以 MATCH 取得候选，再用 instr 保持字面匹配，按 bm25 与登记序稳定排序。短词按目标类型执行字面查询，空查询按登记序列全；names/tags 在数据库筛选。

候选仍逐项执行当前权限与 available 判断，返回精确 total、版本与继续游标；这部分成本由候选数量及领域判断决定。行动注册、权限或所声明依赖变化使旧页过期。插件关闭时释放索引，独立 Actions 可用同步上下文管理器或 close。
