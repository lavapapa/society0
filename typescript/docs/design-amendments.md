# 设计补充（2026-10-29 修复轮）

本文件承载修复轮对 `inputs/society0_next_refactor_v3/docs/` 设计包的补充与修订文字。设计包本身只读；每节由对应簇在自己的 PR 中追加，标题格式 `## <finding-id>：<主题>`，正文首行写明"补充/修订 inputs/society0_next_refactor_v3/docs/<文件> §<节>"。

## 约定

- 各簇只追加本簇小节，不修改他簇小节。
- 需要裁决的事项记录在 `reviews/claude-fix-plan-20261029/02-decisions.md`；已裁决结果见 `evidence/v3fix-e/decisions.md`。

## NEW-5：测试基线只验证当前构建

补充 inputs/society0_next_refactor_v3/docs/12-validation.md §一。

`tests/` 只允许导入当前 `.build`、node 内置模块、同目录支持文件或安装态 `@society0` 包。导入冻结快照（`evidence/pr0*`）、源码树（`packages/*/src`、`packages/*/build`）、主机绝对路径与 env 兜底由 `scripts/check_tests.py` 拒绝；`scripts/test-build.py` 在运行前核验 `@society0/{host,core,schemastery,cordis}` 解析到 `.build` 内，且 build 层测试不含裸 `@society0/` 导入。
