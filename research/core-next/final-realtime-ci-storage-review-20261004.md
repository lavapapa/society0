# 实时性与 CI 独立复验

本轮独立审查 spec 的最终实时性探针与 CI 修订，复跑当前四条大正文和实际 pilot 渲染链。产品、依赖锁及作者文件均未修改，没有模型网络调用。综合性能与 fresh R2 的结论作为范围依据，当前审查没有发现新的阻断。

## 一、实时性

源码实际创建规范 StageStore/ThreadStore，另以 spawn 建立 Observation 进程，先等待观察者 ready 再开始写入。每条正文为固定异质 base64 文本 10485760 字节，四条通过真实 writer、原生编码与压缩、短事务和完整点发布。观察者实际调用 status 和 thread_tail，逐次记录首次取得正文引用的时间；5 ms sleep 是轮询间隔，实际时延来自两进程共同单调时钟。status 与 tail 是两个短请求，报告的组合样本不表示一个跨请求 SQL 快照。

独立复跑四次写入为 68.85、65.79、68.35、71.05 ms，运行事件循环最大间隔 71.10 ms。独立 observer 共 58 组 status+tail，合计响应 p95 为 0.568 ms、最大 1.078 ms。观察过程记录完整 step/prefix/total 从 0/0/1 经新增事件到 0/0/5，再到 1/5/5。写函数返回晚于 SQLite 提交，负的返回至首次可见差值保留原值，口径合理。这些数据支持外部观察在大正文同步写入时继续响应，也明确显示生产者事件循环仍有同步空窗。

observer 完成水位推进后，通过公开 byte range 分段读取四份正文，每份 JSON 解码后的 content 与生成的 10 MiB 原始字符串逐字符相等。writer 高水位为 84.64 MB，observer 全文验证前为 30.26 MB、验证后为 101.99 MB；后段包含主动构建原文、bytearray 和 JSON 全量解析。RSS 为各进程 ru_maxrss 历史高水位，未扣除输入或解释器，也未将两进程峰值之和当作同时刻系统峰值。busy-yield 心跳样本的 p95 受探针调度方式影响，报告采用最大间隔描述实测阻塞，不据此承诺生产尾延迟。

原始结果为 [独立实时测量](final-realtime-storage-independent-20261004.json)，命令与逐值后验在同名 txt；相关四个产品文件与 `2590cf3` 和运行时 HEAD 逐字核对记录于 [身份记录](final-realtime-storage-identity-20261004.json)。作者报告的 108.88 ms 空窗与本次 71.10 ms 属于两次独立小样本，差异符合未做稳定分布验收的口径。当前编码下的响应测量补齐旧 Python 编码阶段 207 ms 数字的版本边界。

## 二、执行链

CI 使用 uv 0.8.12，安装 Python 3.12 与 Rust 1.95.0，以 `uv sync --frozen --all-extras` 消费锁文件。shell extra 包含仓库内的 society0-filesystem 路径依赖，其 native 子目录声明相同 Rust 工具链；Node 22 的依赖由 package-lock 与 npm ci 安装。测试命令明确覆盖 primary、e2e、experiments，并通过环境及 marker 排除真实网络组；sqlite-vec 的隔离可行性依赖明确保留为独立实验。没有用宽泛忽略退出码掩盖失败。

随后 CI 用正式 conversation_pilot 的公开 runner 生成两步工件，经正式 workbench CLI 导出，再将实际 payload 路径传给 npm test。package.json 默认指定 test-concurrency=1，消除两份 Vite 测试共享端口的并发条件。CORE_NEXT_WORKBENCH_PAYLOAD 在渲染步骤设置，因此实际表格/趋势测试不会走缺数据的 skip 分支。

本机独立执行同一 pilot→workbench→默认 npm test，结果为 9 通过、0 跳过，含真实表格与曲线渲染；日志为 [独立 CI 消费者](final-ci-storage-independent-20261004.txt)。uv 同版本的 frozen/all-extras/offline dry-run 核对 96 个包且无需变更，见 [依赖核对](final-ci-lock-storage-independent-20261004.txt)。该检查使用现有本机依赖，Linux GitHub Runner 的干净下载、原生编译与平台执行仍以真实 CI 结果为准。本轮没有重复 fresh R2 的 665 项全量；R2 的 15 项真实排除、实验 59 通过及隔离 vec 三路线另验均在其独立报告明确记录。

## 三、判断

综合报告中真实 1448471 条状态、完整值与顺序、226.54 MB 总目录、小工作集查询、单 entry 边界、当前索引历史工作量、20 步认知组合、32 核及 IPC 的证据，足以支撑已经采用的 Core 存储与计算机制。本轮补齐当前编码的同步阻塞和独立观察响应，持续保留完整原文与完整点水位。

V04 的适用范围仍是已测工作负载及源码身份。Chroma 有效向量驻留、长期 changeset 恢复、增长的认知并发、巨记录整值更新、物理冷盘、长期 WAL 读者以及产业领域迁移，都有各自成本；这轮 4×10 MiB 测量不消除这些边界。本 release 保持 Chroma，sqlite-vec 留作隔离候选。当前专项可冻结，最终发布判断由真实验收、最终源码和 CI 结果共同完成。
