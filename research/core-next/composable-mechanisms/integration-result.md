# 组合机制冻结源码最终验收

2026-10-10 在分支 `codex/next-composable-mechanisms` 完成离线最终验收。测试前后 HEAD 均为 `521bda2ad28a9855ef18e76090c353bda907b4f2`，基线 `7320b13a7d8d58cbe116f9bbc407224fec05de3c` 已确认为其祖先。本记录对应冻结提交的实际执行结果。

## 一、结果

完整离线测试退出码 0：**1048 passed、1 skipped、15 deselected，67.31 秒**。JUnit 共 1049 个记录，0 failures、0 errors；通过项为 primary 944、experiments 104。e2e 的 15 个真实端点用例由 `not real_e2e` 排除，未调用真实模型。

- 唯一跳过项为 `test_core_next_vector_backends` 的模块收集：需要独立 sqlite-vec feasibility 环境。
- 旧新引擎 oracle 23 项通过，覆盖实际旧引擎隔离进程、共同逐笔结果、Next 跨进程恢复与主动损坏比较器。旧 social 恢复动态关注丢失保留为精确断言的旧版本反例。
- 全量覆盖包括组合依赖与生命周期、公共 API、RR 信息入口、social 子机制/排序/认知、存储恢复、观测与 workbench，以及离线实验。此次没有采集代码覆盖率百分比。
- 实际规则 pilot 完成 step 2，`diagnostic_errors=0`；新生成的载荷实际送入 Node 测试。
- Node：**9 tests、9 pass、0 fail、0 skipped、0 cancelled、0 todo**，733.58 ms。实际载荷渲染用例已执行，验证表格 8 行、趋势 2 行以及渲染标记。

证据：[离线日志](</tmp/society0-composition-final-521bda2-offline.log>)、[JUnit](</tmp/society0-composition-final-521bda2-offline.xml>)、[pilot 日志](</tmp/society0-composition-final-521bda2-pilot.log>)、[实际载荷](</tmp/society0-composition-final-521bda2-pilot-payload.json>)、[Node 日志](</tmp/society0-composition-final-521bda2-node.log>)。

## 二、复现

所有命令 cwd：

```text
/Users/marvin/Documents/同花顺（2）/.codex-worktrees/society0-next-composition
```

离线测试（job `bash-876` 已收集，退出码 0）：

```bash
env -u ALL_PROXY -u HTTP_PROXY -u HTTPS_PROXY -u NO_PROXY \
  -u all_proxy -u http_proxy -u https_proxy -u no_proxy \
  PYTHONPATH=src:. SOCIETY0_RUN_REAL_E2E=0 \
  '/Users/marvin/Documents/同花顺（2）/research/simulation/society0core/.venv/bin/python' \
  -m pytest tests/primary tests/e2e tests/experiments -m 'not real_e2e' \
  -o addopts='-rs' --junitxml=/tmp/society0-composition-final-521bda2-offline.xml \
  > /tmp/society0-composition-final-521bda2-offline.log 2>&1
```

随后生成实际 pilot 并复用已有 Node 依赖（job `bash-882` 已收集，退出码 0；本轮未执行 npm install/ci）：

```bash
set -e
export PYTHONPATH=src:. SOCIETY0_RUN_REAL_E2E=0
unset ALL_PROXY HTTP_PROXY HTTPS_PROXY NO_PROXY all_proxy http_proxy https_proxy no_proxy
printf '{"release":{"commit":"%s"},"start":1,"end":2}\n' "$(git rev-parse HEAD)" > /tmp/society0-composition-final-521bda2-pilot.json
'/Users/marvin/Documents/同花顺（2）/research/simulation/society0core/.venv/bin/python' -m society0.kernel.runner --factory examples.core_next.conversation_pilot:build --config /tmp/society0-composition-final-521bda2-pilot.json --output /tmp/society0-composition-final-521bda2-pilot-run > /tmp/society0-composition-final-521bda2-pilot.log 2>&1
'/Users/marvin/Documents/同花顺（2）/research/simulation/society0core/.venv/bin/python' -m society0.kernel.workbench --run /tmp/society0-composition-final-521bda2-pilot-run --version ci --steps 1 2 --actors a b c d --output /tmp/society0-composition-final-521bda2-pilot-payload.json >> /tmp/society0-composition-final-521bda2-pilot.log 2>&1
CORE_NEXT_WORKBENCH_PAYLOAD=/tmp/society0-composition-final-521bda2-pilot-payload.json npm test --prefix tools/workbench-template > /tmp/society0-composition-final-521bda2-node.log 2>&1
```

本机为 macOS、Python 3.12.12、pytest 9.1.1、Node v24.16.0、npm 12.0.2；Python 使用用户指定原仓虚拟环境。CI 的 [tests workflow](<../../../.github/workflows/tests.yml>) 使用 Ubuntu、Python 3.12、`uv sync --frozen --all-extras`、Node 22 与 npm ci。本轮验证本机已有依赖及 Node 24 的行为，未执行 CI22，也未重新验证从锁文件安装的可复现性或清除既有 npm 安装告警。

## 三、工作树

启动时全工作树状态为空；`git diff -- src tests benchmarks examples` 及同路径暂存差异均为空。测试后 HEAD 未改变，以下检查通过且无源码/依赖差异：

```bash
git diff 521bda2ad28a9855ef18e76090c353bda907b4f2 --exit-code -- src tests benchmarks examples tools uv.lock native
git diff --check
git diff --name-status
git diff --cached --name-status
git status --porcelain=v1 --untracked-files=all
git ls-files --others --ignored --exclude-standard
```

测试完成时全部已跟踪文件及暂存区差异为空。全目录检查发现并行 benchmark 作者新增三份成本工件：[成本报告](<cost-formal-report.md>)、[成本表](<cost-formal-tables.md>)、[成本索引](<cost-formal-index.json>)；这些文件未由验收者修改，未等待其完成。忽略路径前后新增两个 Hypothesis 常量缓存，未出现新增源码或仓库内 pilot/checkpoint 目录。已有忽略内容主要为 node_modules、Python 字节码及 pytest/Hypothesis 缓存，目录统计见[工件清单](</tmp/society0-composition-final-521bda2-directory-inventory.json>)。

[验收前状态](</tmp/society0-composition-final-521bda2-status-before.txt>)、[测试后状态](</tmp/society0-composition-final-521bda2-status-after-tests.txt>)、[忽略路径前快照](</tmp/society0-composition-final-521bda2-ignored-before.txt>)与[忽略路径后快照](</tmp/society0-composition-final-521bda2-ignored-after.txt>)保留在 /tmp。最终仅由验收者更新本记录；源码、测试、benchmark、examples 和锁文件相对冻结提交无改动。没有移动、删除、Git 写操作或真实模型调用。

## 四、边界

该冻结提交的完整离线测试及本机实际 pilot 的 Node 渲染验收通过。真实模型端点、独立 sqlite-vec 环境、CI Node 22、浏览器交互与性能结论不在此次执行范围；成本结论由独立成本报告负责。两项后台 job 均已收集，无验收者遗留后台任务。
