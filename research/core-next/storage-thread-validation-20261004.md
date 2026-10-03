# 2026-10-04 存储与 Thread 验证记录

执行环境为 society0-core-next 独立 `.venv`，Python 3.12.12、APSW 3.53.4.0；全部命令在该工作树运行。存储实现先以不存在模块产生 collection 失败，然后逐组增加边界反例。以下记录属于本轮局部软件验收，完整核心发布由总任务汇总。

初始命令 `.venv/bin/python -m pytest tests/primary/test_kernel_storage.py -q` 的先失败版本使用独立 `/tmp/core-next-session-venv/bin/python`，输出 `kernel-storage-red-20261004.txt`。后续同命令分别留下 `red-boundaries`、`red-reader`、`red-lock`、`red-identity`、`red-scan`、`red-blob`、`red-fk` 和 `red-fk-cascade` 原始失败记录。阶段绿灯另存对应 green 文件；早期阶段输出保留，不能把当时产品状态当作最新冻结结果。

独立审查由 spec 编写 `test_kernel_storage_review.py`，发现跨线程 Writer 借用和 SQL LIKE 通配前缀误排合法业务表两项问题，修复后非作者确认关闭。SQL 信息提供者的独立审查由 storage 编写 `test_kernel_information_sql_review.py`：先后发现大 BLOB 范围物化、时点游标、rowid 大小写遮蔽及索引词典序 OR 扫描；各自红证据保存在 information-sql-review 文件，修改由 spec 负责。

最新存储外键验证命令为 `.venv/bin/python -m pytest tests/primary/test_kernel_storage.py tests/primary/test_kernel_storage_review.py -o addopts='' -q`，输出 `kernel-storage-green-fk-final-20261004.txt`，36 项通过。create/open/restore 均启用 foreign_keys。两种父子表名排序的插入恢复成立；默认原生 apply 对 CASCADE 的重复副作用曾造成恢复失败，使用 SQLite 官方 `SQLITE_CHANGESETAPPLY_FKNOACTION` 后双排序恢复通过。

Thread 先失败命令为 `.venv/bin/python -m pytest tests/primary/test_kernel_threads.py -q`，输出 `kernel-threads-red-20261004.txt`。范围读取增加后同命令先产生 `kernel-threads-red-range-20261004.txt`。绿灯命令 `.venv/bin/python -m pytest tests/primary/test_kernel_threads.py -o addopts='' -q -s` 的输出 `kernel-threads-range-green-20261004.txt` 包含八项通过及 VM 数据：历史 1000 与 10000 时 append 都为 466、tail 都为 98。测试超十 MiB UTF8、引号、反斜杠、整数、极小浮点正文的精确还原和范围读取；64 字节跨边界范围解压两块。

正文磁盘试验命令 `.venv/bin/python research/core-next/thread-size-probe-20261004.py` 的最终结果为 `thread-size-results-range-20261004.json`。每种输入在独立子进程和 TemporaryDirectory 中执行，全部数据逐值恢复相等后删除临时目录。正文为约 11 MiB 重复字符串与固定种子的异质 base64 文本，不能据此外推真实模型流量的压缩比。压缩和写入计时包括分片编码、zlib 和 SQLite，complete 单独计时；RSS 是 macOS ru_maxrss 的历史高水位，其差值不能解释为真实瞬时新增峰值。
