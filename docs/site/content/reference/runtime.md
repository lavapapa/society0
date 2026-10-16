# Runtime、Session 与阶段

`from society0.kernel.runtime import Runtime, Phase, DriverResult` 给出共享环境的执行边界：它按调度给出的计划逐步执行阶段，在阶段里激活主体，并在每步结束时发布一个完整步骤。调用者要把主体集合、Information、Actions 和 StageStore 交给它，自己决定阶段顺序、并发容量和激活预算；通常通过 `society0.plugins.runtime_plugin` 接入服务，再由 [runner](<runner.md>) 执行。

## 步骤、阶段与并发

Schedule 决定业务时点，Runtime 按顺序执行阶段并发布完整步骤。

<!-- api: society0.kernel.runtime.Runtime -->
<!-- api: society0.kernel.runtime.Runtime.run_step -->
<!-- api: society0.kernel.runtime.runtime_plugin -->
<!-- api: society0.kernel.runtime.Phase -->
<!-- api: society0.kernel.schedule.StepPlan -->
<!-- api: society0.kernel.schedule.SequenceSchedule -->

调用 run_step 时，step 必须紧接存储里的 complete_step，time 是业务给定的、能编码成 JSON 的时间值。它返回存储的完成回执；阶段返回的 StepResult 要从结果服务读取。一步的顺序是 before 钩子、各个阶段、after 钩子，最后 complete。等待网络响应不会推进模拟时间。

默认的 serial 按提交顺序执行，不同主体能看到前一项已经提交的即时状态。independent 是你对业务彼此独立的声明，容量先取 Phase.capacity，没有再取 Runtime.capacity；serial 的容量固定为 1，容量不合法在配置阶段就抛错。提供方的请求额度另外限制物理请求，它管不了阶段的业务先后。

`prepare` 每个阶段执行一次，返回同一个对象引用给所有 Session.prepared。这个对象被共享、只读，由调用者保证；准备阶段读完数据库的短快照之后再去等外部服务。传入 Mapping 形式的主体集合时，Runtime 保留原引用，等 worker 真正执行时才取出 Actor，避免初始化时给每个主体都创建 Driver。

## 一次激活的范围与结果

同一主体同时只跑一次，信号和结果都按实际轮次记录。

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

等待中的同主体信号会合并，执行期间新到的信号留到下一轮。Session.signals 保留每个载荷，需要显式去重时传 dedupe_token。drain 返回还没被消费的 ActorResult 元组，先按主体第一次提交的顺序排，同一主体再按 round 排；没有新执行时再 drain 得到空元组，context.results 里保留本阶段所有成功返回的结果。

Session.step 是整数形式的完整步骤编号，moment.time 是业务时间。Session 上的交互门面绑定到具体主体，激活结束后 scope 关闭，之前存下来的门面再调用会被拒绝。同一 Moment 里连续激活会复用临时的 cursors，需要跨步骤保留的感知位置由 Thread 保存。Runtime 不会复制整个环境。

## 未完成、异常与恢复

选择未完成怎么处理时，要一并想好研究结果里怎么解释这类记录。

默认的 `Phase(incomplete='fail_step')` 遇到 DriverResult('incomplete') 会抛 IncompleteActivation，整个步骤失败。显式写成 `incomplete='collect'` 则收集这个状态并继续执行其余主体，结果里的 incomplete 保持原值。collect 不会吞掉处理器异常、Thread 写入异常或其他执行异常。waiting 表示正常暂停，阶段结束时还有主体在等待也允许完成；恢复时要用的等待事实由业务状态保存。

激活预算按整个步骤计算，预算用完后还有待执行的工作就会失败。Driver、prepare 或阶段函数抛异常，或者外部取消，都会先取消并等待所有已经启动的任务，再 abort_step。实例随后保持 failed，之后的 run_step 会被拒绝；要接着跑就从可信的完整步骤创建一次新运行。发布过程报错时，完整描述符可能已经存在，应当以权威的完整身份为准判断，外部产生的副作用交给对应机制处理。

一个 Runtime 实例同一时间只执行一个步骤。Host 会先停掉 Runtime，再关闭它依赖的资源；Driver 在被取消时要结束自己拥有的后台工作。执行成本取决于激活次数、领域计算量、等待服务的时间，以及这一步实际持久化了多少数据。

见[调度指南](<../guide/schedule.md>)；源码见 [Phase](<https://github.com/lavapapa/society0/blob/@COMMIT@/src/society0/kernel/runtime.py#L48>)。
