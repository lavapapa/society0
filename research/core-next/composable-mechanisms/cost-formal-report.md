# 固定提交的机制成本验证

本报告记录固定提交的离线分项性能试验，与 [共享工作树探索报告](<cost-report.md>) 分开留证。研究对象为四主体、固定当前收件箱或活动推荐配置下，历史从100增加到10000的成本。正式在此指源码身份已固定；单次插桩测量的速度结论仍受下述统计与后端边界限制。

## 一、来源

| 项目 | 身份 |
|---|---|
| before | `7320b13a7d8d58cbe116f9bbc407224fec05de3c` |
| before 源码 | [原主工作树](</Users/marvin/Documents/同花顺（2）/research/simulation/society0core/>)，运行前后均干净 |
| after | `521bda2ad28a9855ef18e76090c353bda907b4f2` |
| after 源码 | [独立 detached 基准工作树](</tmp/society0-cost-after-521bda2-formal-01/>)，运行前后均干净，保留待父会话复核 |
| 脚本 | after 提交内 [分项基准](</tmp/society0-cost-after-521bda2-formal-01/benchmarks/core_composition_cost.py>) 与 [串行入口](</tmp/society0-cost-after-521bda2-formal-01/research/core-next/composable-mechanisms/cost-run.sh>) |
| 解释器 | [原仓 Python](</Users/marvin/Documents/同花顺（2）/research/simulation/society0core/.venv/bin/python>)，CPython 3.12.12，Clang 21.1.4 |
| 依赖锁 | 两提交 [uv.lock](</tmp/society0-cost-after-521bda2-formal-01/uv.lock>) 的既有 Git blob 均为 `5bf8126914d86074a2c38baf12582124aaf2d634` |
| 依赖声明 | 两提交 [pyproject.toml](</tmp/society0-cost-after-521bda2-formal-01/pyproject.toml>) 的既有 Git blob 均为 `b8540a35c32c8f8324f333b0e0b845a5f5aa405a` |
| 实际主要包 | APSW 3.53.4.0、Pydantic 2.13.4、NumPy 2.5.1、NetworkX 3.6.1、pytest 9.1.1、backports.zstd 1.7.0 |

两侧使用同一个虚拟环境，无安装或升级依赖。以上锁身份记录源码合同；实际包版本另列，不将同一虚拟环境推断为已验证安装内容逐包等于锁文件。`PYTHONDONTWRITEBYTECODE=1`，after 部署未生成 pyc。每个模式/规模/侧分别在独立进程执行，通过 PYTHONPATH 指向该侧 src 与仓库根；模式和规模间串行。

完整原始 JSON、完整点与恢复目录的长期入口为 [完整原始工件归档](</Users/marvin/Documents/同花顺（2）/outputs/society0-next-composition/cost-formal-521bda2/>)。原运行 root [独立正式输出目录](</tmp/society0-cost-formal-521bda2-01/>) 保留，其实际绝对路径为 `/private/tmp/society0-cost-formal-521bda2-01`；源目录存在且自身非 symlink。目标不存在时完整复制，源与归档均为210个文件、76058400字节，复制错误0。raw JSON 保持原样，索引保留原 `raw_comparison` 与 `sources`，另加 `archive_root` 入口。仓库保存本报告、小型数字索引与表格；探索目录及事后源码归档保持原样。

## 二、结果

六组 before/after 业务输出精确比较全部通过，每侧关闭后恢复的业务快照均相等。冻结部署上的三个专属小测通过，包括破坏输出应被比较器拒绝。12份原始进程结果均记录正确源码提交及空 Git 状态。完整78行分项指标见 [正式分项表](<cost-formal-tables.md>)；含全部数值精度、分配磁盘增量、rank/embed计数和来源的 [小型 JSON 索引](<cost-formal-index.json>) 可供机器复核。

| 热路径 | 历史 | SQL VM before→after | rank/embed 次数 | 墙钟 ms | CPU ms |
|---|---:|---:|---|---:|---:|
| RR | 100 | 254→254 | 0/0→0/0 | 1.222→1.067 | 1.204→1.057 |
| RR | 10000 | 254→254 | 0/0→0/0 | 1.057→1.010 | 1.056→1.010 |
| social | 100 | 2924→2629 | 4/0→2/0 | 3.339→3.214 | 3.340→3.208 |
| social | 10000 | 2924→2629 | 4/0→2/0 | 3.768→3.527 | 3.756→3.517 |
| social_embedding | 100 | 6807→5602 | 4/4→2/2 | 8.501→6.285 | 8.485→6.286 |
| social_embedding | 10000 | 6807→5602 | 4/4→2/2 | 232.725→138.135 | 221.335→117.830 |

两个历史规模的 SQL 热点工作量一致。RR 的 VM 工作量无改善；无嵌入 social 的墙钟差距较小，单次测量不足以认定稳定速度收益。嵌入模式跨主体复用减少重复排序、嵌入和替身查询，但替身扫描导致前后都随全部历史向量增长。10000 的实际呈现阶段墙钟 155.017→13.415ms，CPU 134.544→7.270ms；这仍是已预热缓存及假向量后端下的结果。

冷正文小动作两侧三模式均物化0正文；完整原文消费者每次读取并逐字节验证311296字节。嵌入分页物化622600→311300字节；无嵌入分页0→0。实际呈现中，无嵌入正文物化933948→933948，嵌入1245248→933948，曝光和完整正文比较保持相同。

### 2.1 回退与未改善

下表枚举本次全部墙钟上升阶段，具体数值均在78行分项表中；CPU 唯一额外上升项为 social/10000 的 seed（2967.806→2979.710ms）。墙钟下降且 CPU 上升的此项同样计为回退观察。

| 模式/历史 | 墙钟上升阶段 |
|---|---|
| RR/100 | cold_small_action、presentation、complete、close、restore、restored_close |
| RR/10000 | root_complete、cold_small_action、presentation、complete、continued_complete、restored_close |
| social/100 | seed、root_complete、close、continue |
| social/10000 | create、close、continue、continued_complete |
| social_embedding/100 | complete、close、restore、continue、continued_complete |
| social_embedding/10000 | create、seed、root_complete、full_original、complete、close、restore、continue |

较明显的回退包括：embedding/100 恢复40.450→85.423ms（CPU37.695→58.363ms）；embedding/10000 初始化历史4582.732→6425.842ms（CPU3922.341→5076.583ms），完整点3.274→13.662ms（CPU3.182→6.312ms），关闭9.920→12.005ms；social/10000 恢复后完整点2.732→4.870ms。它们有些 SQL 工作量完全相同，单次环境调度与解释器成本尚不能分离，保留原始值而不据此断言算法回归。恢复的历史规模成本未解决：embedding after 从85.423增加到882.764ms，RR after 从28.273增加到117.601ms，social after 从42.771增加到382.519ms。

冷小动作、完整原文读取的 SQL VM 均无改善；RR 除 create/restore 各增加228 VM外，其余阶段两侧VM相同。嵌入模式 complete 的2573 VM与496字节磁盘增量均未改善，种子写入、root_complete、续写的VM也相同。social 完整点两规模两侧净增量均496字节；RR 两规模分别4492/374字节，两侧相同，受WAL容量复用影响。

内存同样没有全面下降。无嵌入 social hot 的Python阶段新分配峰值100时20207→28648、10000时20589→29264字节；embedding create 的Python阶段新分配峰值100时164309→705971、10000时163602→705745字节。RR热路径及多项关闭/恢复阶段也有小幅Python阶段新分配峰值增加，完整表保留这些回退。另一方面，embedding hot 的10000 Python阶段新分配峰值2703926→2191779字节；该阶段 RSS 96780288→85786624字节。RSS包含该进程初始化与导入布局，未将其归因为单个缓存改动。

## 三、边界

每侧都使用同一业务脚本，通过规范写入器生成历史。RR 保留上一轮全部历史，当前轮一条大正文与一条续写；social 固定 `recent_keep_count=4`、`top_engagement_keep_count=2`、`min_lifetime_ticks=0`、`full_scan_until=4`，四主体不随历史增加。无嵌入与启用嵌入分别显式配置；启用嵌入复用测试中的确定性 Embed/Client，全文、原始向量分别保留。假向量 `Client.query` 执行全历史扫描及排序，因此嵌入模式的历史斜率包含替身扫描与排序成本；真实向量后端、真实网络模型与 LLM 认知呈现效果需要另测。

A/C/A续/C续每页一条，先只读预览，再通过 recommended_feed 完整呈现并 after_tick 提交曝光。比较逐项保留评分、顺序、页面总数、元数据、完整冷正文、实际呈现帖子、推荐身份、曝光计数和恢复后继续动作。每侧另核对关闭前与恢复后的业务快照；RR 仅移除存储 revision，注入固定 clock。损坏输出的现有小测验证比较器会报错。

每侧从自己的同语义完整点恢复。无嵌入 social 的 after 省去两张语义表，因此跨版本使用同物理 root 会被完整 schema 验证拒绝；RR 的 DDL 相同。没有修改 schema 或绕过验证。恢复采用同进程新连接与重新装配，保留 OS cache，尚无独立进程冷启动恢复性能证据。

阶段独立记录 create、seed、root_complete、cold_small_action、full_original、hot、presentation、complete、close、restore、continue、continued_complete、restored_close。完整点阶段调用 StageStore.complete，业务与 after_tick 已收束。此指标覆盖机制服务和完整点发布，LLM/Runtime 完整调度步骤另行评估。

CPU/墙钟包含 APSW 每指令 progress_handler 和 tracemalloc 的插桩开销。SQL VM 统计覆盖新连接；正文物化统计 SQL 返回名为 body/content 的单元以及 ReadView.read_blob 的返回字节，未覆盖 SQL 内部解码或操作系统磁盘读取。tracemalloc 每阶段独立计量 Python 新分配峰值；RSS 前后值由 ps 获取，RSS_peak 为进程终身峰值，可能来自 seed。目录逻辑/分配字节为净增量，WAL 复用或关闭回收会产生零值或负值，未表示实际写盘总量。

每个配置单次试验，无置信区间；绝对速度与小幅差异需重复、无仪表测量验证。完整报告如实保留变慢、未变化与改善，不用调用次数减少推断研究信息或效果提升。

### 3.1 独立复审核验

最终性能独立审查已完成，reviewer 已撤销源码身份阻断：12个进程固定 after/source `521bda2` 与 before `7320b13`；六组 semantic 与恢复快照全等，78行、1248个数字一致，全部回退已披露，三个专属小测通过。此处登记已完成的独立复核结果，本次归档未重跑性能或测试。结论限定于离线假向量、单次插桩与保留 OS cache 的热恢复；假向量执行全历史扫描及排序，tracemalloc 记录阶段新分配峰值。真实向量后端、无仪表重复测量及独立进程冷恢复仍待验证。

## 四、复现

保留的部署可按以下命令重新执行；输出目录参数必须使用尚不存在的新路径，省略参数会在 /tmp 自动创建独立目录：

```sh
AFTER_SOURCE='/tmp/society0-cost-after-521bda2-formal-01' \
AFTER_COMMIT='521bda2ad28a9855ef18e76090c353bda907b4f2' \
PYTHONDONTWRITEBYTECODE=1 \
bash /tmp/society0-cost-after-521bda2-formal-01/research/core-next/composable-mechanisms/cost-run.sh \
  /tmp/society0-cost-formal-521bda2-repeat-02
```

入口检查 before 精确提交、两侧干净状态与 after detached 状态，末尾再次检查 after。部署继续保留给父会话核验，未执行提交、推送或合并。
