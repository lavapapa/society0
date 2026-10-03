# 完整步骤存储合同

StageStore 使用 APSW 暴露的 SQLite Session 捕获当前步骤的净行变更。运行中的短事务可及时发布当前状态；可恢复身份由完整步骤描述符决定。该模块处理数据库及显式封存的文件，调用方负责在步骤完成前准备 Thread、记忆和其他组件的恢复数据。

## 一、接口

`StageStore.create(path, schema, initialize=None, run_id=None)` 在新目录创建数据库，执行冻结的 DDL 和同步初始化回调，再用 SQLite backup 建立初始 root。权威表须为普通表并有显式非空主键；单列 INTEGER PRIMARY KEY 的原生规则可直接使用。表和索引定义随运行保存，后续回调禁止 DDL、ATTACH、PRAGMA 修改和自行控制事务。虚拟表等派生索引由后续组件单独管理。

`transaction(fn)` 给同步回调一个 Writer。Writer 提供 `execute(sql, bindings)`、`executemany(sql, bindings)` 和 `query(sql, bindings, max_rows=1000)`。一轮回调对应原生事务，业务异常回滚；返回 awaitable 会被拒绝。callback 结束后借用失效。`iter_query(sql, bindings)` 提供 callback 内的流式扫描，每次迭代检查借用与线程，callback 返回后迭代器失效。query 超过行数上限显式报错，分页和行内大正文的分块布局由组件定义。行数上限不限制单行字节数，Session 也会保存被修改行的原值，因此巨大正文应作为小块追加行，热点字段和冷正文应分表。

`read(fn, expected_revision=None)` 使用独立只读连接和短读事务。ReadView 提供有行数上限的 query、`live_revision` 和 `complete_step`。`StageReader(path).read(...)` 提供相同观察能力，可在生产者当前步骤未完成时使用；每次请求关闭读连接，调用方不得把读事务跨越异步等待。revision 不符会明确失败，跨页稳定性和继续读取合同由信息提供者实现。原生 WAL 快照允许回调中存在更晚的写入，长回调仍会阻止 WAL 回收。

`complete(step, artifacts=())` 接受紧邻上个完整步骤的整数。Session changeset 先流式写入临时文件、同步文件、改名并同步目录，然后原子写入小型步骤描述符。描述符保存 run 身份、父步骤、live revision、changeset 路径及本次文件引用。空步骤也生成完成身份。相同最后步骤、相同引用且没有后续事务时重试返回原回执；不同输入或已有新事务会拒绝。callback 即使没有 SQL 修改也推进 revision，避免把一次新执行与丢失回执混淆。

`abort_step()` 将当前实例及数据库标为需要恢复，后续写入和完成发布拒绝。它保留已短事务提交的诊断事实。`close()` 关闭捕获和连接；上下文管理器执行 close。规范写入器通过进程文件锁排他，计算线程和其他插件通过同一个规范连接提交操作。平台范围为支持 flock 的 POSIX 系统。

## 二、身份与恢复

`steps/<step>.json` 是唯一完成权威。每个步骤一个不可变描述符，其 parent 形成链；changeset 或文件已写出而描述符尚未发布时，这些文件属于未完成准备物。热 complete 使用实例中最后身份，热 read 从 current 的辅助元数据取得水位，均不扫描历史。cold open 扫描并验证完整链的存在性和身份，成本随恢复链长度增长。

current 中的 `complete_step` 是生产者已确认的完成下界。写入次序为权威描述符发布后再更新该值，因此崩溃可以造成它暂时落后。独立 StageReader 不修复目录，也不把该下界当作最新权威证明。cold writer open 根据描述符校准辅助水位。live_revision 表示当前数据库短事务版本，可大于最后完整身份携带的版本。

`StageStore.open(path)` 允许打开与最后完整身份一致的 current。发现未完成事务版本或 abort 标记会要求 restore。`StageStore.restore(source, destination, step=None, run_id=None)` 和 `StageStore.fork(...)` 在新目录从 root backup 加所选完整链的原生 changeset 重建状态，复制引用文件，再建立新运行 root，赋予新身份和来源记录。它们具有全量 root 文件复制成本，后续根压实和垃圾回收尚未实现。运行内热 complete 不执行整库 backup。

恢复前核对 root schema，链中的缺失组件或身份错误明确失败；原生 apply 因缺表忽略变化的行为不能替代这些预检。恢复阶段不读取 dirty current 的业务数据。SQL 内部 revision 等辅助元数据从选中身份重建，不参与 Session 捕获。恢复目标在完整构建之前位于临时目录，失败不发布目标目录。

## 三、文件与故障

`prepare_artifact(chunks)` 消费 bytes 迭代器，产生独占 UUID 文件，完成 fsync 和目录同步后返回 run 内相对路径。它建立与源文件独立的封存副本；迭代器失败会清除临时文件。调用方应把返回引用交给 complete，并且不再修改该文件。准备与引用不会自动纳入其他数据库的事务。

complete 也接受 `artifacts/` 内已经耐久且承诺不可变的合作式引用，记录路径和长度。该入口无法阻止外部代码以相同长度覆写文件；文件不可变性是组件合同。通过 prepare_artifact 可避免工作区文件后续修改影响恢复副本。当前没有通用外部数据库快照协调器，也没有 Chroma 自动备份保证。

步骤发布前失败，恢复选取此前完整步骤。发布后进程退出，恢复选取已发布步骤。回执丢失后相同 complete 可幂等重试；文件系统 fsync 报错属于未知耐久结果，实例停止后由冷恢复判定可见完整身份。跨多个短事务的业务失败由调度器调用 abort_step；数据库之外的网络调用和原生 Python 副作用没有自动回滚。

Session 导出流式消除了整个 changeset 的 Python bytes 副本；原生 Session 仍保存首次修改行的旧值，单行输出块也有内存下界。恢复应用净行变化，不以 changeset 重建动作顺序；动作审计或 Thread 顺序必须作为显式有主键的追加事实写入。读、捕获、恢复和压缩的完整量级验收仍需接入实际组件后进行。
