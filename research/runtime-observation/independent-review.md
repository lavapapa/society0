# 实时观察实现的独立交叉审查

本次审查对照 implementation-plan 的读取隔离、固定版本、历史增长和来源身份要求，检查另一位实现者负责的 `observation.py` 与 Society0 运行状态接入。审查者没有参与这两部分的实现；只新增复现测试，没有直接修改被审查产品代码。Thread、记忆共享和数据集底层由本审查者实现，不列入本次独立审查结论。检查点存储在共享压缩块布局稳定后另行交叉审查，结果见后文。

## 一、发现

九项问题均有可执行复现，位于 `tests/primary/test_observation_review.py`。严重性 P1 表示会污染运行结果、阻断主要运行或观察能力；P2 表示具体读取流程错误或违反性能边界。最初失败及后续修复分别记录，避免把当前代码已经修好的问题写成仍然存在。

### 1.1 运行隔离

**P1，运行状态写入错误传播到业务运行。** `Society0._write_runtime_status` 原先直接传播状态文件写盘异常，且 `_write_jsonl` 在写业务事件前调用它。注入单独的状态文件 OSError 后，原本的 tick_completed 事件也未写出，业务运行可因此失败。实现者将观察状态的 OSError 记录为警告，权威事件写入错误继续按原规则处理；独立复测通过。

**P1，大索引事务阻塞状态读取。** `ObservationReader.__init__` 原来使用 SQLite 默认 DELETE 日志模式。后台索引线程的脏页超过缓存后取得排他锁，前台 status 的简单 SELECT 同样阻塞。复现以独立写连接的小缓存模拟正常大事务溢出，状态读取报 database is locked；这补充了原十条记录慢索引测试未覆盖的行为。实现者改为本地 WAL，独立复测通过。

### 1.2 版本和性能

**P2，默认续读重新选择最新检查点。** `state_page` 原先先选择 latest，再检查 cursor。第一页后出现新提交时，仅传上一页 cursor 会报 cursor_mismatch，无法继续原版本。实现者让未显式指定 checkpoint 的续读取 cursor 中的固定身份；独立复测通过。

**P2，固定增量更新扫描同键全部历史。** `sync` 对 scopes 的路径范围 UPDATE 未使用当前记录的部分索引。保持当前数据和本次增量各一条，将已结束的同键 scopes 从 10 增至 3,000，SQLite VM 指令从 487 增至 15,437。测试直接计数指令，避免以机器时延代替访问复杂度。实现者增加当前路径部分索引并在 UPDATE 明确使用，独立复测通过。

**P2，已提交但尚未索引的中间版本误报不存在。** 连续发布两次，新 reader 在尚未同步时查询第一版本，原先得到 checkpoint_not_found；规格要求 index_pending。实现者通过 checkpoint_id 直接定位 manifest 及对应完整 marker，无需扫描全部历史；独立复测通过。

### 1.3 来源

**P2，分叉运行读取祖先数据集使用了错误目录。** 索引会登记祖先 manifest 的 dataset，但原 datasets 表没有来源根，dataset_page/read_content 始终使用当前运行目录。复现发布 source 数据集，再建立引用 source 的 target 根；target 同步后读取登记的数据集报 unable to open database file。实现者保存并使用原始来源目录；独立复测通过。

**P2，来源移动未返回明确依赖错误。** 索引建立后将 source 目录移动，read_content 原先直接抛出 sqlite OperationalError；HTTP 错误处理也没有捕获该异常，用户无法取得规定的 source_missing 结果。实现者补充来源存在性检查，独立复测通过。

**P1，相同路径更换运行后复用旧索引。** index meta 原先仅绑定绝对路径和 branch。将旧运行移到 archive，在原路径创建新 run_id，再打开同一个派生索引，读取器仍接受旧索引。旧 entries 因而进入新根的查询范围，来源引用也指向被替换的目录。实现者将索引身份绑定 run_id，独立复测通过。

## 二、验证

复现命令为 `PYTHONPATH=src <基础仓库 .venv/bin/python> -m pytest tests/primary/test_observation_review.py -q`。各项红灯均已交由模块作者修复，最终复验与新增存储审查结果见后两节。

这些检查覆盖普通默认分页续读、运行状态故障隔离、大索引事务读写并发、当前更新的历史访问量，以及跨运行数据集和目录身份。现有原测试另覆盖索引进程退出后的事务回滚、marker 前可见性、多页内容重组和 HTTP 后台索引。尚未把本机 SQLite 并发结果推广到远端文件系统；派生索引应放本地目录。

本次不把测试通过视为全部性能验收。大量根记录的首次索引与共享压缩块开销由相应基准报告说明；本次未测真实远端文件系统延迟。


## 三、最终布局复验

索引改为 sources/scope_names 整数 ID 与 entry rowid 关联后，审查者重新构造同一热键的 10 与 3,000 个已结束版本，保持当前状态和增量各一条，VM 指令增长仍低于三倍。新增父节点删除重建、同一 epoch 重复赋值与删除测试，逐页读取旧版本和最新版本，分别核对完整值、原有顺序、精确总数与游标终止。该复验直接针对归一化布局，避免沿用旧布局结果。

第九项 P2 为数据集超大记录引用：dataset_page 在底层分页后添加 kind/dataset/run_id，导致声明 512 字节的页实际达到 1,661 字节。独立红灯由实现者修复后复测通过。最终 `test_observation_review.py` 十项全部通过，包含九项回归与一项最终布局语义检验。产品修复作者为 spec，复现与本次复验作者为 threads。

检查点存储另发现 P2：导出 base 分支没有核对来源 checkpoint_id，原路径被另一个运行替换后，restore 已拒绝但 export 仍成功返回不可恢复的包。`test_storage_review.py` 先复现红灯，storage 增加与恢复一致的身份比较后，由 threads 独立复验通过；导出失败也不留下目标包。

Thread、记忆与数据集部分的修复由 threads 实现、storage 独立复验：待提交 epoch 使用共享只读叠加视图，清空 pending 后立即撤销；超大 opened 载荷保持完整按需读取且 append 不重复扫描历史；写者冷恢复 offsets 使用临时文件原子替换，重建中观察者可读取既有完整索引。数据集追加验收还覆盖 checkpoint_every=2，前两步数据集随同一 marker 可见，后两步失败 epoch 数据集均无法由观察 API 读取。此处明确区分独立审查与作者自行测试。


## 四、审查收束

共享块最终布局增加独立复验：两个不同文件的路径 ID 顺序相反，合并后逐条恢复完整大字符串，并比较跨 1 MiB 块边界的 200 字节原文切片；全部相等。pending epoch 分别在第二次暂存和最终 publish 注入失败，确认原 marker 和根状态保持、两个 pending 记忆身份立即不可见、暂存列表与目录清空。存储独立测试共四项通过。观察独立测试十项通过。本审查范围内未解决 P1/P2 为零；性能代价按 stage-comparison 与 query-scale 报告保留。产品由原模块作者修复，以上复验由未参与该模块实现的 threads 执行。


## 五、第二轮角色与复验

2026-09-29 的 T17 索引优化由原审查者 threads 实现，因此本轮查询独立审查改由 storage 负责。storage 发现当前页并发提交混合版本的 P1，threads 修复整页 WAL 读事务后，storage 独立复验通过；类型化键、同 epoch 重建计数与旧 prepared 等价测试亦通过。父替换临时 ID 表、路径字节缓存及准备中进程退出保留旧 ready 已复查，查询范围无剩余产品项。

threads 对未参与实现的 T16 存储优化继续独立审查。新增快/慢 JSON 编码对 Unicode、控制字符、引号、反斜线、整数/浮点/空值/布尔键的逐字节比较；超过 1 MiB 的正文区间读取；空首段、混合 pending/已索引来源与类型化路径的合并、精确总数及超大引用分页。新增两项与原四项共六项通过，本次未发现新增存储缺陷。查询和存储的作者验证与非作者复验分别记录，范围内未解决 P1/P2 为零。


第二轮末尾存储私有构建库关闭重复 journal/sync 后，threads 再次进行非作者审查并新增三项测试。write 和 merge 的实际调用顺序均为 SQLite 连接关闭、文件 fsync、原子替换、目录 fsync；merge 源文件逐字节保持。注入记录目录最终 fsync 失败后，新步骤没有完整 marker，旧 checkpoint_id 与根状态恢复保持。存储交叉审查累计九项通过；优化仅作用于私有临时构建连接，查询 WAL 与记忆库未改动。最终全量记录独立保存为 deterministic-final-20260929.txt，609 项中间结果另存保留。


存储随后按总条数选择 1,024 或 4,096 字节数据库页。threads 新增两组混合页来源测试，分别跨越小页到大页、大页到小页的目标重建路径，逐条比较完整记录、每路径精确总数和来源文件字节。耐久顺序探针区分新增只读源连接与目标构建连接；目标的 close/fsync/replace/fsync 顺序保持。存储独立组累计十一项通过，未新增产品发现。

## 最终恢复与引用复验（2026-09-29）

threads 作为 storage 非作者复验最新身份解析、根 generator 与 discard 热路径。新增独立测试禁止 iter_records 和 _restore_chain，确认 resolve/fork 保留完整组件资格检查且不读 value；损坏或缺失组件、缺失 Thread 的 latest 回到前一可信 marker，显式版本和 fork 拒绝；跨 base 被替换身份同样拒绝。存储独立测试共 13 项通过，结合 records/manager/memory/真实合同负例的复验 66 项通过。原根 metadata 缺省导致 generator len 错误的全量红灯保存在 deterministic-page-policy-red-20260929.txt。

threads 作者修复观察引用中的 Python bool/int 相等问题：共享 canonical JSON 身份比较覆盖 state/thread/dataset cursor、watch、prepared 身份和 dataset 登记引用；checkpoint/dataset range 共用严格整数 sequence 的元数据入口。新 bool sequence 两例先失败后通过；含类型化 cursor 的 compact/review 24 项通过，storage 负责非作者复验。路径段本身由 JSON 编码保留类型，clear 请求无身份参数。

真实测试合同由 threads 非作者复查：成功链 fixture 的 256 token 设置仅改变该测试提供方 profile 与对应 trace 断言，动作预算、回合上限及业务/记忆断言保留；diagnostics 按独立字段断言允许顺序变化。预算耗尽与输出截断两项确定性负例均通过，截断无动作、无成功记忆、仅一次模型调用且明确 incomplete/output_token_limit。真实结果用于功能及阶段绝对耗时，不与旧失败用例作总体提速比值。

最终交叉复验由 storage 执行 storage_review、observation_review、compact，共 37 项通过，证据 independent-review-final-rerun.txt；确认共享身份比较与整数 sequence 修复。最终确定性全量为 632 passed、14 deselected、32.45 秒，证据 deterministic-final-20260929.txt。此报告覆盖的查询与存储独立审查无未解决 P1/P2。两个 204,882 条基准均已正常结束，TemporaryDirectory 大工件已随退出清理。

## 真实产物驱动的末轮复验（2026-09-29）

threads 作者完成 schema 3 直接集合、类型化压缩段字典、阶段内有界缓存、页内单来源 RecordReader 和存在性检查复用；storage 非作者以独立顺序模型覆盖 8 epoch 随机修改、类型化键、父删除重建、同 epoch 重复写入、历史及当前全页，另复验最后缓存失效与来源切换/异常关闭，共 31 项通过。spec 非作者对 HTTP 四连接饱和、503、断开释放、WAL 写事务下独立只读连接以及只读/rebuild 冲突复验三项通过；冲突参数曾触发删除索引的 P2 已修复。作者真实 TCP 子进程测试同时覆盖半截 body 和暂停接收 8 MiB 响应时其他 status 请求保持可用。

threads 作为 storage 非作者新增随机长键、类型化 fence、published/pending 混合字典合并、超大 Unicode 键和范围字节读取，以及最后流式分页的百万 limit、小字节预算、恰好边界、重复 path 多页不丢不重验证。联合最后 records/storage_review 为 45 项通过，证据 storage-last-page-independent.txt。spec 另有 test_storage_v3_independent.py 五项独立验证，包含顺序元数据十倍规模的解压计数和末段 seek；storage 独立复验 summary 单容器分区，全部十项通过。

本次最终确定性全量为 662 passed、14 deselected、35.43 秒，证据 deterministic-final-real-artifact-20260929.txt；此前 632、659 和中间 TDD 红灯保留原文件。覆盖范围内未解决 P1/P2 为零。真实模型测试和最终实际 HTTP 测量由统一验收报告记载。

最后真实 v3 HTTP 组合验收完成：76.911 秒冷索引期间 304 次 status p95 4.832 毫秒、最大 13.212 毫秒；热页 p95 27.72 毫秒。索引先提交可查询水位、再完成 SQLite 自动 checkpoint 的两个时点分别记录，正常服务退出后关闭连接；全程 WAL 峰值和源完整性证据保留于 query-real-v3-http-final.json。没有新增未解决 P1/P2。
