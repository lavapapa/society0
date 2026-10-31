# 资源、信息与行动

`from society0.kernel.interaction import Ref, Query, Information, Actions` 是主体访问共享环境与执行行动的边界：`Ref` 指出资源，`Query` 描述读取条件，`Information` 按路径把请求交给提供者并判断可见性，`Actions` 保存动作模板并负责发现和调用；业务时点由 `Moment` 单独给出，和 `Ref` 一起传进读取或调用入口。Driver 通常使用 Session 已绑定主体的门面，模型不能自己指定以谁的名义行动。资源归谁由登记方式决定：`mount` 默认借用 provider，`owned=True` 时 Information 才拥有它并负责释放；Actions 的索引可以重建，脱离运行单独使用时由调用者 close。

## 值类型与作用域

资源、业务时点和分页结果都用值类型表示。

<!-- api: society0.kernel.interaction.Ref -->
<!-- api: society0.kernel.interaction.Moment -->
<!-- api: society0.kernel.interaction.InteractionScope -->
<!-- api: society0.kernel.interaction.Query -->
<!-- api: society0.kernel.interaction.Page -->
<!-- api: society0.kernel.interaction.DocumentChunk -->

`Ref` 的 namespace 区分机制实例，kind 区分资源类型，key 定位对象。`InteractionScope` 绑定 actor 与 `Moment`，退出激活后关闭，之后的调用抛 `ScopeClosed`；同一步出现领域故障时，作用域上的交互一起失效。`revision=None` 由提供者自己读取当前版本，`Moment` 和 scope 本身不创建数据库快照。

`Page.total` 是授权后范围的总数，`next_cursor` 是继续读取的入口。`DocumentChunk.data` 是 bytes，`total_bytes` 是完整原文字节数，`next_offset` 指向下一段；按字节拼接后再解码，避免把 UTF-8 字符的中间字节当成完整文本。

## 用路径读取资料

路由按最长路径前缀找到提供者，查询能力由提供者实现。下面读取要求 `/reports` 已挂载 `SQLInformation`，manual 是 DocumentSpec 路由，intro 是文档主键。

<!-- api: society0.kernel.interaction.Information -->
<!-- api: society0.kernel.interaction.Information.mount -->
<!-- api: society0.kernel.interaction.Information.bound -->

```python
page = await session.information.list('/', limit=100)
chunk = await session.information.read(
    '/reports/manual/intro', offset=0, size=65536)
```

路径必须绝对且不含点段。discover、read、invoke 分别授权；资料不可见和不存在都抛 `Unavailable`。根目录的挂载项标为 directory，进入集合后的业务行保持提供者原形状。`Query` 原样传递，Core 不代做过滤或截断；SQL 语义见 [SQLInformation](<information.md>)。

mount 返回同步的注销函数，重复调用不会重复生效。注销不关闭借用的 provider，`owned=True` 时整体关闭才对同一对象释放一次。插件应先登记 provider.close，再登记路由注销，让退出先撤路由再释放资源。注销在停止消费的边界执行；重登记使旧游标失效，旧注销函数不影响新登记。

## 发现并执行行动

动作模板按资源类型登记，资格在调用时重判。

<!-- api: society0.kernel.interaction.Action -->
<!-- api: society0.kernel.interaction.ActionResult -->
<!-- api: society0.kernel.interaction.Actions -->
<!-- api: society0.kernel.interaction.Actions.register -->
<!-- api: society0.kernel.interaction.Actions.find -->
<!-- api: society0.kernel.interaction.Actions.describe -->
<!-- api: society0.kernel.interaction.Actions.invoke -->

先对具体 `Ref` 执行 find，按需 describe 取得完整参数 schema，再 invoke。available 是轻量候选条件，库存、资格这类最终条件由 handler 在业务事务中再判断。注册的 dependencies 与共享 access_dependencies 必须覆盖实际资格和权限表；相关版本变化使发现游标过期。

register 返回同步且幂等的注销函数，插件可用 `ctx.on_close(actions.register(action))` 托管。注销移除模板和索引、释放依赖引用并推进登记代次；旧发现游标过期，describe 抛 `Unavailable`，invoke 返回 rejected/unavailable。

`strict=True` 要求工具 schema 采用严格形式，执行前按 schema 归一化 nullable 字符串再验证参数；read_only 是领域声明，实际只读由执行机制保障。参数 schema 在注册时校验；执行参数未通过 schema 校验时返回 rejected/invalid_arguments，并在 `value.details` 中保留逐项校验错误；未知或不可调用返回 rejected/unavailable。预期业务拒绝用 rejected；accepted 表示已受理，可返回后续 process Ref；completed 表示行动完成。terminal=True 只在模板登记为 terminal 且结果 completed 时出现，handler 自行设置绕不过判断。

names、tags 传 None 不限制该维度，传空集合表示无候选。发现返回完整授权总数，候选仍逐项检查当前权限和 available；开销随相关模板候选数增长，不按全部业务对象展开。

handler 异常、取消或非法返回向上传播，这一步的交互失效，Runtime 终止阶段。即使 Driver 捕获异常，完整步骤仍无法发布。关闭 scope 不撤销已发生的外部副作用；所有权与提交边界由 handler 实现承担。

调用路线见[行动教程](<../tutorial/actions.md>)；源码见 [Information](<https://github.com/lavapapa/society0/blob/@COMMIT@/src/society0/kernel/interaction.py#L133>)。
