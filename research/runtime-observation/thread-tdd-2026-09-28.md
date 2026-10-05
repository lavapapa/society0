# Thread 观察读取的 TDD 记录

测试解释了本次修改的四个主要风险：观察者修改正在追加的文件、分页读取全部历史、正文引用丢失信息、历史 Thread 数量使定位与缓存持续增长。

首先新增 `tests/primary/test_thread_observation.py` 的四项测试并运行，结果四项均失败。元数据读取因调用 `_materialize_payload` 触发测试哨兵；两项分页测试因缺少 `read_event_page` 失败；缓存测试因缺少 `cache_max_entries` 参数失败。原始代码中的尾部修复也会修改只读源文件。

实现后扩展为九项观察读取测试，加上原有 Thread 存储、完整性、记忆和嵌入追溯测试，共 46 项通过。命令为 `PYTHONPATH=src <基础仓库 .venv/bin/python> -m pytest tests/primary/test_thread_observation.py tests/primary/test_agent_thread_store.py tests/primary/test_thread_integrity.py tests/primary/test_thread_native_memory.py tests/primary/test_embedding_thread_trace.py -q`。

分页在写入时维护的定长 offset 文件上定位，精确 total 来自捕获的 offset 数目。续读绑定运行目录、Thread 和捕获边界；页间新增事件不会进入旧页。测试逐页检查 events 数组的实际 UTF-8 JSON 字节数，包含方括号、逗号以及中文字符。超大事件用引用占一项，正文分段拼接后能够还原原始 JSON。

独立子进程读取包含半条 JSON 尾部的文件后，原始字节完全不变；写者重启后仍能修复半条记录并追加。观察者在线程中读取时不等待写者持有的运行锁。扩大到 201 条记录后，读取两条记录的 JSONL 字节量仍低于 4 KB。新建及冷启动读取将 `Path.glob` 替换为失败哨兵仍可完成，缓存条目数保持配置上限；超过 64 KiB 的 opened payload 不进入缓存，完整内容继续能够读取。

本次未测量真实模型服务、远端文件系统及数 GiB 单条记录的峰值 RSS。观察接口不改变 `read_messages` 向模型提供完整历史的语义。新运行写入 locator 与 offsets；旧产物缺少这些派生文件时，分页入口明确报缺少索引，未增加查询时全目录扫描或自动迁移。

旧入口清理新增 `test_runtime_entrypoint.py`，五个旧模块存在断言首先失败，当前共享事务日志测试通过。删除旧 SimEngine、StreamingBridge、节点 diff、YAML 调度、独立 replay 写入与恢复方法后，当前运行检查点和入口测试通过。旧模块对应的两个专用测试随接口删除；当前 CodeSchedule、事务、事件批次及监听接口保留。

记忆集合新增 `test_memory_epoch_sharing.py`，50 个 Memory 共用 10,000 个已发布 epoch 的测试首先失败于对象身份检查。实现发布者拥有集合、World 和 Memory 共用只读成员查询后，两项测试通过；与记忆可见性、运行恢复和当前入口共 26 项通过。不同分支仍各自持有集合，失败 epoch 不可见。社会网络向量查询复用 World 的同一只读集合。

结果大表新增 `test_result_datasets.py`，最初两项失败于缺少数据集读取模块和 `StepResult.to_dict(result_dir=...)` 参数。实现后迭代表、单条超大数据和原有小表语义通过。第三项先失败于缺少提交关联函数，随后验证每个步骤数据集在本步骤 annotations 登记，完整 checkpoint manifest 才确认其提交；根 marker 与未登记孤立数据集均不可冒充已提交结果。

汇总改为一次扫描资源日志，同时更新全局资源、业务操作和主体批次统计；`by_tick`、`agent_batches`、`by_interaction` 保存为数据集引用。完整/分页读取共用 `result_datasets`，输出目录仅遍历一次统计逻辑字节，包含检查点子目录、Thread、向量库与结果数据集。统计明确排除稍后写出的 summary.json 自身；不再为了统计行数重读 JSONL，不再循环重写汇总以逼近自身文件大小。新测试记录资源日志仅打开读取一次，并核对嵌套目录和全部纳入文件的字节总和。

完整确定性验收使用 `SOCIETY0_RUN_REAL_E2E=0 PYTHONPATH=src <基础仓库 .venv/bin/python> -m pytest -o addopts= -q -m 'not real_e2e'`。第一轮 556 项通过、8 项失败、13 项真实服务测试排除；其中七项原断言需要通过新历史读取接口读取，另一项为存储格式断言。显式 `load_run_summary(include_history=True)` 及离线报告读取历史后，原端到端测试继续核对批次、成功失败动作、逐步环境钩子统计；生成器数据集额外逐项核对 1,100 行的值和顺序。

第二轮全量 566 项通过、13 项排除，剩余一项 `test_fork_does_not_materialize_world` 属于并行进行的检查点分叉实现，已交存储负责人处理。本模块测试均通过。完整日志位于本机 `/tmp/runtime-observation-full-tests-2.log`，最终集成测试应在所有模块修改完成后再执行。

旧路径扫描在 src、docs、README、AGENTS、examples、tests 中进行。当前产品代码已无 SimEngine、StreamingBridge、NodeDiffDispatcher、ReplayLogWriter、JsonlIndexWriter 的实现或导入；原注释中的入口名称已更新为 Society0。剩余文本为 AGENTS 明确记录旧入口已移除，以及测试明确断言旧恢复方法不存在。研究目录保留原调查中的历史证据。
