# SQLite原生驱动规格

本包提供worker内同步SQLite专属连接；JS宿主的租约、RPC排队、回执与完整点发布由上层接入。单个连接统一承担SQL/statement/Session/BLOB。支持Linux x64与已固定SQLite3.49.1，跨平台需独立构建验证。

1. SQL只接收绑定参数，单statement；query限行与字节，整型bigint、浮点number、TEXT完整UTF8、BLOB独立副本。领域execute/query通过authorizer阻止连接控制及DDL。
2. 显式begin/commit/rollback，失败事务结算后再接受写入。短事务复用同一statement批写，输入行数与字节在派发前限制。
3. Session显式表清单经过schema验证并按schema生命周期缓存。每段一次短事务，commit后阻止下一写事务直到导出完成。失败/OOM Session退役；导出失败的已提交状态标dirty，停止后续写入。
4. changeset_strm/apply_v2_strm直接使用有界文件IO；输出原生块和实际IO块分别统计。恢复临时禁用触发器，使用FKNOACTION、ABORT冲突和原子savepoint，返回前恢复触发器。原生内联长度限制与全局hard heap预算分别管理。
5. BLOB使用原生范围句柄；snapshot在独立只读连接BEGIN并读取schema以固定视图，有期限，结束后拒绝继续读取。备份使用分页backup API。
6. 共享Int32Array控制字由宿主Atomics.store置1；C progress及流回调直接原子读取。原生操作结束后才能复位和回收任务容量。SQL执行时设置期限与捕获阈值；硬堆上限全库实例共享。
7. close同步释放所有Session/statement/blob和连接，可重复。关闭句柄后方法抛明确错误。最后一次调用的SQLite失败码与连接状态可诊断。

验收先使用真实消费者失败，再完成N-API与包边界。消费者验证参数/约束、联合回滚、索引JOIN、分段恢复、触发器/外键、BLOB快照、OOM退役、跨线程取消和native内存曲线。范围限本包及独占测试与证据目录。
