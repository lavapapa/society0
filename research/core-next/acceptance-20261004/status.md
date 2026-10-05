# Core Next 独立验收记录

本轮由独立 GPT-6.1 Sol 执行全部测试与必要产品修复，根监督边界，另一位 Sol 编写独立消费者并复审产品。最终源码与测试冻结于 2026-10-04T06:59:13Z；测试首、第二帧写入失败参数已计入最后统一执行。工作树 society0-core-next、分支 codex/society0-core-next、基线 0ae6f7dd3a5baac4fbaacc272c500c0cbcb0e4d0，已接受的未提交实现完整保留。未部署、发布或合并。

## 一、前提

本树 .venv 实际导入本树源码，运行锁已移除 pyzstd 和空 datasets extra。最后干净安装为基础、llm、anthropic、google、observe、social、memory、shell 八个 profile；全部重装最终共享帧 wheel，并从 /tmp、移除 PYTHONPATH 后运行真实公开消费者。基础消费者还断言没有 pydantic_ai/pyzstd，执行两步 RunPlan、完整恢复、巨值与 None 的 Dataset 保存恢复。证据为 clean-install-frozen-results.json、clean-consumers-shared-final.json。

Linux 使用登记路线 simcat 的任务目录，未改共享服务。基础和 llm 先成功，all-extra 首次因 Ceph 安装耗时超过 420 秒，迁至本任务 /tmp 后成功。原生构建第一次 crates.io 下载超时，复制此前任务已有 registry cache 后以 Cargo offline 成功构建并安装实际 Linux wheel；工具链复用既有 Rust 1.95 可执行文件，CARGO_HOME/CARGO_TARGET_DIR 独占，未修改 RUSTUP_HOME。之前日志中的 rustup 安装文字来自读取历史 toolchain.log。最终全量真实调用已安装原生桥，另从新基础 wheel 执行 RunPlan/Dataset 消费者；证据为 linux-native-offline-install.txt、linux-linux-final-wheel-consumers.json。

早期一次未指定解释器的测试工具安装误写共享 general 环境；已报告并保留共享包，后续均显式使用目标解释器。一次初始凭据信息窗口过滤不足，带出无关账号和凭据尾片段；未输出完整 key，未持久化该窗口正文，随后精确按 provider 在内存使用，未读取或借用 Codex 应用凭据。一次 Linux 原生安装命令沿用了 pip 默认 wheel cache，写入可再生 wheel 缓存，未安装全局包；后续 pip cache 定位到本任务目录。

## 二、修复

产品回归均先留失败证据再修复：空 changeset 原生流未生成帧，Host/compose 跨任务 TaskGroup 所有权与主动安装取消，兼容 Chat EOF 被 SDK 默认补 stop，embedding SDK 丢 index 与 total usage，SDK 包装网络错误漏重试，Google 两个连接池关闭遗漏，请求选项证据副本漏过滤既有凭据键，发布失败被二次 abort 覆盖，ASGI raw 取消提前归还仍在工作的同步查询额度，查询失败覆盖取消，以及 Rust Result 未传播。

真实大根反证表明 SeekableZstdFile 每个公开请求重建全帧目录，100 页比旧布局慢约 73 倍。profile 的 19.664 秒中 19.343 秒在 seek 表加载，实际范围读取 0.063 秒。按记录独立压缩的过渡方案恢复小读取但空间升至 846 MB；最终复用同一 ChunkWriter 的 64KiB 原生共享帧，SQLite blocks 主键定位，records 保 raw_start/raw_bytes。页内缓存为本次 _open 局部 functools.lru_cache(maxsize=1)，退出清空并关闭连接。最终空间回到旧水平，完整原值、跨块、总数、继续读取、权限与恢复不变。

旧 fixture 迁移承接已批准的 Provider、format 3、Uvicorn、TaskGroup、FTS rank 与当前工件合同，逐项理由及原断言承接见 migration.md。最后 WorkBench 仍精确断言 1000 条完整原值只解压一共享帧、连接仅开一次；真实 HTTP 每请求新 Observation/Results/Datasets 的目标页在 10/5000 条无关历史下都不解压正文，跨界范围恰读两帧。生产者失败与第一/第二帧写入失败均不尾 flush、不登记、不留下工件，旧完整点仍可恢复。

## 三、执行

最后 macOS full-final.txt/xml：838 passed、0 failed、0 skipped、15 real_e2e deselected，44.34 秒，exit=0；其中 primary 763、experiments 75。Linux linux-linux-full-shared-final.txt/xml 同为 838/0/0/15，56.37 秒，exit=0。此前各红灯与过渡绿灯保留原身份，数量不累加。tests/performance 为零测试，tests/reference 为参考资产集合，其实际消费者属于 primary，零 collection 的 exit=5 不当通过。

最后从冻结源码正式 runner CLI 生成新的两步对话 pilot，再用 workbench CLI 导出 pilot-workbench-final.json，Node node-pilot-frozen-chain.txt 为 9 passed、0 skipped；runner/export 均 exit=0，node-build-final.txt 构建成功。MAC 原生 Result 修复后重建重装 wheel，再运行 native-compute-final 31 项；最终两平台统一全量再次覆盖原生消费者。共享 ProcessPool 的实际 GraphEnvironment、取消期间额度保持与 close 排空，FixedStep/PhasedSchedule 公开 RunPlan、离线 Authlib/OIDC/真实 loopback 登录成功和拒绝，多个 provider 真实低层 SDK，Responses/SIWC 各终态、opaque 跨解释器续发，以及 VFS 原文完整 UTF8 重组均在最终测试中通过。

coverage-final.json 为实际分支采集：整个 society0 行覆盖 7080/8077（87.66%），分支 1927/2514（76.65%）；kernel 行 5958/6412（92.92%），分支 1601/2036（78.63%）。独立子进程和 Rust 没有加入 Python coverage，相关消费者已实际执行。78 能力入口与通过用例映射由 capability-review 单独记录，该映射不替代分支覆盖。

## 四、边界

ModelScope 的 Qwen3.5-27B 与 Qwen3-Embedding-8B、官方明确免费的 GLM-4.7-Flash 小规模请求均实际 401。公开 models200不证明请求授权；已登记自托管 models 状态也是401，未继续检索它的凭据。Google key 的账户免费资格未知，未调用。Society0 独立 SIWC 凭据仍缺少，根已请求用户完成正式登录；离线认证通过无法证明订阅资格。15 个真实端点 e2e 未执行，完整真实模型/嵌入/记忆/恢复链与主体决策效果保留缺口。

同源 1,448,471 条完整输入的新旧全部原值相等，空间和小读取成本已复验，完整流程 134.10 秒与旧 133.70 秒接近，不作整体加速结论。20 个活动不变的热步骤 Session 固定 2416B，首末五步均值 1.261/1.009ms，详见 performance.md。当前库实际 11,407 物理行，超预算上限 907 行，详见 code-scale.md。全部结果限定在已执行的源码、安装与消费者范围，真实服务和效果仍需补验。

任务清理已完成：本机删除可再生 native target（约663MiB）与早期九profile安装目录（约1GiB），保留日志；远端删除被否决与过渡的大根、试验库及任务target，保留最终共享根、原始输入位置、任务隔离解释器及小结果。具体删除与留存路径见 cleanup-local.json、cleanup-linux.json，未改既有真实输入、工具链或共享环境包。
