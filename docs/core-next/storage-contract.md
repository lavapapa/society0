# 完整步骤存储合同

StageStore 使用 APSW 暴露的 SQLite Session 捕获当前步骤的净行变更。运行中的短事务可及时发布当前状态；可恢复身份由完整步骤描述符决定。该模块处理数据库及显式封存的文件，调用方负责在步骤完成前准备 Thread、记忆和其他组件的恢复数据。

## 一、接口

`StageStore.create(path, schema, initialize=None, run_id=None)` 在新目录创建数据库，执行冻结的 DDL 和同步初始化回调，再用 SQLite backup 建立初始 root。权威表须为普通表并有显式非空主键；单列 INTEGER PRIMARY KEY 的原生规则可直接使用。create、open 和 restore 均启用原生 foreign_keys。恢复使用原生 FKNOACTION apply 标志：级联产生的行变化已包含在 changeset 中，应用时按 NO ACTION 检查最终外键关系，避免再执行一次级联。表和索引定义随运行保存，后续回调禁止 DDL、ATTACH、PRAGMA 修改和自行控制事务。虚拟表等派生索引由后续组件单独管理。

`transaction(fn)` 给同步回调一个 Writer。Writer 提供 `execute(sql, bindings)`、`executemany(sql, bindings)` 和 `query(sql, bindings, max_rows=1000)`。一轮回调对应原生事务，业务异常回滚；返回 awaitable 会被拒绝。callback 结束后借用失效。`Writer.publish_step` 给追加事实提供计划所属完整步骤：初始化回调为 0，普通事务为当前完整步骤加 1，fork 后继续原步号。它表示准备归属，完成发布仍由描述符决定。`read_blob(table, column, rowid, offset=0, size=65536)` 在当前短快照内调用原生增量 BLOB API，返回 bytes 与总长度，避免 SQL substr 物化整条大 BLOB；表必须具有可定位的原生 rowid。`iter_query(sql, bindings)` 提供 callback 内的流式扫描，每次迭代检查借用与线程，callback 返回后迭代器失效。query 超过行数上限显式报错，分页和行内大正文的分块布局由组件定义。行数上限不限制单行字节数，Session 也会保存被修改行的原值，因此巨大正文应作为小块追加行，热点字段和冷正文应分表。

`read(fn, expected_revision=None)` 使用独立只读连接和短读事务。StageStore 保留一个由当前线程拥有的空闲读连接，每次请求重新建立快照，结束时回滚读事务并关闭未消费完的流式游标。嵌套 read 临时打开另一连接，因此外层旧快照、期间规范写入和内层新快照可以同时成立。ReadView 提供有行数上限的 query、`live_revision` 和 `complete_step`。`StageReader(path).read(...)` 提供单次观察能力，可在生产者当前步骤未完成时使用，请求结束关闭连接；外部观察 worker 可用 `with StageReader(path) as reader` 显式拥有并复用该线程的连接。上下文退出或 close 关闭连接，关闭后读取和跨线程复用会报错。调用方不得把读事务跨越异步等待。revision 不符会明确失败，跨页稳定性和继续读取合同由信息提供者实现。原生 WAL 快照允许回调中存在更晚的写入，长回调仍会阻止 WAL 回收。

`complete(step, artifacts=())` 接受紧邻上个完整步骤的整数。Session changeset 先流式写入临时文件、同步文件、改名并同步目录，然后原子写入小型步骤描述符。描述符保存 run 身份、父步骤、live revision、changeset 路径及本次文件引用。空步骤也生成完成身份。相同最后步骤、相同引用且没有后续事务时重试返回原回执；不同输入或已有新事务会拒绝。callback 即使没有 SQL 修改也推进 revision，避免把一次新执行与丢失回执混淆。

`abort_step()` 将当前实例及数据库标为需要恢复，后续写入和完成发布拒绝。它保留已短事务提交的诊断事实。`close()` 关闭捕获和连接；上下文管理器执行 close。规范写入器通过进程文件锁排他，计算线程和其他插件通过同一个规范连接提交操作。平台范围为支持 flock 的 POSIX 系统。

## 二、身份与恢复

`steps/<step>.json` 是唯一完成权威。每个步骤一个不可变描述符，其 parent 形成链；changeset 或文件已写出而描述符尚未发布时，这些文件属于未完成准备物。热 complete 使用实例中最后身份，热 read 从 current 的辅助元数据取得水位，均不扫描历史。cold open 扫描并验证完整链的存在性和身份，成本随恢复链长度增长。

current 中的 `complete_step` 是生产者已确认的完成下界。写入次序为权威描述符发布后再更新该值，因此崩溃可以造成它暂时落后。独立 StageReader 不修复目录，也不把该下界当作最新权威证明。cold writer open 根据描述符校准辅助水位。live_revision 表示当前数据库短事务版本，可大于最后完整身份携带的版本。

`StageStore.open(path)` 允许打开与最后完整身份一致的 current。发现未完成事务版本或 abort 标记会要求 restore。`StageStore.restore(source, destination, step=None, run_id=None)` 和 `StageStore.fork(...)` 在新目录从 root backup 加所选完整链的原生 changeset 重建状态，复制引用文件，再建立新运行 root，赋予新身份和来源记录。它们具有全量 root 文件复制成本，同时将所选完整链压实为新运行的根；准备孤儿通过显式离线 collect_orphans 清理。保留历史所引用的工件继续保留，详见 [存储生命周期](storage-lifecycle-design.md)。运行内热 complete 不执行整库 backup。

恢复前核对 root schema，链中的缺失组件或身份错误明确失败；原生 apply 因缺表忽略变化的行为不能替代这些预检。恢复阶段不读取 dirty current 的业务数据。SQL 内部 revision 等辅助元数据从选中身份重建，不参与 Session 捕获。恢复目标在完整构建之前位于临时目录，失败不发布目标目录。

## 三、文件与故障

`prepare_artifact(chunks)` 消费 bytes 迭代器，产生独占 UUID 文件，完成 fsync 和目录同步后返回 run 内相对路径。它建立与源文件独立的封存副本；迭代器失败会清除临时文件。调用方应把返回引用交给 complete，并且不再修改该文件。准备与引用不会自动纳入其他数据库的事务。

`Writer.include_artifact(reference)` 可在业务事务内登记文件依赖，登记与业务引用同时提交或回滚。内部辅助表按首次登记 revision 索引，complete 合并本步新增依赖与显式 artifacts，去重后写入完整描述符。复用旧依赖无须重扫历史；恢复从所选描述符链建立工件文件并重建辅助表。辅助表服务于准备与观察，完成权威仍是描述符。

complete 也接受 `artifacts/` 内已经耐久且承诺不可变的合作式引用，记录路径和长度。该入口无法阻止外部代码以相同长度覆写文件；文件不可变性是组件合同。通过 prepare_artifact 可避免工作区文件后续修改影响恢复副本。当前没有通用外部数据库快照协调器，也没有 Chroma 自动备份保证。

步骤发布前失败，恢复选取此前完整步骤。发布后进程退出，恢复选取已发布步骤。回执丢失后相同 complete 可幂等重试；文件系统 fsync 报错属于未知耐久结果，实例停止后由冷恢复判定可见完整身份。跨多个短事务的业务失败由调度器调用 abort_step；数据库之外的网络调用和原生 Python 副作用没有自动回滚。

Session 导出流式消除了整个 changeset 的 Python bytes 副本；原生 Session 仍保存首次修改行的旧值，单行输出块也有内存下界。恢复应用净行变化，不以 changeset 重建动作顺序；动作审计或 Thread 顺序必须作为显式有主键的追加事实写入。读、捕获、恢复和压缩的实际组件验收见 [验收清单](TODO.md)及其链接的存储证据。

SQLite 原生应用标志语义见 [Session apply flags](https://www.sqlite.org/session/c_changesetapply_fknoaction.html)。

## 数据相关的分页版本

`ReadView.run_id` 与 `revision_for(tables)` 在当前短读快照内提供运行身份及所声明表的版本。版本是这些表最近写入事务的最大 live_revision，空集合返回 0，未知表名拒绝。信息提供者声明数据、权限和计数所依赖的表，游标同时绑定 run_id 与该版本；Thread 留证等无关写入因而不会使业务分页失效。

规范写入器复用 SQLite authorizer，在每个事务准备 INSERT、UPDATE、DELETE 时收集目标表，再于同一次事务更新辅助版本表。失败回滚同步撤回版本；无匹配行的写语句可以保守地推进版本。每次事务设置和清除 authorizer，也覆盖语句缓存重新授权、外键级联和 WITHOUT ROWID 表。artifact 登记临时退出授权器后恢复本次事务的收集器，后续业务写入继续受跟踪。

辅助版本表用于当前读取身份，不构成恢复权威，也不进入 Session 变化集。restore/fork 创建新 run_id，将新根的 live_revision 及各表版本一起归零，后续从新身份推进。当前格式面向新运行，旧库没有新增辅助表时需重新创建运行；本项目不提供格式迁移。

## 压缩资源

`Writer.write_json_chunks(value, emit)` 在当前同步写入作用域调用 `emit(raw_bytes, compressed_bytes)`。RapidJSON 推送原文字节，每块至多 64 KiB，backports-zstd 生成独立标准帧后立即由规范 writer 写入 SQL。Thread、Memory、ResourceCalls 和 Results 共用此入口。首个 emit 错误后停止副作用，原生遍历返回时保留原异常，使当前事务回滚。

StageStore 的 create、open、restore 使用同步原生编码压缩，调用者无需配置压缩线程或在途队列。Session changeset 流直接写入原生 zstd 文件，恢复通过解压流交给 APSW Changeset.apply；完整描述符仍是唯一恢复权威。格式身份由 storage.py 的 `_manifest` 与根创建入口共同定义，旧产物使用产生它的源码读取。

单次块缓冲之外仍有输入对象、Unicode UTF8 缓存、原生压缩器与 SQLite/Session 的驻留成本。同步编码会占用调用线程，独立观察者通过 WAL 短快照读取当前状态；CPU 和整步延迟的实测范围见 [验收清单](TODO.md)及对应性能工件。

## 离线导出与清理

`restore` 同时生成独立可恢复包和压实后的新 root，保留来源 run_id/step；源删除后目标仍可继续与再次恢复。源完整链重复引用同一工件时按路径建立一次。不可变工件同文件系统用硬链接共享分配，EXDEV 时复制；其他文件错误传播。源目录删除后目标仍有独立文件入口，封存工件不可原地修改。声明长度冲突明确拒绝。SQL 中的累计 Thread、Memory 和业务历史仍保留，因此 current 与 root 的冷数据复制成本继续存在。源 root 的 schema、run_id、根步骤和初始 revision 必须与运行清单一致。

`StageStore.collect_orphans(path)` 显式执行离线准备孤儿清理。它先取得独占 writer 锁，核对 root 身份及全部完整描述符链和引用文件，之后清除 artifacts、changesets 目录中未被任何保留完整身份引用的文件，返回已删除相对路径。活动 writer、缺组件或身份错误会在删除前报错。未完成步骤且没有完整引用的诊断文件属于可清理范围，应在诊断留存完成后调用。其工作量随完整历史增长，属于离线维护；不会进入短事务或 complete 热路径。详细空间边界见 [存储生命周期](storage-lifecycle-design.md)。


## 不可变批次与只读准备

`prepare_artifact_file(build)` 向同步构建器提供独占临时路径，构建器应关闭所有文件和数据库连接后返回。Store 完成文件同步、改名和目录同步，返回已有工件引用。失败构建会清理临时文件，发布后引用事务失败形成离线可回收孤儿。`Datasets` 用此入口封存一个明确导入批次为单份不可变 SQLite 工件，记录目录保存原文字节起点与长度，blocks 主键索引连续共享的标准独立 zstd 帧；范围读取直接定位有限块。在线单值与冷批次共用 ChunkWriter 编码入口，合同见 [不可变批次正文](cold-datasets-design.md)。

`prepare_readonly(source, destination, step=None, run_id=None)` 与 restore 共用完整链物化过程，返回 StageReader，保留 source 身份及所选完整步骤，省去新 root.sqlite。该目录面向完整点观察，拒绝作为 writer 或恢复来源；可继续运行的分支使用 restore。Observation 的完整点视图使用稳定派生 run_id，使跨进程读取保持已有游标合同。准备失败清理目标临时目录；已可见的旧准备视图生命周期继续由 Observation 管理。

研究者也可直接用它保存持久分析目录。关闭返回的 reader 后，目录及其已登记工件继续存在，能够脱离源运行读取 Thread、SQL 权威记忆原文/向量、结果与数据集。此路径读取完整步骤，源运行随后产生的未完成事实不进入该视图。记忆分析无需启动向量检索服务；调用召回则另按 Memory 索引合同处理。

```python
from society0.kernel.storage import StageStore, StageReader
from society0.kernel.results import Results

with StageStore.prepare_readonly(source_run, analysis_directory, step=10):
    pass
with StageReader(analysis_directory) as reader:
    results = Results(reader)
    header = results.phase(10, 0)
    first_page = results.page(header['tables']['facts'])
```

`source_run`、`analysis_directory` 和表名由调用者选择。分析目录的物化成本随选中完整点的状态与依赖工件增长；准备过程完成后，分页读取沿现有索引和正文引用执行。继续仿真使用原完整运行创建恢复分支。

运行中的 SQLite 与 WAL 置于本地文件系统。网络挂载用于完整封存工件的传输和存放；正式运行先建立本地工作目录，再按完整点合同导出。当前接口没有为 FUSE、CephFS 或其他网络挂载提供在线 WAL 运行保证。
