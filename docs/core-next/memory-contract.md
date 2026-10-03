# Memory 插件合同

Memory 保存主体自己的经验，并将候选检索交给 Chroma。SQL 原文、原始向量、版本与作业回执随 StageStore 的完整步骤恢复；Chroma 可以从这些事实重建。普通规则主体无需创建 Memory 或初始化向量库。

## 一、写入与恢复

`MEMORY_SCHEMA` 与 Thread 等机制的 schema 一起交给 StageStore。`Memory(store, threads, embed=..., client=..., extract=..., policy=..., recall_query=..., recall_top_k=10)` 使用同一个规范写入器；embed 接收文本列表与 metadata，并逐项返回向量。metadata 包含 actor、用途，以及适用时的 thread_id、job_id、memory_ids。提供方负责物理合批留证，Memory 保留每项与原主体的对应关系。

`prepare_job(actor, thread_id, job_key, timestamp=..., visible_step=..., entries=...)` 保存提取结果和固定作业身份；`finish_job(job_id)` 依次完成嵌入、SQL 写入、派生索引同步及回执。索引失败时 SQL 已保存的向量供重试复用。待处理作业被明确纳入完整步骤后，恢复可以继续处理；未完成步骤里的作业属于诊断状态。完整点之后发生的外部调用，在恢复后可能重新执行。

默认自动写入在激活完成后等待作业结束。作业身份由 Thread 与固定输入消息水位组成，同一输入重试复用作业，新激活增加事实后取得新的输入水位。已完成作业回执描述原操作，之后修改或删除记忆不会使原操作重新执行。正在准备的同身份作业仍要求原内容一致。

正文与用户 metadata 复用 Thread 的分块 JSON 编码，原始向量以双精度 BLOB 独立保存。修改热状态无需重新编码正文。单项内容需要完整交给模型时，其完整解码内存仍是明确成本。

## 二、可见性与检索

`timestamp` 表示经验所述时间，用于时间衰减；`visible_step` 表示写入可见时点，默认与 timestamp 相同。主体可以在当前时点记录过去发生的经验。写入可见时点按主体非递减，同一步内多次修改以该步最终版本为准。`update`、`delete` 在同一 SQLite 事务封存旧版本并维护当前投影；fork 通过新数据库身份隔离后续修改。

`recall(actor, query, top_k=10, current_step=..., thread_id=...)` 先等待该主体待处理写入。当前查询使用活动投影和独立 Chroma 集合；历史版本增长不进入当前候选扫描。查询过去时点时，SQL 按可见区间流式取得历史原向量，分批构建临时 Chroma 集合并在退出时删除。该显式历史操作随相关历史与候选集合增长，可能持有较长 SQL 读快照并同步占用调用线程。

候选沿用 L2 距离的 `2 × top_k` 范围，再以 `clamp(1-distance,-1,1) + 0.1 × importance × exp(-decay_rate × age)` 排序；无请求时点时不衰减。候选按记忆类型与原内容去重并保留较高得分。importance 缺省为既有实现实际采用的 3.0。查询嵌入等待结束后重新检查可见水位，必要时复用已得查询向量转入历史路径；候选与正文选择阶段不再挂起。

SQL 保留提供方原始双精度向量，Chroma 当前实现按 float32 存储。恢复索引使用相同原向量和相同距离配置；候选身份、原文、评分与最终上下文需要共同验证。Chroma 水位在整批索引更新完成后发布。其同步 API 当前运行在调用线程，异步方法名称不代表后台执行；并发化须先验证发布顺序及事件循环延迟。

## 三、主体接口

`MemoryPolicy` 的 auto_write、auto_recall、active_tools 独立配置。`before_activation` 根据召回查询构造器和服务级 recall_top_k（默认 10）取得原文记忆；主动 recall 的 top_k 参数保持独立。该钩子也可给出主动工具入口；`after_activation` 仅对 completed 或 waiting 结果写入成功记忆。incomplete 不触发成功提取。kind 为 interview 的 Thread 默认跳过自动经验写入，自动召回仍由独立开关决定。主动记忆通过既有 Actions 的 memory 命名空间提供 remember、recall、update、delete，权限绑定主体身份。

`ThreadMemoryExtractor` 在原 Thread 追加提取提示，经标准 ModelProvider 调用强制工具并记录完整响应。工具声明、提示与解析规则来自共用提取协议；合法空数组表示没有新增记忆。协议不合格时允许一次纠正回合，输出 length 直接记录为未完成。原始 Thread 上下文保持完整，提供方物理重试与资源限制由 ModelProvider 负责。

权威恢复、检索重建和模型生成属于不同边界。确定性测试验证版本与回执一致性，真实 Chroma 小样验证实际距离及精度，真实模型与嵌入服务仍需通过单独端到端验收确认参数和调用链。

## 四、转移与资源

`seed(actor, job_key, timestamp=..., entries=..., visible_step=...)` 复用持久作业流程，固定 seed 身份的重试取得原回执。`export(actor, consume)` 在一个只读 SQL 快照中逐条向同步消费函数交付当前有效记忆，包含完整正文、用户 metadata、importance、timestamp、逻辑 id 和原始向量；返回记录数。消费函数可持续写入外部流，完整导出本身会持有读取快照，运行中使用时需考虑 WAL 保留成本。

`import_records(actor, records, visible_step=...)` 消费一个迭代批次，在一次原生事务中保存原文、原向量及当前索引水位，返回导入数量。整个批次出现维度或正文错误会回滚。调用者通过多个明确批次控制一次 Session 捕获量，并在全部成功后发布完整步骤；中间批次已提交时发生错误，应终止该步骤并依据完整边界恢复。导入保留逻辑 id，重名由原生唯一约束拒绝；该操作不再次调用嵌入服务。历史版本与删除事实的完整转移使用 StageStore restore/fork，当前记忆导出用于明确的记忆种子或资料交换。

`close()` 取消并等待 Memory 自己持有的未完成作业任务，SQL 中的准备状态继续留作诊断或被明确纳入完整步骤。关闭后的写入、召回与提取入口拒绝继续执行，job/get 等原事实读取仍可用于诊断。注入的 Chroma 客户端、模型与嵌入提供方由创建者持有，Memory 不关闭共享资源。现有 Plugin 的依赖关系和逆序退出负责先收束 Memory，再关闭创建它们的资源插件。具体 Chroma 客户端的关闭动作由相应资源创建者提供，借用客户端不推定底层 system 为本实例独占。
