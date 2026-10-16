# 用 Schedule 生成步骤计划

一个步骤有两样东西：业务时间，和这一步里按先后排好的阶段。Schedule 根据最近一个完整步骤提出下一份计划，Runtime 照着执行，并在各阶段做完后发布完整步骤。恢复时可以按已发布的位置重新问一次 Schedule，调度器游标和实际状态就不会各走各的。下面沿用[快速开始](../start/quickstart.md)里的主体和运行插件，把计划的生成、交付和结果走一遍。完整字段见[运行参考](../reference/runtime.md)、[执行器参考](../reference/runner.md)。

## 把业务阶段定义出来

`Phase(name, run, prepare=None, execution="serial", incomplete="fail_step", capacity=None)` 描述一项阶段工作。`run` 接收 PhaseContext，可以在里面激活主体并返回 StepResult；多个 Phase 放进 `StepPlan.phases` 后按列表顺序执行。默认串行，同一位主体按提交顺序行动，后面的主体能读到前面已经提交的 live 状态。只有业务上确实互不冲突，才选 independent；并发容量由领域语义声明，Runtime 不会分析读写关系替你推断是否安全。

下面这段阶段定义放进已有的运行计划：每个时点激活 Alice 和 Bob，把收到的决定数写进阶段指标。主体、驱动和 Runtime 沿用[自己的计划](../tutorial/first-plan.md)。

~~~python
from society0.kernel.results import StepResult
from society0.kernel.runtime import Phase
from society0.kernel.schedule import activate

async def decide(context):
    decisions = await activate(context, ("alice", "bob"))
    return StepResult(metrics={"decisions": len(decisions)})

decision_phase = Phase("decide", decide)
~~~

这里要求主体目录、RuleDriver 或 LLMDriver、interaction 和 results 插件已经装在同一主机上，Phase 自己不会另开一个环境。完整的两位主体装配和结果表例子见 GitHub 上的 [rule_run.py](https://github.com/lavapapa/society0/blob/@COMMIT@/examples/core_next/rule_run.py)。

## 由 Schedule 生成 StepPlan

`SequenceSchedule` 保存整条时间序列和共享的阶段序列。它的 `next_step(completed_step)` 用已完成步数定位下一个时间，自己不推进游标：`completed_step` 为 0 时返回 `times[0]`，完成首步后传 1，返回 `times[1]`。自定义 Schedule 实现同一个异步接口，可以从权威完整状态推出动态计划；执行领域阶段和发布检查点不归它管。

~~~python
from society0.kernel.schedule import SequenceSchedule

timeline = SequenceSchedule(times=(1, 2, 3), phases=(decision_phase,))
plan = await timeline.next_step(completed_step=0)
# plan.time 为 1，plan.phases 为 (decision_phase,)
~~~

应用里用 `schedule_plugin(timeline)` 发布 Schedule 服务。时间序列要盖住本次研究声明的完整运行范围；研究者从第 1 步恢复时，runner 传入的已完成步数是 1，调度因此返回时间 2。时点值是模拟时间，等模型或网络不会让它往前走。

## 把计划交给 Runtime 执行

Runtime 从计划里取 `time` 和 `phases`，步骤编号用下一个连续整数。直接在代码里组合时，计划与执行的调用关系如下；这段代码位于已经打开的 compose 异步作用域，`host` 是主机，`store` 是从 storage 取得的共享存储。

~~~python
plan = await host.service("schedule", "schedule").next_step(store.complete_step)
if plan is not None:
    number = store.complete_step + 1
    receipt = await host.service("runtime", "runtime").run_step(
        number, plan.time, plan.phases
    )
~~~

正式运行一般交给 runner 的 `run_plan(path, plan, source=None, step=None)`：它反复读取下一份计划、调用 Runtime、记录 `RunContract` 和逐步诊断。before 钩子、业务阶段、after 钩子全部成功之后，Runtime 才发布完整步骤；Driver 返回 waiting 表示之后还可以再激活，返回 incomplete 默认让步骤失败，除非这个 Phase 明确选了 collect。预算耗尽、模型截断这类未完成情况，保留它真实的终态。运行后的指标和主体结果读取见[结果教程](../tutorial/results.md)，完整调用细节见[执行器参考](../reference/runner.md)和[结果参考](../reference/results.md)。

以轮次研究为例，Schedule 可以把同一时点的阶段排成“开启新轮、主体交流、汇总测量”：第一阶段调用轮次机制的 `start_round`，第二阶段用 `activate` 提交需要行动的主体，最后一个阶段把本轮的配对数量或消息数放进 `StepResult`。一个阶段做完 Runtime 才进下一个，所以后面的阶段能看到前面已经提交的权威状态。恢复时 `SequenceSchedule` 只按最近的 `complete_step` 重新生成余下时间的计划，还没完整发布的半轮不会被当成已完成的时间点。

阶段运行函数抛出异常时，Runtime 会取消该阶段还在执行的工作，并调用存储的失败路径，这个实例不能再跑下一步。调用者先查清异常从哪来、完整下界在哪，再从可信步骤恢复。运行期间等网络不会改变 `plan.time`，计划里的时间只由研究者写的时间线决定。用这套流程，你只需要决定每个时点发生哪些业务阶段、哪些结果能回答研究问题，执行身份和恢复边界由通用运行层负责。

## 恢复一次，验证时间和阶段

先用业务时点 `(1, 2, 3)` 跑三步，读取每一步的阶段结果，核对 `time`、阶段名和主体数量；再从第 1 步的完整步骤恢复，确认只补做了第 2、3 步对应的工作。阶段里的每个领域动作都保存实际回执，用来验证后面的阶段确实读到了前面阶段的结果。

采用 `incomplete="collect"` 时，结果表要同时保存各主体的原始 status 和 reason，把决定完成和显式收集的未完成区分开。`RunContract` 里记录的有效容量与激活上限，应当和计划实际使用的值一致；主体并发的选择见[三类并发](parallel.md)。

`next_step` 返回 None 时，runner 结束循环并标记 completed。研究上的解释仍然要回到结果、对照和证据口径，详见[研究边界](../concepts/research.md)。
