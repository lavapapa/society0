# 组合机制离线验收记录

本次验收于 2026-10-10 在共享工作树执行。基线 HEAD 为 `7320b13a7d8d58cbe116f9bbc407224fec05de3c`，分支 `codex/next-composable-mechanisms`。源码和测试保持只读；本记录是验收者新增的唯一仓库文档。共享作者仍可修改文件，测试结果对应本次实际采集与执行的版本。

## 一、结果

全离线测试退出码 0：**1047 passed、1 skipped、15 deselected，64.27 秒**。JUnit 包含 1048 个记录（含收集阶段跳过项）、0 failures、0 errors；通过项为 primary 943、experiments 104。e2e 的真实端点用例由 marker 排除，未执行模型网络请求。

- Oracle：23 项通过，包括实际旧引擎、跨进程恢复、action.result 共同映射与结果主动损坏检测；本轮没有 oracle 红项。Next oracle 源中已有非空 persona。
- 跳过：`test_core_next_vector_backends` 要求独立 sqlite-vec feasibility 环境，归属可选实验依赖。
- Node：9 tests、9 pass、0 fail、0 skipped；实际 pilot 完成 step 2、diagnostic_errors 0，载荷用于 Node 渲染测试。
- npm ci：退出码 0，74 packages installed。安装器报告 9 vulnerabilities（1 moderate、8 high），以及 esbuild/fsevents install scripts 被本机 npm 策略阻止；没有运行 audit fix、修改锁文件或放宽脚本策略。现有 Node 测试仍通过。
- 本机 Node v24.16.0 / npm 12.0.2，CI 使用 Node 22，版本等同性尚未验证。

证据：[离线日志](</tmp/society0-composition-integration-offline.log>)、[JUnit](</tmp/society0-composition-integration-offline.xml>)、[Node 日志](</tmp/society0-composition-integration-node.log>)、[安装日志](</tmp/society0-composition-integration-npm-ci.log>)、[实际 pilot 载荷](</tmp/society0-composition-integration-pilot-payload.json>)。

## 二、命令

所有命令 cwd：

```text
/Users/marvin/Documents/同花顺（2）/.codex-worktrees/society0-next-composition
```

完整离线命令（job bash-812，已收集，退出码 0）：

```bash
env -u ALL_PROXY -u HTTP_PROXY -u HTTPS_PROXY -u NO_PROXY \
  -u all_proxy -u http_proxy -u https_proxy -u no_proxy \
  PYTHONPATH=src:. SOCIETY0_RUN_REAL_E2E=0 \
  /Users/marvin/Documents/同花顺（2）/research/simulation/society0core/.venv/bin/python \
  -m pytest tests/primary tests/e2e tests/experiments -m 'not real_e2e' \
  -o addopts='-rs' --junitxml=/tmp/society0-composition-integration-offline.xml \
  > /tmp/society0-composition-integration-offline.log 2>&1
```

Node 依赖与实际 pilot（依次为 jobs bash-815、bash-820、bash-823，全部已收集，退出码均为 0）：

```bash
npm ci --prefix tools/workbench-template > /tmp/society0-composition-integration-npm-ci.log 2>&1
export PYTHONPATH=src:. SOCIETY0_RUN_REAL_E2E=0
unset ALL_PROXY HTTP_PROXY HTTPS_PROXY NO_PROXY all_proxy http_proxy https_proxy no_proxy
printf '{"release":{"commit":"%s"},"start":1,"end":2}\n' "$(git rev-parse HEAD)" > /tmp/society0-composition-integration-pilot.json
/Users/marvin/Documents/同花顺（2）/research/simulation/society0core/.venv/bin/python -m society0.kernel.runner --factory examples.core_next.conversation_pilot:build --config /tmp/society0-composition-integration-pilot.json --output /tmp/society0-composition-integration-pilot-run
/Users/marvin/Documents/同花顺（2）/research/simulation/society0core/.venv/bin/python -m society0.kernel.workbench --run /tmp/society0-composition-integration-pilot-run --version ci --steps 1 2 --actors a b c d --output /tmp/society0-composition-integration-pilot-payload.json
CORE_NEXT_WORKBENCH_PAYLOAD=/tmp/society0-composition-integration-pilot-payload.json npm test --prefix tools/workbench-template > /tmp/society0-composition-integration-node.log 2>&1
```

启动前用 `ps -axo pid,etime,command` 检查，未发现 pytest/npm test/npm ci 在途进程。没有启动 server，没有执行 git add/commit/push/checkout。

## 三、工作树

`git diff --check` 两次完成且无输出。锁文件状态检查无修改。验收前后对 main、stable 分支（含 origin 对应远端引用）及所有 tags 执行以下引用快照比较，diff 无差异：

```bash
git for-each-ref refs/heads/main refs/heads/stable/ refs/remotes/origin/main refs/remotes/origin/stable/ refs/tags --format='%(refname) %(objectname)'
git diff --check
git diff --name-only -- src
git ls-files --others --exclude-standard src
```

[引用前快照](</tmp/society0-composition-integration-refs-before.txt>)与[引用后快照](</tmp/society0-composition-integration-refs-after.txt>)、[已跟踪修改清单](</tmp/society0-composition-integration-modified.txt>)、[未跟踪清单](</tmp/society0-composition-integration-untracked.txt>)供归因。观察到的生产源码变更来自共享工作树既有作者，本验收未修改它们：

```text
已跟踪修改：
src/society0/kernel/actors.py
src/society0/kernel/cognition.py
src/society0/kernel/composition.py
src/society0/kernel/information_sql.py
src/society0/kernel/interaction.py
src/society0/kernel/llm.py
src/society0/kernel/memory.py
src/society0/kernel/plugins.py
src/society0/kernel/workspace.py
src/society0/plugins/__init__.py
src/society0/plugins/round_robin.py
src/society0/plugins/social.py
src/society0/plugins/social_models.py
新增未跟踪：
src/society0/kernel/vectors.py
src/society0/plugins/social_cognition.py
src/society0/plugins/social_domain.py
src/society0/plugins/social_ranking.py
src/society0/plugins/social_recommendation.py
src/society0/plugins/social_semantic.py
```

大工件检查使用 `git ls-files --others`（分别含/不含 `--ignored`）、文件 stat 与 `du -sk`：cost-frozen-mechanism 占 74728 KiB，cost-small 占 18116 KiB，源码 tar.gz 占 872 KiB。被忽略检查点文件共 72,212,480 字节；最大单个 SQLite 为 3,903,488 字节。未忽略的 >=1MiB 或 sqlite/zst/bin/db 条目共 66 个、19,824,057 字节，其中 12 份原始 JSON 各约 1.57MB。原始检查点、压缩变化集及源码归档适合保留在 /tmp 或外部实验工件位置，仓库保留汇总与引用；本轮按授权仅报告，全部未移动或删除。

## 四、边界

本轮失败分组为空，未复现已知 oracle 红项。旧引擎恢复动态关注丢失作为 oracle 中的明确反例继续保留，属于旧版本限制。docs 作者后续 typing/API 导入测试若在本轮收集完成后才落盘，需要单独补验；本报告不覆盖未来修改。共享文件未冻结为新提交，发布验收仍需在最终源码固定后确认。真实模型、独立 sqlite-vec 环境、Node 22 与 npm 安装器警告留作后续独立事项。

四个后台 job 均已收集，无验收者遗留后台任务。当前证据支持本次离线组合与实际 pilot 渲染测试通过。
