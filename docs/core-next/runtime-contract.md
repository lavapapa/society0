# 运行调度合同

运行时将插件提供的信息、行动与存储服务组成一个共享环境。Actor 持有身份、Driver 与主观状态引用，Driver 通过绑定身份的 Session 运行。此实现涵盖规则和假驱动的调度闭环；模型循环、完整 Thread 与记忆持久化由各自插件继续接入。

## 一、组合

应用通过 `runtime_plugin` 将主机中的信息、行动、存储服务接入运行时。每个参数使用 `(插件名, 服务名)`，因此主机可以在消费者退出后才关闭提供方。两个机制可向同一个 Actions 服务注册不同命名空间的行动，共用同一个权威存储。

```python
from society0.kernel.runtime import Actor, DriverResult, Phase, runtime_plugin

class RuleDriver:
    async def run(self, session):
        # 此处通过 session.information 与 session.actions 作实际决定。
        return DriverResult('completed')

plugin = runtime_plugin(
    [Actor('alice', RuleDriver(), state_ref=None, config={})],
    information=('interaction', 'information'),
    actions=('interaction', 'actions'),
    store=('storage', 'store'),
    capacity=8,
    max_activations=4096,
)

async def decide(ctx):
    ctx.activate('alice', {'reason': 'new day'})

# 安装所需服务和 plugin 后，在 PluginHost 作用域中运行：
# runtime = host.service('runtime', 'runtime')
# await runtime.run_step(1, '2026-10-04', [Phase('decide', decide)])
```

示例最后三行需要应用实际安装的服务。`run_step(step, time, phases)` 的整数 step 紧接 `store.complete_step`。Session 与 PhaseContext 的 `prepare_artifact(chunks)` 将字节迭代器交给存储流式封存，并登记本步骤的产物引用。各阶段共用本步登记，退出后失效；完成或失败后清空增量引用，失败的准备操作不会登记为成功。调用同步 `store.complete(step, artifacts=tuple(refs))` 成功返回后才更新 `runtime.last_completed`。存储写者保持所属线程，运行时不会为发布跨线程转移连接。

## 二、阶段

Phase 按列表顺序执行，默认 `execution='serial'`。同批不同主体依提交顺序激活，后项可看到前项已执行行动产生的 live 状态。对于互不冲突或行动顺序无关的机制，可以显式选择 `execution='independent'`；这是一项领域声明，运行时不从读写声明自动证明其成立。共享冲突机制应采用 serial。独立阶段的并发上限取 Runtime.capacity，完成的槽位立即补入等待主体。

`Phase(name, run, prepare=None, execution='serial')` 的 prepare 在激活前执行一次，其返回值以同一对象引用交给所有 Session.prepared。准备函数负责返回不可变、适合共享的结果。该能力不复制完整 World，不承诺任意信息请求都绑定历史版本；scope.revision 默认为 None，信息服务返回各次实际版本。准备过程中取得的 SQLite 读回调须先退出，再等待外部模型或网络。

`ctx.activate(actor_id, payload=None, dedupe_token=None)` 复用现有 ActivationPool。相同主体等待中的信号合并为一个 activation；执行中的新增信号合并为下一轮。Session.signals 保留本轮各项载荷，不丢弃合并内容；相同 dedupe_token 遵循池的去重合同。同主体同时有一个 Driver 写者，默认按主体身份作为激活键与串行域。`ctx.drain()` 可以显式等待当前工作，阶段末尾也自动等待全部后续激活。

总激活上限覆盖完整 run_step，各 Phase 使用剩余额度。额度耗尽且出现待激活工作时明确失败；后续纯机制阶段仍可运行。上限允许由运行配置调整为正整数或 None，不通过额外激活请求伪装正常完成。准备和运行函数均可同步或异步；模拟时间完全由 run_step 的 time 指定，网络等待不会推进模拟时间。

## 三、会话

每次 Driver.run(session) 获得 Actor、Moment、state_ref、information、actions、prepared、signals 与 cursors。Actor.config 和 state_ref 是已有对象引用，运行时不会构造每主体 World 副本。Session.activate 可产生后续主体信号，退出后的会话不能继续提交激活。

每次激活建立新的 InteractionScope，在 Driver 返回、抛错或取消时关闭。借出的信息和行动门面随后拒绝新调用；已经进入行动处理器的事实由业务事务决定，作用域失效不冒充业务撤销。不同激活的 scope 对象不同，同一个 `(actor, time, phase)` 的 cursors 字典在连续同 Moment 激活中复用，用于信息会话游标。时点或阶段改变后清空当前游标登记，历史不随运行时会话表累计增长。重启后的会话恢复需由 Thread 与主观状态插件另行提供。

DriverResult.status 包括 completed、waiting、incomplete。waiting 表示正常暂停，可由后续信号重新激活；阶段结束时开放等待允许步骤完成。Driver 应通过主观状态服务保存需要恢复的等待事实，返回值本身不自动写入权威库。incomplete 表示本次决定未完成，使完整步骤发布失败；预算耗尽或输出截断应采用这一状态。实际工具结果与 Thread 原文保存由驱动插件承担，运行时不对它们裁剪或摘要。

## 四、失败

Driver、准备函数或阶段运行异常会停止该阶段，取消并等待所有已启动工作清理，再调用 store.abort_step。外部取消也遵循相同次序。运行实例随后保持 failed，拒绝继续调用 run_step；它不会自动重试成功行动。发布调用报错时回读权威存储的完成水位；如果故障发生在实际发布成功后，保留该完成身份，同时维持运行实例 failed，等待显式处理。StageStore.abort_step 的实际合同是废弃当前未完整状态并要求显式恢复，已经发生的外部副作用仍须由对应插件保留记录和处理。

PluginHost 退出时关闭 Runtime。外部正在运行的步骤会被取消并等待结束，再释放依赖服务。一个 Runtime 实例由一个调用者拥有运行生命周期，同时执行第二个步骤会被拒绝。使用者须让后台工作在 Driver 的取消清理中结束，避免脱离作用域继续修改事实。

作者证据在 `tests/primary/test_kernel_runtime.py`，首轮模块缺失红灯与整步骤预算红灯分别保留在 `research/core-next/runtime-red.txt`、`runtime-step-budget-red.txt`，绿灯记录在 `runtime-green.txt`。测试覆盖 PluginHost 两机制组合、顺序 live 行动、独立命名空间并发、同主体互斥与信号合并、共享准备引用、并发补位、同 Moment 游标、网络等待、取消清理、激活限制、开放等待与发布失败。本合同针对局部调度及其真实消费者，完整仿真恢复和 LLM 决策链仍需后续端到端验收。

## 五、收集与按需加载

阶段可显式设置 `incomplete='collect'`，收集 Driver 返回的 incomplete，并继续其余主体。默认 `fail_step` 保持未完成即失败；领域处理器、Thread 写入与其他执行异常始终使步骤失败。采用 collect 的机制负责解释未完成结果，完成步骤仍保留各主体的真实状态，不把 incomplete 改写为 completed。

`await context.drain()` 返回此次尚未消费的 `ActorResult(actor_id, round, result)` 元组，其中 result 保留 status、reason、value。结果按主体首次提交顺序、同主体激活 round 排列，与网络返回顺序无关。同轮合并的多个信号对应一次实际执行与一个结果。重复 drain 没有新执行时返回空元组；`context.results` 提供该阶段全部已成功返回的结果视图，读取不增加激活计数。多次 drain 的各批各自保持上述顺序，机制可以消费结果后再激活下一批。

Runtime 同时接受 `Iterable[Actor]` 或 `Mapping[str, Actor]`。Mapping 保持服务引用，实际 worker 开始执行时才取主体并构造 Driver，初始化和排队不枚举全体主体。查找异常仍按执行异常终止步骤。runtime_plugin 同样保留 Mapping 的按需访问语义，适合持久 ActorStore 与 selector 返回少量主体 ID 的组合。
