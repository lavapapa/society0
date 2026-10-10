# 组合机制分项探索试验

本次对照 Next 起点 7320b13 与共享组合工作树，研究固定活动集合扩大历史时的机制成本。全部数字属于探索证据：运行期间 after 为仍有变动的未提交工作树，缺少可确证的冻结源码身份。结果覆盖规则服务、Information 分页及显式呈现；正式性能验收等待明确 source commit 的独立 detached 工作树串行复测。

## 一、结果

history=4 的三个小测通过；隔离进程 before/after 的 RR、social、social_embedding 三模式分别在 4、100、10000 完成精确业务比较。比较保留分数、排序、页面总数、逐项元数据、完整冷正文、实际呈现的完整帖子、曝光计数及恢复后继续动作。主动破坏语义输出的测试会拒绝比较。小测起始红例为模块尚不存在时的 ModuleNotFoundError；实现后发现 RR conversation_view 含存储 revision，恢复的业务校验移除这一非业务修订号，时间由固定 clock 注入。

| 热路径 | 历史 | before → after SQL VM | rank / embedding 调用 | 墙钟毫秒 |
|---|---:|---:|---|---:|
| RR 当前配对与收件箱 | 100 | 254 → 254 | 0/0 → 0/0 | 1.139 → 0.960 |
| RR 当前配对与收件箱 | 10000 | 254 → 254 | 0/0 → 0/0 | 1.071 → 1.175 |
| social A/C/A续/C续 | 100 | 2924 → 2629 | 4/0 → 2/0 | 4.108 → 3.226 |
| social A/C/A续/C续 | 10000 | 2924 → 2629 | 4/0 → 2/0 | 3.311 → 3.792 |
| social_embedding A/C/A续/C续 | 100 | 6807 → 5602 | 4/4 → 2/2 | 9.145 → 6.135 |
| social_embedding A/C/A续/C续 | 10000 | 6807 → 5602 | 4/4 → 2/2 | 172.204 → 91.706 |

无 embedding 的 10000 social 单次墙钟退化，SQL 工作量下降并未保证该次耗时下降。RR 热路径工作量无改善。embedding 模式的确定性 Client.query 会扫描全部历史向量；其历史斜率包含替身 O(N) 成本，不能外推真实向量后端。在此替身下，跨主体缓存减少了重复排序、重复嵌入及重复查询。

冷正文小动作：两侧各模式 SQL 返回正文/Blob 读取均为 0 字节。完整消费者读取 311296 字节原文并逐字节验证。embedding 分页物化正文 622600 → 311300 字节，来自复用同一主体完整偏好输入；实际呈现与曝光保留，原文消费者没有裁剪。

完整点、恢复与关闭均单列。恢复本来包含历史重建，其开销随历史增长；10000 RR 墙钟 125.428 → 151.072ms，无 embedding social 394.411 → 501.997ms，属于本次退化观察。对应 CPU 分别 116.685 → 128.242ms 与 382.623 → 386.105ms，后者墙钟差异受等待/调度影响更大。完整增量磁盘不随历史同比增长：social 本次两规模两侧均 496 字节；RR 100/10000 分别 4492/374 字节，WAL 已分配容量影响目录净增量，不能将其解释为每步实际写盘总字节。

全部阶段 CPU、墙钟、SQL VM、Python 峰值、RSS、磁盘净增量及正文物化见 [探索分项表](<cost-tables.md>)。原始 JSON 与完整点保全在工作区 [100/10000 探索测量目录](</Users/marvin/Documents/同花顺（2）/outputs/society0-next-composition/cost-exploratory/cost-frozen-mechanism/>)，小样本保全在 [history=4 目录](</Users/marvin/Documents/同花顺（2）/outputs/society0-next-composition/cost-exploratory/cost-small/>)。归档保留原目录名，名称中的 frozen 不代表源码身份已获验证。

## 二、口径

每侧由独立进程通过 PYTHONPATH 指定对应 src 与仓库根，解释器统一使用原仓 .venv。每个模式四个主体；RR 增加上一轮历史、保持本轮收件箱固定；social 历史增长，活动推荐配置 recent=4、engagement=2、lifetime=0、full_scan=4 固定。历史通过规范行动写入，初始化耗时独立计量。embedding=False 显式配置；True 使用原有测试 Embed/Client，保留每段完整嵌入文本和原始向量。

分页为只读预览，随后 recommended_feed 进行实际完整呈现并 after_tick 提交曝光。评分、排序和曝光分别对照，未用预览免曝光替代实际呈现。热路径结束后读取全量详情进行核验，核验不计入 hot。

阶段按 create、seed、root_complete、cold_small_action、full_original、hot、presentation、complete、close、restore、continue、continued_complete、restored_close 拆分。complete 直接调用 StageStore.complete，业务与 after_tick 已先收束，属于完整点发布成本；没有把它描述为 Runtime/LLM 全步骤耗时。

SQL VM 通过既有 APSW progress_handler 每指令计数，所有新连接均安装；正文物化统计 SQL 返回名为 body/content 的单元与 ReadView.read_blob 返回字节。此口径衡量 Python 可见物化，未衡量操作系统实际磁盘读取，也不覆盖 SQL 内部解码及其他别名字段。Python 峰值用 tracemalloc，每阶段独立启动；CPU/墙钟包含观测开销。RSS 前后值由 ps 获取，peak 为进程终身 ru_maxrss，包含初始化历史，不能当作阶段增量峰值。SQL VM 回调会显著放大绝对耗时；每组单次试验适合定位成本，稳健速度结论需要冻结后重复与无仪表计时。

恢复使用各自同语义完整状态、新目录与新 SQLite 连接，保持 OS cache，当前实现为同进程重新装配；没有完成进程冷启动恢复性能试验。social 无 embedding 的 after 不安装语义叶服务，省去 social_embedding_pending/social_vectors 两张表，完整 schema 验证不接受旧物理 root。RR 实测 schema 相同；actors.data 拆分未改变 actors DDL。这里没有改 schema、绕过验证或声称全部模式都跨版本不兼容。

## 三、复现

在目标工作树执行小测：

```sh
PYTHONPATH=src:. /Users/marvin/Documents/同花顺（2）/research/simulation/society0core/.venv/bin/python -m pytest tests/experiments/test_core_composition_cost.py -q
```

取得父会话给出的明确 source commit 后，另建该提交的干净 detached 基准工作树，运行 [复现脚本](<cost-run.sh>)。脚本核验两侧提交与干净状态，after 必须 detached；默认输出到 /tmp 下新建独立目录，禁写 pyc。可用首个位置参数指定源码树外的新输出目录。

```sh
AFTER_SOURCE='/绝对路径/只读detached基准工作树' \
AFTER_COMMIT='父会话确认的完整提交号' \
bash research/core-next/composable-mechanisms/cost-run.sh
```

BEFORE_SOURCE 可显式指定干净的 7320b13 基准工作树；解释器沿用原仓 .venv。准备阶段尚未创建正式基准工作树或启动正式复测。真正 LLM 认知呈现成本等待 InputBatch 合同冻结后另测。

每个进程 JSON 记录实际导入源码根、PYTHONPATH、Git HEAD/状态。探索 after 的 HEAD=7320b13 与 dirty 状态无法精确表示运行源码。[运行后源码归档](</Users/marvin/Documents/同花顺（2）/outputs/society0-next-composition/cost-exploratory/cost-source-after.tar.gz>) 含 pyc，保留为诊断资料；事后归档不足以证明运行期间来源稳定。正式验收应在明确提交上复跑，并补无仪表计时、重复次数与独立进程冷恢复。

修改边界为新增基准、专属实验测试、小型报告与数字索引；大型探索证据已整体移动保全至工作区 outputs。未修改 src、其他测试、原主树，未执行 Git add/commit/push。父会话安排的独立审查已核对六组语义与78行数字，源码身份问题使这些数字保持探索级别。

探索观察提示跨主体复用减少重复工作、固定活动集下 SQL 热点不随历史增长；正式结论等待冻结提交复测，恢复成本、真实向量后端与端到端模型成本仍需分别评估。
