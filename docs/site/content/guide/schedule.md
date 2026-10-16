# 用 Schedule 生成步骤计划

一个仿真步骤同时有业务时间和有序阶段。Schedule 根据最近一个完整步骤提出下一份计划，Runtime 接手执行计划并在各阶段收束后发布完整点。把这两件事分开后，恢复时可以依据已发布位置重新询问 Schedule，避免调度器游标与实际状态各走各的。本页沿用[快速开始](../start/quickstart.md)中的主体和运行插件，重点展示计划的生成、交付与结果。完整字段见[运行参考](../reference/runtime.md)、[执行器参考](../reference/runner.md)。

## 1. 定义业务阶段

Phase(name, run, prepare=None, execution="serial", incomplete="fail_step", capacity=None) 描述一项阶段工作。run 接收 PhaseContext，可在其中激活主体并返回 StepResult；多个 Phase 放进 StepPlan.phases 后依列表顺序执行。默认串行，同一主体按提交顺序行动，后续主体可读到之前已提交的 live 状态。只有业务上确实互不冲突时才选择 independent；并发容量是领域语义声明，Runtime 不会分析读写自动推断安全。

下面是放入已有运行计划的阶段定义，在每个时点激活 Alice 和 Bob，并将收到的决定数写为阶段指标。主体、驱动和 Runtime 沿用[自己的计划](../tutorial/first-plan.md)：

~~~python
from society0.kernel.results import StepResult
from society0.kernel.runtime import Phase
from society0.kernel.schedule import activate

async def decide(context):
    decisions = await activate(context, ("alice", "bob"))
    return StepResult(metrics={"decisions": len(decisions)})

decision_phase = Phase("decide", decide)
~~~

这里的主体目录、RuleDriver 或 LLMDriver、interaction 与 results 插件需预先安装到同一主机；Phase 本身不创建第二个环境。完整的两个主体装配与结果表例子可查看 GitHub 的 [rule_run.py](https://github.com/lavapapa/society0/blob/@COMMIT@/examples/core_next/rule_run.py)。

## 2. 生成 StepPlan

SequenceSchedule 保存完整时间序列与共享阶段序列。它的 next_step(completed_step) 以已完成步数定位下一时间，不推进内部游标：completed_step 为 0 时返回 times[0]，完成首步后传入 1，返回 times[1]。自定义 Schedule 实现同一异步协议，可从权威完整状态推导动态计划；它不负责执行领域阶段或发布检查点。

~~~python
from society0.kernel.schedule import SequenceSchedule

timeline = SequenceSchedule(times=(1, 2, 3), phases=(decision_phase,))
plan = await timeline.next_step(completed_step=0)
# plan.time 为 1，plan.phases 为 (decision_phase,)
~~~

应用可用 schedule_plugin(timeline) 发布 Schedule 服务。时间序列要覆盖本次研究声明的完整运行范围；若研究者恢复到第 1 步，runner 会传入已完成步数 1，调度因此返回时间 2。时点值是模拟时间，等待模型或网络不会替它推进。

## 3. 交给 Runtime 执行

Runtime 从计划取得 time 与 phases，使用下一连续整数步骤编号执行。直接组合时，计划和执行的调用关系如下。此段位于已打开 compose 的异步作用域，host 为该主机，store 是从 storage 取得的共享存储：

~~~python
plan = await host.service("schedule", "schedule").next_step(store.complete_step)
if plan is not None:
    number = store.complete_step + 1
    receipt = await host.service("runtime", "runtime").run_step(
        number, plan.time, plan.phases
    )
~~~

正式运行通常交由 runner 的 run_plan(path, plan, source=None, step=None)：它重复读取下一计划、调用 Runtime、记录运行合同与逐步诊断。Runtime 在 before 钩子、业务阶段、after 钩子全部成功后才发布完整点；Driver 返回 waiting 表示可之后再激活，返回 incomplete 默认使步骤失败，除非该 Phase 明确选择 collect。预算耗尽、模型截断等未完成情况应保留其真实终态。运行后的指标与主体结果读取见[结果教程](../tutorial/results.md)，完整调用细节见[执行器参考](../reference/runner.md)和[结果参考](../reference/results.md)。

以轮次研究为例，Schedule 可把同一时点的阶段排为“开启新轮、主体交流、汇总测量”。第一阶段调用轮次机制的 start_round，第二阶段用 activate 提交需要行动的主体，最后阶段将本轮配对数量或消息数放进 StepResult。每个阶段完成后 Runtime 再进入下一个阶段，因此后阶段能看到此前阶段已经提交的权威状态。恢复时 SequenceSchedule 只根据最近 complete_step 重新生成余下时间的计划，不会把尚未完整发布的半轮当成已完成时间点。

如果阶段运行函数抛出异常，Runtime 会取消该阶段尚在执行的工作并调用存储的失败路径，当前实例不能继续运行下一步。调用者应先查明异常来源和完整下界，再从可信步骤恢复。运行期间等待网络不会改变 plan.time，计划中的时间仅由研究者的时间线确定。用这套流程时，开发者只需决定每个时点要发生哪些业务阶段、哪些结果能回答研究问题，执行身份与恢复边界由通用运行层负责。

## 4. 验证时间与阶段

先使用业务时点 `(1, 2, 3)` 运行三步，读取每一步的阶段结果并核对 time、阶段名和主体数量；再从第 1 步完整点恢复，检查仅追加第 2、3 步对应的工作。为阶段中的每个领域动作保存实际回执，验证后阶段确实读到前阶段的结果。

若采用 `incomplete="collect"`，结果表应同时保存各主体原始 status 和 reason，区分决定完成与显式收集的未完成。运行合同中的有效容量与激活上限应与实际计划一致；主体并发的选择见[三类并发](parallel.md)。

当 next_step 返回 None，runner 结束循环并标记 completed。研究解释继续依赖结果、对照与证据口径，详见[研究边界](../concepts/research.md)。
