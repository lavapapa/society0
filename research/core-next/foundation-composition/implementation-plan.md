# 基础组合改造实施记录

本文件记录 Society0 Next 基础组合任务的执行顺序与状态。规格入口为[交接包](</Users/marvin/Documents/同花顺（2）/outputs/society0-next-composition/implementation-handoff/README.md>)，接口定义保持在分项规范，实际结果在本目录另行记录。

## 一、身份

用户已授权按交接计划开始实施。基线为 `e57707ca637ee4e5d8b97161b04c9ae70e824a41`；远端 `https://github.com/lavapapa/society0.git`；任务分支 `feat/next-foundation-composition`，任务工作树 `/Users/marvin/Documents/同花顺（2）/research/simulation/society0-foundation-worktree`。原 next 工作树保持干净；稳定线、main 和产业环境不修改。

测试复用原仓 `.venv/bin/python`（Python 3.12.12），显式 PYTHONPATH 指向本任务 src 与根目录，已核对 society0 实际导入位置。APSW 3.53.4.0、pytest 9.1.1、pytest-asyncio 1.4.0、rapidjson 1.25、backports-zstd 1.7.0、pydantic-ai-slim 2.54.0；`uv pip check` 通过，`uv lock --check --offline` 通过。本次没有重建全部 extras 的环境；缺失可选能力按真实失败定位，禁止解释为已覆盖。未改依赖锁。

## 二、待办

| 当前 todo | 范围与出口 | 状态 |
|---|---|---|
| 一、集中收尾与调整 | 收齐已知修复、调用方与文档；固定源码后执行一次确定性全量；按既定范围核对同语义成本；E2E 通过后合入并推送 Next。 | 进行中 |
| 二、端到端验收 | 本人通过公共入口实跑运行、工具与记忆、故障恢复及资源关闭，保存实际工件；本阶段全部自行执行。 | 待执行 |

既有 T00—T09 已完成开发验证，细节和各次红绿结果保存在 [验证记录](validation-result.md)。原 T10—T14 编号继续用于关联历史证据，后续实际推进按上面两个大项执行。公开十类场景已有 99 项回归及最后 4 项来源／成本模式检查通过；真实 OAuth 的工具和恢复证据另行保存。

## 三、执行

已完成开发保留失败先行与最小修复证据。用户已取消目标模式，目标保持 paused/disarmed，停止自动续轮；最新要求将剩余工作合并为两个大项，本阶段全部由本人执行，不使用子智能体。

推进先完成集中收尾中的代码、文档与一次全量验证，再亲自执行公共路径 E2E，最后收齐固定成本结果与 Next 集成交付。具体缺陷触发受影响路径的复核，额外全量重跑须记录原因；成本按既定模式、历史规模及每侧五次采样封口。真实 memory/embedding 服务可用性为 E2E 的明确待办，当前探测 SSH 已断开且无在途模型请求。

确定性测试清除代理并关闭 SOCIETY0_RUN_REAL_E2E、SOCIETY0_RUN_CORE_REAL，pytest 排除 real_e2e。用户已授权通过 simcat 可达的 LiteLLM／GPU 部署开展真实模型端到端验证，并补充 Pod 间内网路径；用户进一步授权临时使用 GPU Pod 现有模型服务密钥；该部署不可用时，可通过 Society0 已有接入使用本人 Codex OAuth 模型。运行使用[真实验收共享接线](../../../tests/e2e/core_next_real_support.py)的显式 SOCIETY0_REAL_* 合同及独立部署目录。服务地址和凭据由环境提供，付费 embedding 保持未授权。测试输出保留在本任务 `evidence` 子目录，失败日志不覆盖。后续在各任务出口填写命令、实际结果和残余风险，不用旧报告替代新验收。
