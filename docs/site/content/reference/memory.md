# Memory：经验、召回与迁移

`from society0.kernel.memory import Memory, MemoryPolicy, MemoryActivation, MemoryExtension`。Memory 管理主体经验的权威正文、向量和作业；MemoryExtension 将它接入规则或模型激活。

## 一、策略与作用域

三项开关独立决定自动召回、自动写入和主动工具。每次激活可通过 policy_selector 返回 MemoryActivation，将策略和自动召回数量一起冻结。

<!-- api: society0.kernel.memory.MemoryPolicy -->
<!-- api: society0.kernel.memory.MemoryActivation -->
<!-- api: society0.kernel.memory.Memory -->
<!-- api: society0.kernel.memory.MemoryExtension -->

自动召回需要 recall_query；decision 自动写入需要提取器，激活装配时缺少依赖会失败。自动写入针对 completed 或 waiting，incomplete 不进入成功提取，interview 默认跳过自动写入。主动 remember、recall、update、delete 与记忆原文目录按 active_tools 控制，关闭自动召回仍可保留写入和主动访问。

激活默认以 `Session.step` 作为可见版本与衰减序号，进入作用域时固定，单位为完整步骤。`Moment.time` 保留领域时间原文。显式 seed、recall 等高级调用可传入自己的逻辑序号；timestamp 表示经验时间，visible_step 表示何时可见，同主体可见序号须非递减。

## 二、权威与检索

SQL 保存正文、原始双精度向量、版本、Thread 关联及作业回执；Chroma 是可重建检索索引。原始向量和派生索引的精度分别遵循各自存储实现。

<!-- api: society0.kernel.memory.Memory.prepare_job -->
<!-- api: society0.kernel.memory.Memory.finish_job -->
<!-- api: society0.kernel.memory.Memory.recall -->
<!-- api: society0.kernel.memory.ThreadMemoryExtractor -->

prepare_job 返回固定作业 ID，finish_job 完成嵌入、权威保存、索引同步与回执。索引失败后复用 SQL 中已有向量。自动作业身份绑定 Thread 及输入消息水位；已完成回执随以后记忆修改继续保留。

recall 先等待该主体待写作业，返回带完整内容和 score 的候选列表。当前查询使用活动投影；查询过去可见时点时按历史版本建临时集合，结束后清理。候选按向量距离加重要性时间衰减排序，并按类型和原内容去重。未指定 current_step 时不衰减。

ThreadMemoryExtractor 在原 Thread 追加提示并使用结构化输出；合法空数组表示无新增经验。格式错误允许一次纠正，length 保持未完成。提取使用完整历史，模型和嵌入均产生外部资源成本。Chroma 同步调用运行在调用线程；历史检索还可能持有较长 SQL 快照。

## 三、转移与关闭

当前有效记忆可导出为种子资料，完整历史的恢复与分叉使用存储完整点。

<!-- api: society0.kernel.memory.Memory.seed -->
<!-- api: society0.kernel.memory.Memory.export -->
<!-- api: society0.kernel.memory.Memory.import_records -->
<!-- api: society0.kernel.memory.Memory.close -->

export 在一个只读快照中逐条调用同步 consume，返回条数；正文与向量完整交付，长导出会保留读取快照。import_records 在一次事务内导入一批，返回数量，保留逻辑 ID 与原向量且不重做嵌入；维度、正文错误或重名使该批回滚。多个批次中途失败时，已提交批次按失败步骤处理。

close 取消并等待自己管理的任务，不关闭注入的客户端和提供方。插件退出顺序先收束 Memory，再释放依赖资源。

```python
from society0.kernel.memory import MemoryPolicy

policy = MemoryPolicy(auto_write=True, auto_recall=False, active_tools=True)
# 交给已配置提取器的 memory_plugin；写入、主动访问保持开启。
```

装配见[服务工厂](services.md)，学习路径见[记忆教程](../tutorial/memory.md)。具体实现为 [Memory](https://github.com/lavapapa/society0/blob/@COMMIT@/src/society0/kernel/memory.py#L139)。
