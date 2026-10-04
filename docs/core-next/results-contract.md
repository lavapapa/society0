# 调度结果与进度

代码步骤沿同一个 Runtime 执行并在完整步骤边界发布。结果是可恢复事实，进度是允许短暂落后的观察信息；这两类写入分别处理故障。

## 一、执行

CodeSchedule 持有 Runtime 和顺序 Phase 列表，run_step 直接调用 Runtime.run_step。同步规则、异步规则、LLM 与 interview 均保留原 Driver 会话合同。选择器交付主体 ID 的迭代序列，队列在执行时按需取得 Actor。执行、收尾和完整步骤发布仍由 Runtime 负责。`RuleDriver(callback)` 接受同步或异步回调，回调显式返回 DriverResult。`activate(context, identifiers)` 接收主体 ID 的普通或异步迭代器，并返回这批 drain 的有序结果。

`runtime_plugin` 从所属主机取得完整的步骤钩子，在每步开始时固定顺序，执行 before、业务 phases、after 后再 complete。机制在安装时调用 `context.on_step(before=..., after=...)`；回调采用零参数 bound method。主机退出时先执行 quiesce，取消并排空 Runtime，再按依赖逆序关闭机制资源。关闭主机不会执行业务步骤钩子。

阶段函数可返回 StepResult，字段为 metrics、tables、artifacts、observations、notes。结果插件按阶段出现的顺序保存该返回值；同名阶段可在同一步重复出现，身份含阶段序号。run_step 返回 StageStore 的完成凭据，结果通过结果服务读取。

## 二、事实

Results 由插件声明原生 SQLite 表，正文调用规范 Writer 编码入口，与 Thread 和 Memory 共用编码资源。表接受单次迭代器，逐行写入，按有限行数分批事务；不先转成列表。每行保存原始 JSON 与完整字节数，大行以引用按范围取得，分页保留精确 total、固定读取上界和继续位置。

`tables` 的普通列表和生成器表示逐行数据。`TableValue(value)` 表示一份完整 JSON 值，保存为一条记录；例如 DataFrame 使用 `TableValue(frame.to_dict(orient="tight"))`，显式保留列、索引、名称与数据次序。模型对象先调用其公开导出方法。裸 Mapping、字符串与 DataFrame 会收到输入形状错误，防止把字段名当成数据行。此入口无需安装 pandas。

组合多个机制时，登记一次 `dataset_plugin()`；机制通过声明依赖取得共享 `datasets` 服务，自身 schema 仅声明领域表。普通行结果无须装配该服务。

`DatasetTable(reference)` 复用已由 Datasets 封存并登记的数据集，正文文件保持原引用。`Results.page` 对两类存储都返回相同页项结构：`{ordinal, raw_bytes, value}` 保存原始业务值，巨行则返回 `{ordinal, raw_bytes, payload_ref}`；将 `payload_ref` 原样传给 `read_record` 读取完整原文。业务 JSON 的任何字段均位于 value 内，不参与引用识别。页还保留精确 total 和继续游标，最终 JSON 字节预算包含页项外壳。完整值占一条记录，数据集按其原记录数计入 `row_count`。DataFrame 或模型的显式转换成本由所选择的完整值大小决定。

metrics、artifacts、observations、notes 同样保持完整 JSON 或字符串。当前指标索引引用原始结果行；汇总计数在规范写入时维护。`metric(phase, name)` 明确读取单个当前指标，阶段再次返回指标后用新的键集合替换该阶段投影；旧指标原文仍在历史结果中。`summary()` 返回累计阶段、表行和各激活状态的计数，不重放历史。`step(number)` 返回该次完整步骤候选的模拟时间、阶段数、实际激活数、耗时、容量和激活预算；是否正式完成依照步骤描述符。成功返回 DriverResult 的主体激活记录保存 actor、round、status、reason、时长和值（含显式 collect 的 incomplete），LLM 值沿既有 Thread/资源引用追溯原文。抛错或取消的阶段通过原始 Thread、失败标记和进度诊断追溯，阶段结果表不冒充已经完成。结果写入失败使完整步骤失败，已写的部分结果属于原运行诊断，恢复依照完整步骤描述符。

## 三、观察

进度使用独立小型 JSON 快照，记录 run_id、尝试步骤、阶段、活动数、已结束激活数与更新时间。Runtime 在稳定生命周期边界更新，不复制业务对象或完整 Thread。写入采用临时文件后原子替换，进度 I/O 故障不改变业务事实；观察者可以根据时间识别陈旧快照。

完成与失败身份继续读取 StageStore 描述符和原生失败标记。业务 SQLite、Thread、结果事实的错误继续传播；进度快照属于独立诊断介质，其错误不会被用于忽略权威存储的失效。HTTP 与 Python 观察器读取同一进度快照和权威身份，后续不建立第二套运行状态。

## 四、组合

以下组合让规则主体、持久结果、进度和调度共用一个运行主机。`domain_plugins` 代表调用方提供的领域插件；它们通过 on_step 登记必要的步骤收束。运行路径由调用方选定。

```python
from society0.kernel.actors import ActorRecord, actor_plugin
from society0.kernel.composition import compose
from society0.kernel.interaction import interaction_plugin
from society0.kernel.results import StepResult, results_plugin
from society0.kernel.runtime import DriverResult, Phase, runtime_plugin
from society0.kernel.schedule import RuleDriver, activate, progress_plugin, schedule_plugin

async def decide(context):
    results = await activate(context, ('alice',))
    return StepResult(metrics={'activated': len(results)})

plugins = [
    actor_plugin({'rule': lambda record: RuleDriver(lambda session: DriverResult('completed'))},
                 records=[ActorRecord('alice', 'rule')]),
    interaction_plugin(lambda scope, operation, target: True),
    results_plugin(), progress_plugin(),
    runtime_plugin(actor_service=('actors', 'actors'),
                   information=('interaction', 'information'), actions=('interaction', 'actions'),
                   store=('storage', 'store'), results=('results', 'results'),
                   progress=('progress', 'progress')),
    *domain_plugins,
    schedule_plugin([Phase('decide', decide)]),
]
async with compose(run_directory, plugins) as host:
    receipt = await host.service('schedule', 'schedule').run_step(1, 0)
    counters = host.service('results', 'results').summary()
```

表分页返回固定读取条数上界、精确 total 和游标；结果引用可随完整恢复读取，游标绑定当前运行身份，恢复分支应从第一页重新查询。超过页预算的单行返回 record_ref，通过 `read_record(ref, offset=..., size=...)` 连续取得完整 JSON 字节。`phase(step, index)` 与 `metric` 是调用者明确选择的原文读取接口，巨大字段应使用结果集的分页与范围读取。
