# 固定双源成本结果

本记录比较 before=`e57707ca637ee4e5d8b97161b04c9ae70e824a41` 与 after=`b4487cde40d40aee0e8c6484a8d0be43efa9c77e` 的纯机制成本。三种模式、两种历史规模各侧固定五样本，顺序交替执行 before/after；另行采集仪表工作量和独立解释器的冷恢复。所有双源业务语义、评分、顺序、曝光、原文与恢复快照逐字段相等。

## 一、测量

[入口](../../../benchmarks/core_composition_cost.py) 同时提供 instrumented、timing 和 resume_probe，运行脚本见 [本次固定采样](</Users/marvin/Documents/同花顺（2）/outputs/society0-next-composition/cost-b4487cd.py>)。共 12 个仪表运行、60 个无仪表样本、60 个独立进程冷恢复；没有增加样本。源码在测量前后均为对应固定 HEAD 且工作树干净。重现实验时，从对应两个提交建立干净的 detached 工作树，按实际位置设置脚本 SOURCES；每侧 PYTHONPATH 指向该侧 src 与根目录，两侧共用 after 的固定基准脚本，每个样本使用全新输出目录。

[样本索引](</Users/marvin/Documents/同花顺（2）/outputs/society0-next-composition/cost-b4487cd/sample-index.json>) 保留所有原始 JSON 与日志路径；[已提交完整统计](evidence/t13-cost-summary.json) 保留每阶段 wall/CPU、RSS、文件逻辑／分配增量的五次原值、median、p95、范围与标准差。p95 使用 nearest-rank，五样本时为最大值。OS 文件缓存保留；RSS 峰值为整个解释器生命周期，含 Python 与 native 分配，无法单独归因于某阶段。纯计时没有 SQL progress handler、row trace、blob patch 或 tracemalloc；未采集字段为 null。

编码阶段测量业务结果 JSON 编码；规范写入器与完整点内部编码仍计入对应阶段。SQL 正文字节计数覆盖命名 body/content 单元格和 blob 读取，不涵盖所有规范帧解码；完整原文范围读取另外逐字节断言、计时并报告实际 311296 字节。无原文裁剪或替换。

## 二、结果

下表为 wall 中位数，单位 ms。hot restore 为同进程新连接；cold restore 为独立解释器首次恢复。

| 模式 | 历史 | hot before→after | hot restore before→after | cold restore before→after |
|---|---:|---:|---:|---:|
| rr | 100 | 0.499→0.544 | 10.695→10.324 | 11.719→12.373 |
| rr | 10000 | 0.571→0.494 | 26.939→26.278 | 26.295→27.035 |
| social | 100 | 1.320→1.120 | 13.283→12.592 | 13.968→14.329 |
| social | 10000 | 1.543→1.198 | 69.181→66.524 | 69.961→67.794 |
| social_embedding | 100 | 2.388→2.202 | 14.405→14.468 | 15.690→15.059 |
| social_embedding | 10000 | 12.104→11.343 | 82.557→89.306 | 96.228→101.235 |

100→10000 历史时，rr 的 small action/hot/complete/continue SQL VM 分别固定为 432/254/1411/410；普通 social 分别固定为 1246、before 2629 / after 2441、2465、1382。semantic 模式 small action=1246、hot=before 5602 / after 5108、complete=2573 均固定。social hot rank=2、semantic hot embed=2 两侧相同，属于已有基线能力。

semantic 的首次 continue SQL VM 从 2312 增长到 82682，两侧一致。该阶段在新资源中首次重建向量派生索引：[Semantic._sync_vectors](../../../src/society0/plugins/social_semantic.py) 从 through=0 读取已持久化向量。归入冷启动重建，后续稳态热步骤增长尚无本次数据。semantic 的 hot 墙钟随历史增长，与 [确定性向量资源 Collection.query](../../../tests/primary/test_kernel_memory.py) 枚举全部 Python rows 有关；本实验没有据此推断真实 Chroma 的查询斜率。

原文物化的 Python 仪表峰值约 2.495 MB，两侧保持一致。历史 10000 的整进程峰值 RSS（MiB）为 rr 60.28→59.55、social 66.06→66.25、semantic 79.50→79.84。semantic 大历史 hot restore 与 cold restore 均有时长回退，普通 social 和 semantic 的进程峰值也有小幅回退；全部保留在统计中。

## 三、判定

纯机制的原文、业务语义和可信恢复通过；固定活动量的 SQL 热查询与完整点没有新增随历史线性增长的工作量。social SQL hot 工作量减少 188 VM steps，semantic 减少 494；rank/embed 调用次数保持基线值。墙钟分布有快有慢，本次不作全面性能提升结论。

含 Agent 的证据来自 [真实生成／记忆阶段时长](</Users/marvin/Documents/同花顺（2）/outputs/society0-next-composition/acceptance-0f62933/real-memory-timing-jobs.json>)：完整工具、自然结束、原 Thread、自动提取、真实 BGE 嵌入、主动记忆操作及跨进程恢复均保留。该项是 after 的真实功能与等待口径；本次未做跨版本真实模型性能分布比较，无法推断模型等待收益。GPU 备用嵌入服务尚未因管理路线恢复而部署，当前可用服务为获授权 SiliconFlow BGE-M3。
