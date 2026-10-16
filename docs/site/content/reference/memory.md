# Memory：经验、召回与迁移

`from society0.kernel.memory import Memory, MemoryPolicy, MemoryActivation, MemoryExtension`。Memory 管理主体经验的权威正文、向量和作业；MemoryExtension 把这些能力接进规则主体或模型主体的激活过程。向量客户端、嵌入函数和提取器都由你注入，Memory 只管好自己启动的任务和写入的权威数据。

## 三项策略与激活作用域

自动召回、自动写入和主动工具各由一个开关控制。

<!-- api: society0.kernel.memory.MemoryPolicy -->
<!-- api: society0.kernel.memory.MemoryActivation -->
<!-- api: society0.kernel.memory.Memory -->
<!-- api: society0.kernel.memory.MemoryExtension -->

每次激活都可以通过 policy_selector 返回一个 MemoryActivation，把这次用的策略和自动召回数量固定下来。自动召回需要有 recall_query；decision 模式的自动写入需要有提取器，装配激活时缺依赖会失败。自动写入只处理 completed 或 waiting，incomplete 不会被当成成功经验提取，interview 默认跳过自动写入。主动的 remember、recall、update、delete 和记忆原文目录都由 active_tools 控制；关掉自动召回后，写入和主动访问仍然可以保留。

激活默认用 `Session.step` 作为可见版本和衰减序号，进入作用域时就固定下来，单位是完整步骤。`Moment.time` 保留领域时间的原始值。seed、recall 这类高级调用可以自己传逻辑序号；timestamp 是经验发生的时间，visible_step 是它从哪一步开始可见，同一主体的可见序号必须非递减。

## 权威数据与检索索引

SQL 保存正文、原始双精度向量、版本、Thread 关联和作业回执；Chroma 是可以重建的检索索引。原始向量和派生索引的精度各自跟随所属存储实现。

<!-- api: society0.kernel.memory.Memory.prepare_job -->
<!-- api: society0.kernel.memory.Memory.finish_job -->
<!-- api: society0.kernel.memory.Memory.recall -->
<!-- api: society0.kernel.memory.ThreadMemoryExtractor -->

prepare_job 返回一个固定的作业 ID，finish_job 负责完成嵌入、写入权威数据、同步索引和记录回执。索引失败之后会复用 SQL 里已经有的向量。自动作业的身份绑定到 Thread 和输入消息的水位；已经完成的回执在之后的记忆修改中继续保留。

recall 会先等该主体待写的作业完成，再返回候选列表，每项都带完整内容和 score。查询当前状态时使用活动投影；查询过去某个可见时点时，按历史版本临时建集合，用完清理。候选按向量距离加上重要性的时间衰减排序，并按类型和原内容去重。没有指定 current_step 时不做衰减。

ThreadMemoryExtractor 在原 Thread 上追加提示，并要求结构化输出；合法的空数组表示没有新增经验。格式错误允许纠正一次，结束原因是 length 时保持未完成。提取使用完整历史，模型调用和嵌入调用都会产生外部资源开销。Chroma 的同步调用在当前调用线程上运行；检索历史时还可能持有较长时间的 SQL 快照。

## 导出、导入与关闭

当前有效的记忆可以导出成种子资料；要恢复或分叉完整历史，用存储里的完整步骤。

<!-- api: society0.kernel.memory.Memory.seed -->
<!-- api: society0.kernel.memory.Memory.export -->
<!-- api: society0.kernel.memory.Memory.import_records -->
<!-- api: society0.kernel.memory.Memory.close -->

export 在一个只读快照里逐条调用同步的 consume，返回条数；正文和向量都完整交付，导出时间较长时读取快照会一直保留。import_records 在一次事务里导入一批并返回数量，保留逻辑 ID 和原向量，不重新做嵌入；维度不对、正文有问题或出现重名都会让这一批回滚。分多批导入而中途失败时，已经提交的批次按失败步骤处理。

close 取消并等待自己管理的任务，注入的客户端和提供方不归它关。插件退出时先停掉 Memory，再释放它依赖的资源。

```python
from society0.kernel.memory import MemoryPolicy

policy = MemoryPolicy(auto_write=True, auto_recall=False, active_tools=True)
# 交给已配置提取器的 memory_plugin；写入、主动访问保持开启。
```

装配见[服务工厂](services.md)，学习路径见[记忆教程](../tutorial/memory.md)。具体实现为 [Memory](https://github.com/lavapapa/society0/blob/@COMMIT@/src/society0/kernel/memory.py#L139)。
