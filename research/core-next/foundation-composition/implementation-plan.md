# 基础组合改造实施记录

本文件记录 Society0 Next 基础组合任务的执行顺序与状态。规格入口为[交接包](</Users/marvin/Documents/同花顺（2）/outputs/society0-next-composition/implementation-handoff/README.md>)，接口定义保持在分项规范，实际结果在本目录另行记录。

## 一、身份

用户已授权按交接计划开始实施。基线为 `e57707ca637ee4e5d8b97161b04c9ae70e824a41`；远端 `https://github.com/lavapapa/society0.git`；任务分支 `feat/next-foundation-composition`，任务工作树 `/Users/marvin/Documents/同花顺（2）/research/simulation/society0-foundation-worktree`。原 next 工作树保持干净；稳定线、main 和产业环境不修改。

测试复用原仓 `.venv/bin/python`（Python 3.12.12），显式 PYTHONPATH 指向本任务 src 与根目录，已核对 society0 实际导入位置。APSW 3.53.4.0、pytest 9.1.1、pytest-asyncio 1.4.0、rapidjson 1.25、backports-zstd 1.7.0、pydantic-ai-slim 2.54.0；`uv pip check` 通过，`uv lock --check --offline` 通过。本次没有重建全部 extras 的环境；缺失可选能力按真实失败定位，禁止解释为已覆盖。未改依赖锁。

## 二、待办

| 当前 todo | 范围与出口 | 状态 |
|---|---|---|
| 一、集中收尾与调整 | 收齐已知修复、调用方与文档；固定源码后执行一次确定性全量；按既定范围核对同语义成本；E2E 通过后合入并推送 Next。 | 进行中 |
| 二、端到端验收 | 本人通过公共入口实跑运行、工具与记忆、故障恢复及资源关闭，保存实际工件；GPU 备用嵌入部署按用户新授权交给一个子智能体。 | 核心 E2E 通过；备用服务待稳定 SSH |

既有 T00—T09 已完成开发验证，细节和各次红绿结果保存在 [验证记录](validation-result.md)。原 T10—T14 编号继续用于关联历史证据，后续实际推进按上面两个大项执行。公开十类场景已有 99 项回归及最后 4 项来源／成本模式检查通过；真实 OAuth 的工具和恢复证据另行保存。

## 三、执行

已完成开发保留失败先行与最小修复证据。用户已取消目标模式，目标保持 paused/disarmed，停止自动续轮；剩余工作合并为两个大项，由本人执行主线与全部文档。用户新授权一个子智能体执行 GPU 备用嵌入部署，该委派未扩展到主线开发或测试。

本人公共入口全部十类 case 的 17 个验收报告通过，真实 Codex OAuth＋SiliconFlow BGE-M3 的双主体自动写入、自动召回、主动工具、完整 Thread 与可信点新进程恢复通过。[成本结果](cost-result.md) 固定两侧源码、三模式、两种历史、每侧五次样本，全部业务内容和恢复快照相等；结果包含回退与测量边界。确定性全量 1173 pass 已执行，之后的代码改动限于独立基准入口，其仪表／计时／冷恢复同业务结果已专项核对。剩余收尾为 Next 合入推送与任务工作树清理。GPU 备用部署的 SSH 管理路线持续 timeout/reset，部署尚未完成，现有共享服务未改，全部临时连接已退出。

确定性测试清除代理并关闭 SOCIETY0_RUN_REAL_E2E、SOCIETY0_RUN_CORE_REAL，pytest 排除 real_e2e。用户已授权通过 simcat 可达的 LiteLLM／GPU 部署开展真实模型端到端验证，并补充 Pod 间内网路径；用户进一步授权临时使用 GPU Pod 现有模型服务密钥；该部署不可用时，可通过 Society0 已有接入使用本人 Codex OAuth 模型。运行使用[真实验收共享接线](../../../tests/e2e/core_next_real_support.py)的显式 SOCIETY0_REAL_* 合同及独立部署目录。服务地址和凭据通过显式运行配置与环境提供；用户新授权 SiliconFlow 便宜／免费嵌入模型及独立轻量备用部署，本次实际使用 BGE-M3，凭据未复制、输出或持久化。测试输出保留在本任务 `evidence` 子目录，失败日志不覆盖。后续在各任务出口填写命令、实际结果和残余风险，不用旧报告替代新验收。
