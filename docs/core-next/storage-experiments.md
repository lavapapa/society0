# 小型存储试验规格与结果

本文保留选型阶段的试验条件、测量和当时结论。文中的“当前”“候选”“尚未实现”均指该次试验时点；现行接口与装配方式见[存储合同](storage-contract.md)、[存储生命周期](storage-lifecycle-design.md)与[不可变批次正文](cold-datasets-design.md)。阶段状态与后续验收由 [TODO](TODO.md) 记录。

本试验服务于 Core Next 的存储决策，使用标准库 SQLite、JSONL 和 gzip 验证最小机制。它包含固定领域表与有限查询，尚未成为正式持久化接口。现有结果支持活动索引与追加事实共存，也显示长读版本、跨步骤恢复和压缩内存需要分别预算。源码为 `benchmarks/core_next_storage.py`，规格测试为 `tests/experiments/test_core_next_storage.py`；正式 src 未修改。

## 一、合同

SQLite 保存当前记录、活动索引、按 owner 的精确计数和完整提交记录。JSONL 按本次新增事实生成独立段，文件 flush/fsync 后改名并同步目录，再将段引用与当前记录写入同一 SQL 事务。SQL COMMIT 是本小试验的唯一完成点，失败段作为未引用工件保留。这里的“完整”覆盖试验内的 SQL 和 JSONL，不包括 Thread、Chroma、自定义组件或远端介质。

只读 view 在首次取得 revision 时开启读事务，以 owner 和记录 ID 索引分页，cursor 绑定数据库路径身份、owner、revision 和最后一个键。每页最多读取 limit+1 条记录，total 由规范写入处维护。路径身份用于本地临时试验，尚不构成迁移、分叉或持久公共引用；未实现权限变更、任意类型键与通用查询语言。

stage 为可完整发布的单元。内部 savepoint 与外层事务使用 SQLite 原生机制；本试验没有自制 WAL、undo 或业务多版本表。活动成员变化同时维护 partial index 与 owner 计数，失败一起回滚。

## 二、方法

测试先于试验实现。初始红灯因模块缺失无法收集；第二轮三项红灯暴露跨库 cursor 误用、活动成员更新缺失及 after_commit 未注入；第三轮补原生事务与长 reader 反例；第四轮先失败增加独立子进程退出注入。最终 13 项通过。原始红绿日志见 [research/core-next](../../research/core-next/)，详细命令记录见 [storage-commands-20261004.txt](../../research/core-next/storage-commands-20261004.txt)。

历史规模为 1,000、10,000、100,000，活动记录固定 64，两个 owner 各 32。seed 同时生成 SQL 记录与历史 JSONL，不保存 Python 全量列表。活动请求返回 16 条；测量每次修改 8 条记录及 8 条新增事实。查询和 stage 的 SQLite VM 指令数通过 progress handler 每条计数取得，计数试验与计时分开，避免 Python callback 扭曲计时。VM 指令数加 EXPLAIN 计划用于判断扫描增长，未测 SQLite 原生 scanstatus 的实际页访问数。

每个规模在独立进程运行，随后测 100 次新 view 查询及 20 次真实不同值的 stage。压缩使用独立准备的 32 MiB 异质结构化文本块，每块 1 MiB；编码与生成在计时之外，读取输入文件、线程创建关闭和 gzip 压缩在计时内。0、2、4、8 worker 各跑三次，交替配置顺序，FIFO 取结果。另一趟相同调度逐块解压并比较原 bytes，验证 32 块全部相等；计时输出未持久保存。

环境为 macOS 26.6.2 arm64，12 逻辑 CPU，CPython 3.12.12、SQLite 3.50.4，本机临时目录。SQLite WAL、synchronous=FULL、连接页缓存建议 2 MiB，自动 checkpoint 关闭以观察 WAL；不采用并发 writer/checkpointer。峰值 RSS 为各子进程 ru_maxrss，统一换算 bytes。此处 file fsync 与 SQLite FULL 的小文件延迟不能代表 Ceph、断电硬件履约或大批量同步成本；macOS 未另开 fullfsync。

## 三、结果

完整原始数字见 [storage-experiment-results-20261004.json](../../research/core-next/storage-experiment-results-20261004.json)。同一固定活动请求在三个规模均执行 157 条 SQLite VM 指令，stage 均为 178 条，修改 9 行（8 条值加完成记录），新增 JSONL 均为 152 字节。查询计划为 active_owner 上的 owner/id 范围 SEARCH，未走历史全表扫描。

历史从 1,000 扩到 100,000 时，100 次分页查询总墙时分别约 11.25、12.31、9.25 毫秒；20 次不同值提交总墙时约 5.49、5.40、4.41 毫秒，总 CPU 约 5.00、4.81、3.92 毫秒。没有单调增长证据，样本和负载很小，不作生产吞吐承诺。进程峰值分别约 25.12、25.33、27.31 MB，包含初始化、SQLite、Python 与计量。

数据库主文件分别为 40,960、188,416、1,781,760 字节，历史 JSONL 为 39,780、417,780、4,377,780 字节。一次计数 stage 加 20 次计时 stage 的新增事实共 3,512 字节；WAL 均为 173,072 字节，SHM 为 32,768 字节。这表明历史本身按内容增长，当前活动读写与本次事实输出可以保持固定工作量。这里没有生成全库 backup，也没有测任意历史 checkpoint 重建。

压缩输出所有配置均为 14,308,760 字节。串行三次中位墙时 286.94 毫秒、CPU 284.91 毫秒、峰值 31.59 MB；2 worker 为 141.24、285.33 毫秒与 42.17 MB；4 worker 为 72.75、290.18 毫秒与 45.86 MB；8 worker 为 43.05、301.03 毫秒与 49.53 MB。8 worker 的纯压缩流水线墙时约为串行的 1/6.67，总 CPU 增加约 5.7%。结果没有包含输出写盘、SQL、JSON 编码或正式状态遍历，不能外推整步加速。

待处理队列限制为 8 个 1 MiB 输入块；8 MiB 是队列中的原始输入预算。实际驻留还包含已弹出但仍由生成器或消费者持有的 raw、已完成压缩输出、线程与 zlib 工作内存以及解释器。结果中的 peak_above_prior_highwater 是两个历史高水位之差，不能解释为精确新增内存；绝对 RSS 是更直接的本机证据。

## 四、边界

事务对照先在一个外层事务内执行 20 个 savepoint 修改：writer 看到 20，另一连接仍看到已提交的 0，ROLLBACK 后恢复 0。随后构造故意不完整的分阶段提交反例：把 current 写为 21 并 COMMIT，complete 仍标 0，旧值已不在 current。这个结果直接反驳“延迟推进恢复水位即可保留旧状态”；该反例临时库随试验结束销毁，不能作为合法运行库使用。

一份固定 reader 在 20 次提交期间继续读旧值。PASSIVE checkpoint 返回 41 个 WAL 帧、仅 1 帧可归并，WAL 达 168,952 字节；释放 reader 后 TRUNCATE 将 WAL 归零。固定分页的代价由真实旧读事务承担。长期 HTTP cursor、无限浏览会话不能照搬这个短 scope；应明确过期、准备有限查询结果或使用实际保留的版本。

故障测试验证 after_update、after_file、before_commit 异常回到上一 SQL/JSONL 完成身份；after_file、before_commit、after_commit 还通过子进程 os._exit 后重新打开验证。after_commit 回执失败保留新完成身份，不能称为回滚。未覆盖磁盘满、系统断电、硬件故障、Ceph、损坏修复、任意历史恢复、fork、Thread/Memory 配对或多写者。未引用文件保留只是失败证据，本试验不实现 GC。

## 五、决策

优先进入原型的是“完整发布单元一个原生事务，局部修改用 savepoint，外部不可变事实段先准备”。它减少自制回滚和版本索引，代价是发布前外部 reader 看不到当前事务，以及长单元占有写事务。将一个 stage 命名为完整 tick 还需要 Thread/Memory 的真实准备合同，本结果没有完成这项证明。

若 PRD 要求步骤内交互发布，并允许失败后回到较早完整步骤，需要真实历史版本或独立恢复基底。原生数据库当前事务无法替代长期历史保存。应另做阶段提交加最小版本方案与固定基底的对照，再决定采用范围；本试验不会为了出一个漂亮结果暗中增加自制 MVCC、每次整库备份或无限长读锁。

线程压缩值得作为独立优化继续测，初期可比较 4 与 8 worker 在真实编码及写盘流水线中的收益。活动查询则应先保证按当前范围定位、规范写入维护索引和精确计数，再讨论缓存。上述选择保留小 dict 与 JSONL 的合法用途，并为 PRD 提供可测边界，而非预先锁定完整存储架构。

## 六、原生 Session 候选

追加试验在 `/tmp/core-next-session-venv` 独立环境安装 APSW 3.53.4.0 与 pytest 9.1.1，没有改变项目依赖或锁文件。该 APSW 链接 SQLite 3.53.4，实测编译包含 ENABLE_SESSION 与 ENABLE_PREUPDATE_HOOK，提供 Session、changeset_stream 和流式 Changeset.apply。[APSW 官方接口](https://rogerbinns.github.io/apsw/session.html)说明原生 changeset 记录净行变化，不保证业务发生顺序，不携带 schema 创建操作。按业务序号保存追加行才能保留 Thread/事实顺序。

### 6.1 可执行恢复边界

原型先通过原生 backup 生成一次 root 数据库，Session 附着唯一 writer 连接，连续两个短事务分别更新 value 为 1 和 2；streaming changeset 完成文件耐久后发布完整步 1。下一 Session 将 current 提交到 3，在导出前、导出后及完整 marker 后分别使子进程直接退出。前两种退出留下 current=3、完整步=1，从 root 与完整链原生 apply 得到 2；marker 后退出得到完整步=2，恢复为 3。三个结果均由重新打开数据库及真实 native apply 验证。未完成 current 被放弃，未实现逆向撤销日志。

新进程重新创建 Session 得到空 changeset，证明捕获状态本身存在进程内。恢复依赖已经固定的 root、schema 和完整 changeset 链；不能仅重新打开前进 current 就认为恢复成功。独立连接更新同一库不会被所附 Session 捕获；因此规范插件写入必须经过单一权威 writer 连接，在同步调用边界协调。多个插件可共享该写者，线程数或插件数不对应数据库 writer 数。

测试还确认目标表缺失时 native apply 会忽略该表变化。正式恢复必须先核对冻结 schema、主键与表参与范围，不能用“apply 未报错”判断恢复正确。该试验核对全部行相等；没有实现 schema 迁移、分叉身份、链清理或 root 压实。Thread/Memory 等外部资源的完整准备仍属于尚未验收的组合合同。

### 6.2 捕获与流式下界

负载固定 16 个初始记录，比较 1 KiB 与 1 MiB 正文，实际触达 1 或 8 条记录，两次短事务后再修改另一条并 rollback。四种布局分别是余额和正文同一行的不同列、正文拆到 detail 冷表、余额嵌在巨大 JSON 单元格、每次追加新的正文行。每种组合独立进程测量，输出 CPU、墙时、RSS、Session 自报捕获内存、changeset 字节、流式回调最大块、backup 与 apply 时间和文件尺寸。原始结果见 [storage-session-results-20261004.json](../../research/core-next/storage-session-results-20261004.json)。

1 MiB 正文、8 条热点时，同一宽行的净 changeset 仅 269 字节，捕获内存却达 9,586,896 字节；正文拆表后 changeset 252 字节、捕获内存 2,800 字节。计数包含那条被 rollback 的旧行；rollback 消除最终净变化，并不承诺当场释放 Session 已记录的原始值。这个差异符合 SQLite 捕获 UPDATE/DELETE 原行值、导出时计算净变化的机制。[SQLite Session 原理](https://www.sqlite.org/sessionintro.html#changeset_construction)。

余额嵌入大 JSON 时，8 条更新产生 16,777,780 字节 changeset；单个 streaming 输出回调达到 2,097,233 字节，包含单行旧新正文。16 条新增 1 MiB 正文输出 16,777,468 字节，单次回调最大 1,048,603 字节。streaming 避免整个 changeset 形成一个 Python bytes，但捕获旧行和单个大行仍有内存下界。append 测量的约 1.07 MB 捕获内存主要来自故意修改后 rollback 的旧正文行；新增行的后镜像在导出时从数据库读取，不能把这一数字解释为任意 append 规模的总内存保证。

### 6.3 选择意义

原生 Session 值得进入下一轮候选：日常 current 通过短事务实时发布，完整恢复由一次 root 加已完成步骤 changesets 组成。它复用成熟行变更捕获与应用，避免手写 diff/undo，并允许每个步骤关闭旧 Session 释放捕获内存。步骤内部观察版本与完整恢复身份必须同时标明。changeset 导出期间需要固定相关 writer 视图，不能边导出边继续同一捕获范围的业务修改。

其主要代价来自宽行捕获、巨大字段旧新值输出、root 复制以及链重放。恢复与 fork 仍需要复制 root 并顺序应用完整链；此次记录了 root backup 成本，尚未证明长期链增长可接受。原型不默认每步整库 backup，也不利用长期 reader 假装永久 revision。后续应先把热点标量与巨大正文分离，用显式追加行表达 Thread，再测真实步骤捕获集；是否需要分段 Session 或周期性新 root，按峰值和恢复预算决定。

APSW 环境最终 20 项通过，原项目环境为 13 项通过、7 项因未安装可选 APSW 而显式跳过。全部 Session 测试已经在独立环境执行，跳过代表依赖隔离。临时环境没有 pytest-asyncio，会提示仓库 asyncio_mode 选项未知，这些试验无异步测试。此候选尚未纳入产品依赖、正式恢复或性能验收。

### 6.4 StageStore 契约草案

以下名称用于 PRD/SDD 讨论，尚未新增产品类。`initialize(path, schema)` 接受冻结的显式表、主键、索引和类型合同，创建 current 与唯一初始 root；`writer_transaction(fn)` 在权威 writer 连接执行同步回调，原生事务负责成功提交或失败撤销；`read_page(scope, query, cursor)` 在短只读事务中返回 live revision、最近完整恢复身份和结果，离开调用即释放读事务；`complete(step, prepared_refs)` 封存从上一完成边界开始的 Session；`restore(root, chain, destination)` 在新目标重建完整数据库；`fork(complete_identity, destination, new_run_id)` 基于同一恢复过程建立新运行清单。插件共享 writer，计算线程只接不可变输入和返回值，不接收写连接。

writer_transaction 成功可推进 live revision，失败不发布本次 SQL 变更。Session 跨这些短事务存在，完整步骤失败后禁止继续当前分支，从最后完整链重建。调用方不得在回调内自行 COMMIT、DDL 或切换线程；当前草案把插件视作合作代码，不设计 SQL 解析防火墙。回调中允许原生 savepoint 的必要用法由核心统一控制。一次失败已经取消整个外层事务时，不按普通可恢复业务拒绝继续运行。

read_page 的 cursor 包含 run、scope 和 live revision。两次请求之间 current 已改变时，明确返回版本过期；保持固定跨页内容需要调用方选定完整恢复版本并使用独立查询副本或有限准备结果。当前查询接口不暗中建立历史版本表，也不为长期 cursor 持有 WAL reader。每个 owner 的资格谓词和可见计数必须进入实际查询，不能只在返回阶段过滤。

complete 先停止该步骤 writer，并确认所需外部引用已固定。把 Session 流式写入唯一临时 changeset 文件，flush/fsync，完成命名并同步目录；随后原子发布小型完成描述符，描述 root 身份、schema 版本、本步骤 changeset、父完成身份及外部引用。该描述符成为长期恢复的唯一完成权威，current 中的辅助水位从它派生或核对，不作为第二个相互独立的完成权威。描述符发布成功后才关闭旧 Session 并开始下一步捕获。回执丢失时依据已有完成身份处理，禁止再次执行业务动作。

root 同样先完成原生 backup，在固定业务边界暂停 writer 或采用已验证固定读取条件，关闭目标、同步文件与目录后再发布 root 描述符。首次创建 root 可要求没有正在执行的动作。后续周期性新 root、链压实及 GC 尚未确定，不能据此删掉仍被 fork 或恢复引用的旧工件。

restore 读取已发布完成链并核对顺序、父身份及冻结 schema，原生复制 root 到新的临时数据库，再顺序 stream apply 完整 changesets。默认冲突策略为中止，不能静默 skip/replace；全部成功且目标关闭同步后才发布新运行描述符。缺表风险通过在打开 writer 和恢复前比对冻结 schema 与实际数据库的表、列、显式主键、索引等定义处理，Session 只附着已声明表，运行中禁止未声明 DDL。无需新增哈希。原生 apply 不负责 schema 管理，应用不能把“无异常”当作 schema 等价证据。

fork 建立新 run/branch 身份并记录来源完整身份，可以复用不可变 root/chain 引用但必须有明确保留策略；运行中的 current 需要自己的可写库。首个实现可直接复制 root 并重放链，记录读写量和恢复时间，不宣称零复制分支。上述单库原生机制没有解决独立 Chroma 的耐久准备、线程调用副作用或远端存储故障，这些仍需组合验收。

### 6.5 语言与正文边界

这些试验支持先保留 Python 的领域与组合层，由 APSW 把事务、索引、Session 捕获、stream apply 交给原生 SQLite。当前内存差异主要由表布局决定：同样 Python 层调用，正文拆表令 Session 捕获从约 9.6 MB 降为 2.8 KB。将整个库改写成 Rust 不会自动消除原生 Session 保存原行的成本；引入第二种语言应等待明确热点与接口收益证据。

Python/native 边界仍需测量：绑定大 TEXT/BLOB、执行 JSON 编码、返回行转换、streaming 回调交接以及 apply 对单行的物化。此次 streaming 大 JSON 回调已出现约 2 MiB 单块，证明使用流式 API 仍不能保证任意单值都满足固定小缓冲。Thread 每条消息单独插入可以避免重写整段历史，但一条正文的大小仍决定绑定、SQLite 行处理及输出回调的下界；源 Python 字符串、native 存储与输出 bytes 的生命周期可能重叠。若需要更严的上限，应测明确正文分块行及稳定顺序，而非先承诺所有消息零复制。

后续语言判断以逐阶段 CPU、GIL 占用、每条 SQL/回调次数、边界复制字节和进程峰值为依据。优先批量 SQL、小记录布局、有界线程和原生现成能力；只有仍占主导的纯 Python 热点才比较局部原生实现，避免尚未证明问题来源就重写整库。
