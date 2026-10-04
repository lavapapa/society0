# 代码调度

研究协议由 Schedule 提供模拟时间与显式 Phase。默认串行阶段保持共享状态的业务顺序；明确独立的主体活动可以使用 independent 阶段。公共时间由研究计划传入 Moment.time，完整步骤序号由 Session.step 提供。

## 一、活动

Phase.run 接收上下文，通过 activate(actor_id, payload) 和 drain() 取得 ActorResult。重复激活同一时点继续相同 Thread 与感知游标。Phase.prepare 可在阶段前构建一次参与者共用的只读投影；工作量由实际投影大小决定。待办事项可以继续开放，waiting 与 incomplete 有明确区别。默认 incomplete 阻止完整发布；研究明确需要收集未完成结果时选择 collect，真实异常仍使步骤失败。

```python
async def decide(ctx):
    records = await activate(ctx, selected_actor_ids)
    return StepResult(tables={'decisions': result_rows(records)})

from society0.kernel.schedule import SequenceSchedule

schedule = SequenceSchedule([1, 2], [Phase('decide', decide)])
plan = await schedule.next_step(completed_step=0)
await runtime.run_step(1, plan.time, plan.phases)
```

serial 容量为 1；independent 的显式 Phase.capacity 覆盖 Runtime.capacity。物理模型请求并发由端点与共享请求额度另外约束。选择器支持角色、active、谓词和流式抽样；分析全人口是显式成本。主体调度容量、端点请求槽位与计算进程数分别控制各自资源。多核计算通过显式 `compute.run` 提交独立函数任务；单次调用的内部拆分由算法决定，接线见 [共享计算](../../docs/core-next/runner-contract.md#共享计算)。

## 二、认知与记忆

LLMPolicy(mode='decision') 执行行动循环，mode='interview' 使用 result_schema 和 submit_result 测量。required_names/tags 表达完成所需行动，completion_names/tags 与 Action.terminal 表达真实结束语义。硬预算耗尽或输出截断保留未完成状态，完整 Thread 不裁剪。

CognitiveInput 注入完整人格、主观状态、环境说明与研究指定精度；感知回调返回新增消息与持久游标。重要信息始终可通过 Information 取得。记忆三开关独立，memory_plugin 的 policy_selector 可以按激活选择。研究定义的提取策略通过 memory_plugin(extract=...) 接入，MemoryExtension 在 Thread 关闭前收束成功经历；原 Thread 留证、完整输入水位和记忆作业身份同时保留。

## 三、结果

StepResult 的 metrics 保存数值，observations 保存完整 JSON，tables 接受明确行序列。完整结构用 TableValue，已封存冷数据用 DatasetTable；DataFrame 先显式转为保列、索引和顺序的结构，例如 to_dict(orient='tight')。result_rows 保主体、轮次、状态、原因与完整值；result_mean(records, ('result','score')) 明确读取 LLM 访谈字段，规则直接值用字符串字段。Results.page 返回明确 value/payload_ref 外壳，巨行原文按引用范围读取。

每完整步骤由 Runtime 统一完成，业务钩子、主体与工件收束后才发布。模型等待、工具执行和激活总时长存在包含关系，不直接相加为独立成本。
