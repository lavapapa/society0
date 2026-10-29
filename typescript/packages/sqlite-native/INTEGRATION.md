# 原生层接入

本包提供PR04/05所需的真实原生能力。run语义继续由core/sqlite与sqlite-history负责；本文件列出需主智能体集成的明确事项。

## 一、接口

以[正式声明](src/index.d.ts)为调用合同。包内方法同步执行，连接在一个worker内持有。主机的SqliteTransaction不携带原生指针，worker通过有序消息调用同一连接。

- managed SQL：query、execute、executeMany；execSchema供可信bootstrap/连接管理；checkForeignKeys负责原生外键检查。
- 事务与捕获：startSession→begin→execute→commit→exportSegment；rollback在显式/自动回滚两种SQLite状态下结算，并释放捕获。
- 读视图：独立readOnly连接→beginSnapshot→query/readBlobRange→endSnapshot/close；在建立读视图后查询运行revision。
- 历史：backupTo分页备份，exportSegment/applySegment使用新建暂存文件及有界原生IO。压缩、对象、描述符和发布另由历史服务组合。
- 取消：createInterruptControl创建共享控制字。资源任务Promise以实际SQL结果、中断结果或worker exit结算；AbortSignal监听器设置控制字，清零发生在原操作完全结算以后。

## 二、接入项

1. 根workspace、依赖锁、构建和测试入口增加本包。包内build.py生成Linux x64 N-API工件；根构建调用此脚本。根配置由主维护，本批次未修改。
2. core/sqlite接入writer lease、队列行数/字节、操作序号、总事务时长/语句数、回执和unknown探测。普通参与者调用execute等入口，commit留在worker控制层。
3. Session表清单包括业务恢复必需表、receipt和frontier；TEXT主键须显式NOT NULL。schema安装完成后checkForeignKeys，再开始捕获。派生表的独立恢复声明由owner维护。
4. PR05给每段关联revision区间；commit完成后在下一写入前export。导出失败的dirty连接停止写入。保留失败文件和已提交live用于诊断，按已发布完整点恢复。
5. SqliteError保留code、sqliteCode及sqliteExtendedCode。COMMIT错误由上层进入unknown探测；底层state.autocommit用于诊断，不能替代业务回执。
6. 进程级统一owner设置SQLite硬堆额度；每run另外配置行、捕获、参数、响应与排队预算。多个worker线程共享同一库实例的native硬限额。
7. close在worker所有任务结算之后执行，随后等待worker exit。该包close负责资源释放；上层保留完整点、数据和证据。

## 三、验收

在TypeScript根运行独占消费者：

```bash
node --test --test-concurrency=1 tests/sqlite-native.test.mjs tests/sqlite-native-boundaries.test.mjs
node tests/sqlite-native-memory.mjs 2048
```

生产证据由开发根的sqlite-native专属记录维护，包含TDD红绿、类型消费、native取消和两种增量规模。

已实现的原生能力可以立即接入PR04。run-directory、bootstrap owner编排、operation receipt、dirty探测、文件fsync发布和端到端run恢复仍按各自PR验收；本包验收与PR04整体完成状态分别记录。
