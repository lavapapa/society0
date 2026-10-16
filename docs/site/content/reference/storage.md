# StageStore 与短事务

`from society0.kernel.storage import StageStore, StageReader, ReadView, Writer` 提供规范 SQL 写入、短快照读取与完整步骤发布。组合运行通常从 `('storage', 'store')` 借用 StageStore。

## 一、创建与访问

存储用当前库处理短事务，以完整描述符确定可恢复身份。

<!-- api: society0.kernel.storage.StageStore.create -->
<!-- api: society0.kernel.storage.StageStore.open -->
<!-- api: society0.kernel.storage.StageStore.transaction -->
<!-- api: society0.kernel.storage.StageStore.read -->
<!-- api: society0.kernel.storage.StageReader -->
<!-- api: society0.kernel.storage.StageReader.read -->

create 在新目录执行冻结 DDL 与同步 initialize，建立初始 root，返回拥有规范写者锁的 StageStore。权威表要求显式非空主键，单列 INTEGER PRIMARY KEY 可用原生规则；外键启用。open 要求 current 与最新完整身份一致，存在未完成事务或 abort 标记时要求 restore。

transaction(callback) 与 read(callback) 返回回调结果。写入回调异常回滚整个短事务；回调返回 awaitable 抛 `TypeError`。写者使用所属线程，禁止嵌套写回调和跨线程转移。规范写入禁止 DDL、ATTACH、修改 PRAGMA 与自行控制事务。

StageReader 可读取生产者未完整的 current；每次短读结束关闭连接。用 `with StageReader(path) as reader` 可明确拥有并复用当前线程连接；关闭后或跨线程复用报 StorageError。StageStore 的 read 复用独立只读连接，嵌套 read 可以取得新快照。

## 二、借用视图

ReadView 与 Writer 都是 borrowed view：有效期严格限于同步回调。

<!-- api: society0.kernel.storage.ReadView.query -->
<!-- api: society0.kernel.storage.ReadView.iter_query -->
<!-- api: society0.kernel.storage.ReadView.read_blob -->
<!-- api: society0.kernel.storage.ReadView.revision_for -->
<!-- api: society0.kernel.storage.Writer.execute -->
<!-- api: society0.kernel.storage.Writer.executemany -->

```python
rows = store.read(lambda view: view.query(
    'SELECT id, amount FROM orders WHERE owner=? LIMIT 20', ('alice',)))
# rows 是独立的结果；网络等待应发生在 read 返回之后。
```

回调返回后视图及未消费的 iter_query 失效；不要返回生成器后继续消费，也不要跨 await 保存数据库事务。query 超过 max_rows 抛 StorageError，行数限制不限制单行字节量。需要流式扫描时在回调内消费 iter_query；原生 read_blob 返回 `(bytes, total_length)`，避免先物化整个 BLOB。

`expected_revision` 核对全库 live_revision；`revision_for(tables)` 在同一快照读取相关表版本，未知表抛 `ValueError`。规范事务同步维护表版本，失败回滚；无匹配行的写语句仍可能推进依赖版本。Writer.publish_step 标识本次事实准备归属，正式完成由描述符决定。

## 三、发布与文件

Runtime 通常负责 complete，插件负责准备并登记自己的依赖。

<!-- api: society0.kernel.storage.StageStore.prepare_artifact -->
<!-- api: society0.kernel.storage.Writer.include_artifact -->
<!-- api: society0.kernel.storage.StageStore.complete -->
<!-- api: society0.kernel.storage.StageStore.abort_step -->

prepare_artifact 消费字节迭代器并返回封存文件的运行内路径；将路径通过 Writer.include_artifact 与业务引用同事务登记，或交给 complete 的 artifacts。仅保存路径到任意业务 JSON 不会自动登记文件依赖。发布后的文件须保持不可变。

complete 要求紧邻上个完整步骤；变化流与工件耐久后原子发布描述符，返回回执。相同最后步骤、相同显式引用且无后续事务可幂等重试，其他重试抛 StorageError。空步骤也有完成身份；只读观察的 complete_step 是已确认下界，可能落后于刚发布的权威描述符。

abort_step 标记需恢复并保留已短事务提交的诊断事实，后续写入与完成被拒绝。它不回滚外部系统副作用。close 释放连接、变化捕获与写者锁，不发布步骤。

Session 捕获保存首次修改行旧值，巨型冷热数据同一行会放大小更新成本；应拆表或使用分块正文。热 complete 随本步净变更与新工件增长，cold open 校验完整链；恢复与导出成本见[生命周期](<storage-lifecycle.md>)。

操作见[存储指南](<../guide/storage.md>)；源码见 [ReadView](<https://github.com/lavapapa/society0/blob/@COMMIT@/src/society0/kernel/storage.py#L90>)。
