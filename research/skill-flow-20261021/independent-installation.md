# Society0 skill 独立安装验收

最新结论：主流程与离线运行通过；首次发现的两项 P2 文档问题已由作者修复，并通过独立复验，详见第五节。以下保留首次发现及当时证据。现有授权自动推进、预算风险披露、明确无记忆分支及工作台缺失处理均有清楚约束。本次采用非作者视角，实际复制完整 skill 到临时独立目录，从入口沿安装、运行起步、构建运行与交接读取，并执行文档提供的离线命令。

## 一、问题

两项问题影响独立安装后的查阅和结果分析，均有明确位置，未修改被审材料。

### 1.1 P2：三份直接参考仍有 11 处越出 skill 的相对链接

完整复制 skill 后，73 处非代码块本地 Markdown 链接中有 11 处断链。核心五页自身链接均可解析，但主入口与构建流程直接路由的以下参考仍假设 skill 放在仓库内：

| 位置 | 断链目标与影响 |
|---|---|
| [environment-design.md:29](../../skill/references/environment-design.md#L29) | 交互合同，无法读取资源归属约定 |
| [environment-design.md:31](../../skill/references/environment-design.md#L31) | 社交合同、推荐观测示例，两处 |
| [environment-design.md:50](../../skill/references/environment-design.md#L50) | 双机制 conversation 示例 |
| [environment-design.md:54](../../skill/references/environment-design.md#L54) | 插件合同 |
| [environment-design.md:58](../../skill/references/environment-design.md#L58) | 共享环境示例、Shell 合同，两处 |
| [agent-design.md:7](../../skill/references/agent-design.md#L7) | 规则与模型配方、公共组合示例，两处 |
| [step-dsl.md:21](../../skill/references/step-dsl.md#L21) | runner 共享计算合同 |
| [step-dsl.md:27](../../skill/references/step-dsl.md#L27) | 公共组合示例 |

这些目标均使用 `../../docs/...` 或 `../../examples/...`，复制后指向独立 skill 外部不存在的位置。尤其[构建与运行:26](../../skill/references/build-and-run.md#L26)直接引导读取调度页，因此会在实际构建路径中遇到。建议沿用[安装页:24](../../skill/references/installation.md#L24)的 `$SOCIETY0_REPO` 定位方式，明确具体文件和符号，保留 skill 内部链接的独立性。

### 1.2 P2：分析页“直接运行”示例仍指向旧起步目录

[运行与分析:52](../../skill/references/run-monitor-analyze.md#L52)称完成无模型起步后可直接运行代码，[第 62 行](../../skill/references/run-monitor-analyze.md#L62)却设置 `Path("runs/first-study/resumed")`。当前[安装验收:80–83](../../skill/references/installation.md#L80-L83)实际输出为 `$SOCIETY0_STUDY/environment-check/resumed`；新入门页也没有创建旧路径的步骤。

影响：首次安装用户照抄分析示例会找不到刚产生的运行；已有工作目录若碰巧保留旧路径，还可能读取旧实验。建议示例默认读取安装页的环境变量路径，并明确 LLM starter 的不同输出位置及表名。此项依据实际已成功运行的安装输出与代码路径静态对照认定，未对其他已有目录执行读取或恢复。

## 二、实际证据

执行日期为 2026-10-21。源码 HEAD 为 `ace767216aee2d4d018e422c1ce97a99a1be4dde`，工作树包含本次正在验收的未提交 skill 修改，因此结论针对当时工作树内容。解释器为用户指定的既有 Python 3.12.12，实际导入定位到本树 [society0 入口](../../src/society0/__init__.py)。

### 2.1 命令及退出码

外层命令使用以下形式，全部实验数据与 skill 副本位于 `TemporaryDirectory`，退出后打印 `TEMPORARY_EXISTS False`。启用 `PYTHONDONTWRITEBYTECODE=1`，子进程继承该设置。

```sh
PYTHONDONTWRITEBYTECODE=1 /Users/marvin/Documents/同花顺（2）/research/simulation/society0core/.venv/bin/python - <<'PY'
# Python 临时验收包装器：复制 skill，提取已读文档代码块，
# 以 subprocess.run(['bash', '-e', '-c', command], ...) 执行。
# env: PYTHONPATH=<当前工作树>/src
#      SOCIETY0_REPO=<当前工作树>
#      SOCIETY0_PYTHON=<上述既有解释器>
#      SOCIETY0_STUDY=<TemporaryDirectory>/study
PY
```

| 实际执行内容 | 退出码 | 证据 |
|---|---:|---|
| `python --version`、`git rev-parse HEAD`、导入路径查询 | 0 | Python 3.12.12；HEAD 如上；导入本树 src |
| 从复制的[安装页:63–106](../../skill/references/installation.md#L63-L106)原样提取 `offline-recovery-start/end` 内完整 shell 块，用 `bash -e -c` 执行 | 0 | 三次 runner 全部 completed，完整步骤分别 0、1、2，diagnostic_errors 全为 0 |
| 从复制的[运行起步:9–14](../../skill/references/runtime-quickstart.md#L9-L14)及[47–60](../../skill/references/runtime-quickstart.md#L47-L60)提取 shell 块，合并为同一子 shell 执行 | 0 | starter --check completed，complete_step=0，diagnostic_errors=0 |
| 使用 Observation 读取 starter 的 status/resource_usage/list_threads | 0 | complete.step=0；failed=false；物理 requests=0；embedding_uses=0；线程 total=0 |
| 上述完整临时验收包装器（后台任务 bash-8987d92d7b174a9d，已收集完成输出） | 0 | 临时目录正常清理，无遗留运行 |
| 修正路径规范化后的独立链接扫描 | 成功 | 73 处本地链接，11 处真实断链；核心五页链接全部可解析；临时目录已清理 |

安装规则运行实际输出摘要：

```text
check complete_step 0
first complete_step 1
resumed complete_step 2
restored decisions [{'actor': 'alice', 'time': 2}, {'actor': 'bob', 'time': 2}]
```

文档最后的 Observation 循环是在恢复完成之后读取 first，确认原运行仍停在 1。恢复结果通过 `StageStore.prepare_readonly(..., step=2)` 物化后读取，两个主体时间均为 2。

首次链接扫描同时产生两个验收脚本误报：macOS 临时根目录未 resolve 导致所有链接被标为越界；正则还把代码中的 `starter['build_plan'](...)` 识别为链接。随后统一 resolve 根目录、跳过 fenced code，并重新扫描；最终 11 处均经文件内容人工核对。初次误报没有计入缺陷。

### 2.2 API 与状态核对

实际命令覆盖 runner CLI 工厂加载、零步发布、规则执行、`--source/--step` 恢复、Observation 状态与结果表、只读完整点视图。其调用与[run_plan:42–97](../../src/society0/kernel/runner.py#L42-L97)、[Observation.status:77–89](../../src/society0/kernel/observation.py#L77-L89)、[prepare_readonly:641–645](../../src/society0/kernel/storage.py#L641-L645)相符。

[SequenceSchedule:71–76](../../src/society0/kernel/schedule.py#L71-L76)按 completed_step 索引完整时间线，与保留 moments=(1, 2) 的恢复说明一致。starter 初始化使用真实插件与服务装配；此次零请求验收观察到完整点 0 和零用量。实际 LLM 行为及记忆跨轮效果留待获准真实端点验证。

## 三、流程判断

以下判断来自主入口、阶段指南、starter 与关键公开 API 的联合核对，覆盖本次要求的授权与机制风险。

- **授权自动推进清楚。** [配置:49–51](../../skill/references/experiment-configuration.md#L49-L51)保留已有完整授权并直接进入构建；[构建:3–16](../../skill/references/build-and-run.md#L3-L16)要求可见任务与自动逐项推进；构建或试跑的有限授权也被保留。新增科学设定、模型或费用范围回到对应选择。
- **预算风险已明确披露。** [运行起步:37–41](../../skill/references/runtime-quickstart.md#L37-L41)区分并发和累计调用，明确默认无限轮次、两时点并非两请求、恢复重放成本，以及缺少硬上限时的监督延迟窗口和风险授权。[LLMPolicy:83–84](../../src/society0/kernel/llm.py#L83-L84)确实默认 None。文档没有把估算或 semaphore 宣称为费用硬上限；实际执行仍依赖具体预算配置与提供方能力。
- **明确无记忆任务有排除分支。** [运行起步:16](../../skill/references/runtime-quickstart.md#L16)要求改用对应无记忆/领域计划，[第 33 行](../../skill/references/runtime-quickstart.md#L33)明确移除扩展、服务依赖和配置；[构建:22](../../skill/references/build-and-run.md#L22)要求关闭未选组件。原样 [starter:81–100](../../skill/assets/minimal_experiment.py#L81-L100)实际包含记忆及 embedding，且没有无记忆 CLI 开关，文档如实说明其样例边界。对此采用“按所选机制装配计划”的路径，未发现强制所有实验运行带记忆 starter 的要求。
- **工作台缺失处理符合当前交付边界。** [交接指南:3–11](../../skill/references/workbench-guide.md#L3-L11)要求从实际入口加载并遵循对方合同；缺失时保留本地证据与待交接状态，继续已授权且独立的工作。没有自造 API、JSON、页面或回执。本次未执行工作台接入。
- **状态判断使用实际事实。** [起步:82](../../skill/references/runtime-quickstart.md#L82)要求完整步骤、动作事实和原始工具回执共同核对；[构建:58](../../skill/references/build-and-run.md#L58)保留预算耗尽、截断和失败身份。初始化成功与正式实验完成的证据范围分明。

## 四、范围

本次没有运行单元或集成测试，没有调用真实模型、安装依赖、新建 venv、修改源码、共享解释器或其他工作树。执行范围是文档离线安装验收、starter 零请求检查及结果读取；临时文件均随 TemporaryDirectory 正常清理。持久产物为本报告。

未覆盖全新设备 clone/uv/Rust/wheel 安装、其他平台、真实服务认证及工具行为、记忆提取与召回、真实模型恢复、完整科学实验、工作台实际交互和回执。领域资料仅做链接扫描与主流程直接参考核对，未逐篇审查专业内容。上述未覆盖项对应本次授权边界；离线证据足以确认文档中的本地安装验收链能够完成。

首次建议为修复三份参考的 11 处仓库相对链接及分析示例默认路径；修复后的独立复验记录如下。真实服务与工作台接入由各自获准任务补充。

## 五、修复复验

2026-10-21 收到作者修复通知后，独立重读修改位置、重新复制 skill，并实际重新执行安装离线链及分析页完整 Python 代码块。两项原发现均已关闭。

### 5.1 链接修复通过

三份参考现在从安装页取得 `$SOCIETY0_REPO`，展开绝对路径后读取仓库资料。再次扫描独立 skill 副本，66 处非代码本地 Markdown 链接全部存在；原先 11 处仓库引用（环境 7、主体 2、调度 2）展开后全部对应当前仓库实际文件。独立包与源码仓库的边界清楚。

报告自身的本地链接也从本报告所在目录解析核对，目标正确落到 `../../skill/...` 与 `../../src/...`。首次复验扫描将历史说明中的行内 Python 调用误识别为链接；排除行内代码后重新核对，最终输出 `REPORT_LOCAL_LINKS 33 BROKEN []`、`LINK_CHECK_EXIT=0`。

### 5.2 安装后直接分析通过

沿用此前获准解释器与本树 `PYTHONPATH`，全部数据使用新的 TemporaryDirectory，启用 `PYTHONDONTWRITEBYTECODE=1`。从独立副本提取安装页标记内的原样 shell 块，以 `bash -e -c` 执行，**退出码 0**。再从分析页提取完整 Python 块，以同一解释器通过标准输入原样执行，**退出码 0**，无需替换代码中的路径。

安装输出再次确认 check/first/resumed 的完整步骤依次为 0、1、2，diagnostic_errors 均为 0。分析的实际输出为：

```text
指标： [{'step': 2, 'phase': 'decide', 'phase_index': 0, 'name': 'decisions', 'value': 2}]
表行： [{'step': 2, 'phase': 'decide', 'phase_index': 0, 'table': 'decisions', 'value': {'actor': 'alice', 'time': 2}}, {'step': 2, 'phase': 'decide', 'phase_index': 0, 'table': 'decisions', 'value': {'actor': 'bob', 'time': 2}}]
TEMPORARY_EXISTS False
```

[分析页:52–65](../../skill/references/run-monitor-analyze.md#L52-L65)已将规则验收路径与 starter 的目录、responses 表名区分说明。原发现第 1.2 节的旧第 62 行属于首次检查历史位置，当前代码位于第 63 行。

复验未调用模型、运行单元或集成测试、安装依赖或修改其他文件。当前两项修复通过，独立安装离线链能够直接衔接结果分析；第四节所列真实服务与工作台未覆盖范围保持不变。
