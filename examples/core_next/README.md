# Next 公共组合示例

本页说明如何通过公共工厂运行与验收组合能力。各场景由普通 Python 代码生成 RunPlan；配置选择有限的研究用例。所有输出保留真实 SQL、完整 Thread、完整点与退出事实。接口合同见 [Next 组合合同](../../docs/core-next/composition-contract.md)。

## 一、运行

从干净、已提交的仓库根目录执行。使用已安装项目依赖的 Python，输出目录须为新目录。以下命令冻结当前源码提交，用确定性 typed provider 完成两个步骤，再由另一个进程从 step 1 恢复。

```bash
export PYTHONPATH="$PWD/src:$PWD"
export OUT="$(mktemp -d /tmp/society0-composition.XXXXXX)"
python - <<'PY'
import json, os, subprocess
from pathlib import Path
commit=subprocess.run(['git','rev-parse','HEAD'],check=True,capture_output=True,text=True).stdout.strip()
config={'release':{'commit':commit},'case':'multi-source','variant':'default',
        'provider':'scripted','start':1,'end':2}
(Path(os.environ['OUT'])/'config.json').write_text(json.dumps(config),encoding='utf-8')
PY
python -m society0.kernel.runner \
  --factory examples.core_next.composed_cognition:build \
  --config "$OUT/config.json" --output "$OUT/continuous"
python -m society0.kernel.runner \
  --factory examples.core_next.composed_cognition:build \
  --config "$OUT/config.json" --output "$OUT/restored" \
  --source "$OUT/continuous" --step 1
python -m tests.e2e.core_next_composition_assertions \
  --case multi-source --run "$OUT/restored" \
  --continuous "$OUT/continuous" --output "$OUT/report.json"
```

验收器输出纯 JSON：`format`、`case`、`variant`、`source_commit`、`run_id`、`checks`、`first_difference`。每条检查列出预期、实际值和事实位置。成功退出 0；事实差异退出 1；输入不全或读取失败退出 2。`--output` 保存同一份报告，stdout 保留机器格式。

默认场景使用真实 social 与持久通知来源，原文含中文、emoji 与独立序号。两次激活共享同一 Moment；主体每次实际获得完整已有 Thread。通知确认发生在输入与来源位置保存之后。社交嵌入关闭，模型由确定性提供方实现。

## 二、场景

`release.commit` 和 `provider="scripted"` 为公共配置的必填项。`variant` 由下表限定；通常使用 `start=1,end=2`。规则场景使用轻量规则配方；大模型场景经完整 LLMDriver 执行，保留正常工具、回合与行动预算。

| case | variant | 实际消费路径 |
|---|---|---|
| `multi-source` | `default`、`reordered`、`discarded`、`interview` | 双来源顺序、独立选材、结构化访谈与完整历史 |
| `multi-source` | `empty-values` | 两个来源分别产生 None、0、空字典，实际保存并继续消费，区分缺席与已存在的 None |
| `multi-source` | `concurrent` | 独立 Phase 容量 2，两个主体经 Event 真正交错；各自位置、背景和原文保持隔离 |
| `multi-source` | `moment-switch` | 三个进程依次使用 Moment A、B、A；end 分别 1、2、3，从各上一步完整点恢复，原 A 身份与历史继续使用 |
| `source-lifecycle` | `default` | notices A/B → B → A/B，end 分别 1、2、3；省去来源保留其旧位置 |
| `registration` | `default`、`re-register`、`owned`、`install-failure` | 实际发现、读取、执行、撤销登记、同名重注册、所有权关闭与安装失败清理 |
| `recommendation` | `default`、`external-selection`、`external-relations`、`native`、`semantic-change`、`paging` | 真实推荐、外部数据变化、缓存失效、异步中途变化丢弃、分页与全部原文 |
| `actor-directory` | `default`、`readonly`、`persona` | 显式外部目录、分页、驱动选择与同 ReadView persona 依赖 |
| `artifact-read` | `default` | 全部长正文的 UTF-8 分块读取、所有权边界与关闭作用域 |
| `manifest` | `default`、`alternate-tree`、`write-failure` | includes 静态展开、两个 DAG、同根不同树恢复与真实清单写失败 |
| `shutdown` | `runtime-first`、`worker-first`、`runtime-error`、`worker-error` | step 2 worker 待定时取消运行，实际等待工作与关闭依赖；额外关闭异常精确记录 |
| `extensions` | `default`、`memory-write`、`memory-recall`、`memory-tools`、`memory-all`、`runtime-replacement` | 扩展准备与逆序退出，独立记忆策略，真实主动工具，替代 Runtime/Schedule |
| `configuration` | `legal-two-graphs`、`external-directory`、`base-only` | 独立 DAG、明确 data 引用与无大模型/记忆可选依赖的基础规则路径 |
| `configuration` | `duplicate-source`、`empty-source-id`、`bad-service`、`bad-position`、`missing-provider`、`missing-builder`、`duplicate-plugin`、`service-cycle`、`schema-cycle`、`records-and-data` | 指定公共边界的异常类别、消息、I/O 与业务完成事实 |

`multi-source` 默认来源为 `social.main.feed.v1` 与 `notice.main.v1`，`source-lifecycle` 为 `notice.a.v1` 与 `notice.b.v1`。`sources` 列表顺序决定材料顺序；省去来源保留其 schema 和历史 cursor，空列表保留基础认知。空值与并发场景使用固定的 `position.a.v1`、`position.b.v1`。改变 ID 的含义时创建新 ID 或新 Run。

`moment` 默认 `{"time":1,"phase":"read"}`，普通步骤序号沿用该 Moment。`activations_per_step` 固定为 2。`extensions` 额外接受 `mode="interview"`：结构化访谈保留其独立记忆写入边界。

推荐语义与记忆场景通过确定性完整文本嵌入及本运行拥有的本地 Chroma 验证；真实模型与嵌入服务需另走显式获授权环境。

## 三、失败

失败验收保留原工件，并从最近可信完整点创建新目录。`multi-source` 的 `fault` 支持 `source-read`、`append-invalid-context`、`append-invalid-message`、`append-invalid-cursor`、`after-callback-partial-write`、`after-step-flush`、`provider-error`、`before-complete`；设置 `fault_step=2`。使用成功配置从失败运行的 step 1 恢复，然后通过 `--failed-run` 同时核对未发布步骤与成功链。

配置负例在构造、图校验、服务安装或实际来源消费边界失败。下面使用公开 build/run_plan 保存完整异常栈，再以实际退出码验收。将配置的 case/variant 改为所需负例，执行于新目录。

```bash
python -c 'import asyncio,json,sys; from examples.core_next.composed_cognition import build; from society0.kernel.runner import run_plan; asyncio.run(run_plan(sys.argv[2],build(json.load(open(sys.argv[1])))))' \
  "$OUT/config.json" "$OUT/failed" > "$OUT/failure.log" 2>&1
STATUS=$?
python -m tests.e2e.core_next_composition_assertions \
  --case configuration --config "$OUT/config.json" \
  --log "$OUT/failure.log" --exit-code "$STATUS" --run "$OUT/failed"
```

构造或图错误发生在目录创建之前，`--run` 可省略；服务与位置错误需要保存已有运行目录。验收匹配确切异常类别与相关消息，并核对业务未完成。

`registration/install-failure` 与 `manifest/write-failure` 使用标准 runner CLI。消费者须将真实 subprocess 的 `returncode`、`stdout`、`stderr` 保存为失败目录中的 `cli-result.json` 后，再调用标准验收器。清单写失败发生于标准状态文件建立之前；附属退出事实与可读原生 SQL 共同解释该边界。

四个 shutdown 场景预期 CLI 非零退出、可信 complete_step=1。步骤撤销后原生 StageStore 拒绝进一步事务，已声明的 Journal 资源将关闭事实保存至退出工件；验收比对步骤内 SQL 前缀、任务排空与依赖关闭顺序。

## 四、证据

默认 `capture=true` 为行为验收保存实际 typed provider 参数、选项及完整观测副本。普通双来源及来源生命周期场景可设置 `capture=false`，用于成本采样时关闭这批额外副本；原生 Thread、请求事件、工具、来源确认、正常结果和完整点仍完整保存。故障验收要求 `capture=true`。完整行为验收使用默认捕获，成本对照另行核对原生权威事实。

实际模型服务的配置入口为 [显式环境合同](../../tests/e2e/core_next_real_support.py)；OAuth 路线通过 `SOCIETY0_REAL_LLM_PROVIDER=openai-codex` 使用成熟原生适配器。SDK 参数捕获、离线 HTTP 正文、真实网络推理分别留证。实时服务、成本结果和发布提交见各自的运行与发布工件。

确定性场景解决组合、历史、位置、缓存与清理风险；正式使用应按研究协议固定源码、依赖、模型参数与起止范围，并继续验证真实提供方的行动、记忆和恢复链。
