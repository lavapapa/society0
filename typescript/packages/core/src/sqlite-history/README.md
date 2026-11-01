# 本地完整点与独立分叉

本服务将真实SQLite世界、逐事务Session段、不可变正文与外部尝试证据组织为可恢复完整点。数据库执行、迁移与初始化沿用PR04；文件、压缩和备份均由生产服务完成。

## 一、装配

原生Cordis装配顺序为resources/tasks → executionFence/artifacts/database → initializers/sqliteBootstrap → history → attempts。history注册真实bootstrap publisher，attempts绑定稳定lineage authority。各入口公开同一Schemastery Config、metadata和api；literal profile使用声明的service协议。

新run通过sqliteBootstrap.create运行真实initializer及frontier事务，再发布root。包含正文引用的schema需声明[artifactsMigration](../artifacts-local/store.ts)。新lineage由明确的新建initializer调用attempts.initialize()；authority目录位于run目录之外。重新打开缺失authority保持unknown。

## 二、发布

调用方通过bindQuiescence绑定实际业务TaskPromise.settled；空推进同样等待自己的真实TaskPromise。TaskScope.drain用于结束owner，持续运行的checkpoint使用物理settled屏障。

业务事务结束后seal fence，再调用publish(lease, {operationId,id,parentId,expectedRevision,requiredOperations})。发布核对同一只读snapshot的revision/frontier/schema/receipts，封存新增revision段与对象引用，最后原子发布并fsync描述符。返回后由持有者resume(lease)。每个完整点有独立身份，零增量和相同logicalTime均可产生新点。

失响应调用probe(operationId)。published返回已验证依赖的同一完整点；unknown保留诊断并等待核实。描述符rename窗口可由probe完成目录耐久确认。进程退出遗留的publication marker在确认原PID已退出后清理。首次读取扫描有界描述符索引，正常追加更新内存身份索引；发布不确定时失效重扫。

已有数据库打开时保持diagnostic。status为clean后activate核对实际数据库并恢复写接纳。dirty live保留原样，旧完整点通过fork恢复。describe(pointId)负责验证指定旧点及其依赖。

## 三、分叉

fork接受operationId、pointId、targetRunId、targetDirectory，以及可选initialControl:{generation,controlSequence,planId}和migrations。省略控制参数时继承所选点；新profile传入真实新控制身份与声明的迁移。

最新clean点在同一原生只读snapshot内核对revision/frontier/schema/receipts后backup该连接，applyCount为零。旧点或dirty live从根与逐段changeset恢复。目标先在私有目录中通过共享schema authority迁移，再在身份事务中写入fork frontier回执，经recordForkPrepared保存实际prepared事实，最终发布新root。目标重开跳过seed。

probeFork({operationId,targetDirectory})返回prepared及点、not-prepared或unknown。目标尚不存在时返回not-prepared；已有未发布私有目标保持unknown，供诊断。已发布目标的同ID fork重试复用原root。迁移失败保留源与未发布目标。

同盘正文按对象身份硬链接，跨设备有界复制。目标持有自己的路径和保留范围。attempts authority始终共享lineage；独立证据快照及正文随目标保留。pending/unknown的旧operation阻止新增ordinal自动重发。stageEvidence的每个输入yield受实际evidenceChunkBytes只读预算约束，生产者按该值分片。markUnknown(identity,reason,artifacts)先保留partial raw正文，再在同一SQLite事务中合并refs并记unknown。

## 四、资源

默认IO块64KiB、segment 64MiB、root 256MiB、单描述符1MiB、依赖上限10000。Session每事务导出后释放；捕获阈值与SQLite全库64MiB硬限额共同约束原生分配。硬限额覆盖该SQLite库分配器，V8、Buffer、zstd及Node自带SQLite单独计量。

export(pointId)返回标准GNU tar的有界异步流，运行环境需要tar命令。归档包含world root、段、点描述符、对象和所需证据。已有独立证据snapshot可直接导出；实时authority引用由已绑定ledger形成独立快照。close等待worker退出、tar关闭及对象pin释放。

compact增加物化根并保留点身份，失败时原链和既有reader继续有效。清理策略由持有完整点保留集合的调用方决定，unknown期间保留依赖；对象borrow由调用方finally释放。

诊断区分backupBytes（真实数据库分页备份字节）、applyCount（实际changeset应用次数）、rootCopies（发布/物化根数）、对象copy/hardlink与SQLite/RSS峰值。Native状态同时暴露每段capturePeak、Session释放状态及实际schema/session计数。

验收日志与规模、跨设备结果位于[evidence/pr05](../../../../../../evidence/pr05/)。生产能力使用标准SQLite与Node流；恢复身份由分配的ID确定。

计时使用performance.now累计单调wall time，返回timings.{phase}:{count,failed,totalMs}。jsonEncode精确覆盖JSON.stringify与UTF8 Buffer构造；jsonFileIO、jsonRenameIO、jsonDirectoryIO分别覆盖atomic writer已完成的文件写入同步、rename、目录同步，观察钩子等待另计。fileFinalizeIO覆盖段/根封存。

compressPipeline/decompressPipeline包含编解码与管线读写等待；native.*从worker创建计至实际exit；publication/rootPublication/fork/restore/compaction为端到端阶段，export还包含消费者背压。上述阶段存在包含和并发关系，时长分别呈现。压缩纯CPU、流读写拆分与设备IO耗时明确为unknown。原子writer的细阶段记录已完成次数，端到端measure记录全部结算及失败；backupBytes为分页备份逻辑数据字节，文件系统journal和硬件传输另计。


暂存段 `segments/<revision>.changeset` 在所属完整点描述符耐久发布后由 publish/publishRoot 删除；晚于最新点的段属于 live 后缀，close/重开/GC 保留。发布失败遗留的 `history/segments/*.zst`、`*.pending` 与 `history/roots/*.pending` 只由显式 `history.gc()` 清理；gc 在本实例内与 publish/fork/compact/export 互斥并持有发布锁，发布标记 owner 可能存活时拒绝（`HISTORY_GC_UNKNOWN`）。调用前应确保没有其他进程打开该 run 目录。`diagnostics().rawPendingCount/rawPendingBytes` 是最近一次 collectRaw/gc 的快照；`rawCollected/rawBytesCollected/orphanFilesCollected/lastGcError` 为累计/最近错误。`history/staging/` 残留与未引用 root 文件留待后续 retention。

`history.gc()` 命令入口为后续待办；目前由调用方通过服务 API 显式发起清理。
