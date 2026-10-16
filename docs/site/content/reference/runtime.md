# Runtime、Session 与阶段

`from society0.kernel.runtime import Runtime, Phase, DriverResult` 提供共享环境执行边界；通常通过 `society0.plugins.runtime_plugin` 接入服务，再由 [runner](<runner.md>) 执行。

## 一、调度

Schedule 决定业务时点，Runtime 完成有序阶段并发布完整步骤。

<!-- api: society0.kernel.runtime.Runtime -->
<!-- api: society0.kernel.runtime.Runtime.run_step -->
<!-- api: society0.kernel.runtime.runtime_plugin -->
<!-- api: society0.kernel.runtime.Phase -->
<!-- api: society0.kernel.schedule.StepPlan -->
<!-- api: society0.kernel.schedule.SequenceSchedule -->

run_step 的 step 必须紧接存储 complete_step，time 是业务指定的 JSON 时间值。返回存储完成回执；阶段返回的 StepResult 从结果服务读取。步骤顺序为 before 钩子、phases、after 钩子、complete。网络等待不推进模拟时间。

默认 serial 依提交顺序执行，不同主体可看见前项已提交的 live 状态。independent 是调用者对业务独立性的声明，容量依次取 Phase.capacity、Runtime.capacity；serial 容量固定为 1，非法容量在配置时抛错。提供方请求额度另外限制物理请求，不能代替阶段的业务顺序。

`prepare` 每阶段执行一次，返回同一对象引用给全部 Session.prepared。调用者负责其共享只读语义；准备读完短数据库快照后再等待外部服务。Mapping 主体集合保持原引用，在 worker 实际执行时取 Actor；避免初始化时逐人口创建 Driver。

## 二、激活

同主体互斥执行，信号和结果保留实际轮次。

<!-- api: society0.kernel.runtime.Session -->
<!-- api: society0.kernel.runtime.PhaseContext.activate -->
<!-- api: society0.kernel.runtime.PhaseContext.drain -->
<!-- api: society0.kernel.runtime.DriverResult -->
<!-- api: society0.kernel.runtime.ActorResult -->
<!-- api: society0.kernel.schedule.activate -->

```python
async def decide(ctx):
    ctx.activate('alice', {'reason': 'new period'})
    results = await ctx.drain()
    return None
```

等待中的同主体信号合并；执行期间新增信号进入下一轮。Session.signals 保留各载荷，dedupe_token 用于显式去重。drain 返回尚未消费的 ActorResult 元组，按主体首次提交顺序、同主体 round 排列；无新执行时再次 drain 返回空元组，context.results 保留本阶段全部成功返回结果。

Session.step 为整数完整步骤编号，moment.time 为业务时间。Session 的交互门面绑定主体，激活结束后 scope 关闭，保存的门面拒绝继续调用。连续同 Moment 激活复用临时 cursors；持久感知位置由 Thread 保存。Runtime 不复制整个环境。

## 三、完成与故障

选择未完成策略时应同时决定研究结果如何解释。

默认 `Phase(incomplete='fail_step')` 遇 DriverResult('incomplete') 抛 IncompleteActivation，整步失败。显式 `incomplete='collect'` 收集该状态并继续其余主体，完整结果保留 incomplete 原值。collect 不吞掉处理器、Thread 写入或其他执行异常。waiting 是正常暂停，阶段末仍有等待允许完成；需要恢复的等待事实由业务状态保存。

激活预算覆盖整个步骤；耗尽后再有待执行工作会失败。Driver、prepare、阶段函数异常或外部取消会取消并等待所有已启动任务，随后 abort_step。实例保持 failed，后续 run_step 被拒绝；恢复从可信完整点创建新运行。发布报错时可能已存在完整描述符，应以权威完整身份判断，外部副作用由对应机制处理。

一个实例同一时间执行一个步骤。Host 先收束 Runtime 再关闭依赖；Driver 的取消清理须结束所拥有的后台工作。执行成本由激活次数、领域计算、服务等待及本步持久化量决定。

见[调度指南](<../guide/schedule.md>)；源码见 [Phase](<https://github.com/lavapapa/society0/blob/@COMMIT@/src/society0/kernel/runtime.py#L48>)。
