# 基础组合改造实施记录

本文件记录 Society0 Next 基础组合任务的执行顺序与状态。规格入口为[交接包](</Users/marvin/Documents/同花顺（2）/outputs/society0-next-composition/implementation-handoff/README.md>)，接口定义保持在分项规范，实际结果在本目录另行记录。

## 一、身份

用户已授权按交接计划开始实施。基线为 `e57707ca637ee4e5d8b97161b04c9ae70e824a41`；远端 `https://github.com/lavapapa/society0.git`；任务分支 `feat/next-foundation-composition`，任务工作树 `/Users/marvin/Documents/同花顺（2）/research/simulation/society0-foundation-worktree`。原 next 工作树保持干净；稳定线、main 和产业环境不修改。

测试复用原仓 `.venv/bin/python`（Python 3.12.12），显式 PYTHONPATH 指向本任务 src 与根目录，已核对 society0 实际导入位置。APSW 3.53.4.0、pytest 9.1.1、pytest-asyncio 1.4.0、rapidjson 1.25、backports-zstd 1.7.0、pydantic-ai-slim 2.54.0；`uv pip check` 通过，`uv lock --check --offline` 通过。本次没有重建全部 extras 的环境；缺失可选能力按真实失败定位，禁止解释为已覆盖。未改依赖锁。

## 二、待办

| 任务 | 内容 | 状态 |
|---|---|---|
| T00 | 规则、版本、工作树、依赖和执行计划 | 完成 |
| T01 | 登记注销、索引及安装者托管 | 完成：失败先行与 159 项相关回归 |
| T02 | 主体目录委托、只读选择、公共工件读 | 完成：失败先行与 111 项相关回归 |
| T03 | 展开插件图与 runner 清单 | 完成：失败先行与 79 项相关回归 |
| T04 | Runtime/worker 两阶段关闭 | 完成：失败先行与 102 项相关回归 |
| T05 | 推荐来源、同视图版本、semantic 与分页 | 完成：失败先行与 109 项相关回归 |
| T06 | 通用多来源认知组合 | 完成：40 个新用例与 115 项相关回归 |
| T07 | 社交来源及调用方迁移 | 进行中 |
| T08 | rule/LLM 官方配方 | 待执行 |
| T09 | 公开完整例子与只读验收器 | 待执行 |
| T10 | 调用方、文档、能力矩阵核对 | 待执行 |
| T11 | 确定性全量验证 | 待执行 |
| T12 | 独立公开路径端到端验证 | 待执行 |
| T13 | 固定源码同语义成本验证 | 待执行 |
| T14 | Next 集成、发布身份与交付 | 待执行 |

## 三、执行

每个任务先写可观察失败用例，再实现最小产品改动并完成相关回归。共享社交文件依次处理 T01→T04→T05→T07。本模型自行负责整体设计和文档；独立验收留在完成端到端开发后，范围为公开路径 E2E。

默认清除代理并关闭 SOCIETY0_RUN_REAL_E2E、SOCIETY0_RUN_CORE_REAL，pytest 排除 real_e2e；真实模型与付费 embedding 未授权。测试输出保留在本任务 `evidence` 子目录，失败日志不覆盖。后续在各任务出口填写命令、实际结果和残余风险，不用旧报告替代新验收。
