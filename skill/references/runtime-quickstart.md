# 首次实验的技术起步

本页用于 Step2 配置与 Step3 构建运行。正式实验的 Step1 先完成两轮并行搜索及专业设计选择。首次使用、缺环境或修复时读取 [安装与环境验收](installation.md)；已有环境直接核对其记录。技术试跑与正式实验使用不同运行目录。

## 一、配置

先确定解释器、Next 源码身份和本地研究目录；沿用安装指南的 `SOCIETY0_REPO`、`SOCIETY0_PYTHON`、`SOCIETY0_STUDY`。将 skill 的 [两轮 starter](../assets/minimal_experiment.py) 复制到研究版本目录。下面从已克隆仓库取同一资产，目录中已有脚本时先核对版本，避免覆盖。

```sh
export SOCIETY0_RELEASE="$(git -C "$SOCIETY0_REPO" rev-parse HEAD)"
mkdir -p "$SOCIETY0_STUDY/versions/v001/runs"
test ! -e "$SOCIETY0_STUDY/versions/v001/experiment.py" && \
  cp "$SOCIETY0_REPO/skill/assets/minimal_experiment.py" "$SOCIETY0_STUDY/versions/v001/experiment.py"
```

starter 是带记忆的接线样例，使用正式插件、持久主体、LLMDriver 与代码调度：一个主体先查看消息详情，记忆扩展在原 Thread 关闭前提取成功浏览的经验，下一轮提交结构化访谈。需要 `llm`、`memory` 依赖组；空记忆仍保留当前消息，提供方失败留下诊断并阻止该步骤发布。用户已排除记忆或此互动与实验不符时，使用对应的无记忆/领域计划做代表性试跑，保持所选机制。

### 1.1 模型与记忆

在用户指定的运行环境中注入以下变量，值来自用户选定的服务与研究配置。密钥通过已授权凭据机制注入进程，避免写进脚本、公开配置与终端历史。

| 用途 | starter 实际读取的变量 |
|---|---|
| 决策与访谈模型 | `SOCIETY0_LLM_MODEL`、`SOCIETY0_LLM_BASE_URL`、`SOCIETY0_LLM_API_KEY` |
| 决策请求参数 | 可选 `SOCIETY0_LLM_OPTIONS`，JSON 对象，省略为 `{}` |
| 记忆 embedding | `SOCIETY0_EMBED_MODEL`、`SOCIETY0_EMBED_BASE_URL`、`SOCIETY0_EMBED_API_KEY`、`SOCIETY0_EMBED_DIMENSIONS` |
| 源码身份 | `SOCIETY0_RELEASE` |

starter 的决策与提取共用 `models['main']`，提取通过 `memory_plugin(extraction=('resources','models','main'))` 接线；embedding 独立。CLI 使用 OpenAI 兼容端点、端点并发 1、超时 60 秒，embedding 的 `send_dimensions=False`，声明维度必须等于服务实际输出。订阅服务不提供 embedding，应另外选定嵌入来源。

研究要求独立提取模型时，在研究版本脚本的 `model_plugin` 中注册 `decision` 与 `extraction` 两个 profile，让 `llm_driver_plugin(provider=('resources','models','decision'))` 与 `memory_plugin(extraction=('resources','models','extraction'))` 分别引用。同步冻结两个 profile、提取参数和凭据变量名到 RunContract；starter CLI 没有独立提取模型开关。其他 provider_type、订阅接入和模型参数，以已克隆仓库的 `$SOCIETY0_REPO/docs/core-next/models-contract.md` 的“组合”和 `$SOCIETY0_REPO/docs/core-next/subscription-guide.md` 为准。

`MemoryPolicy(auto_write=True, auto_recall=True, active_tools=True)` 是 starter 的实际策略：自动写入、自动召回、主动工具三项独立选择；访谈默认跳过自动写入。研究调整这些开关时修改研究版本中的 `MemoryPolicy`，同步更新 RunContract，避免把三个开关合并成一个“开启记忆”。未使用记忆的研究计划移除相应扩展、服务依赖和配置，并按实际机制决定是否还需要 embedding。

### 1.2 并发与预算

主体并发由 `Phase.capacity` / `Runtime.capacity` 决定；提供方端点 `concurrency`、单个 ModelProvider 的 `global_concurrency` 与跨 profile 共用的 `request_limit=asyncio.Semaphore(...)` 分别限制物理请求。共享 semaphore 传给 `model_plugin` 与 `embedding_plugin`；它限制并发数量，累计请求数另行计量。EmbeddingProvider 没有 `global_concurrency` 参数。starter 的 Runtime 与端点容量均为 1；扩大规模前分别核对这些层级。

starter 的 `LLMPolicy` 默认 `max_turns=None`、`max_action_calls=None`，一次主体激活可能多次请求；两个业务时点并无“两次请求”的保证。真实试跑使用第二轮已批准的费用、规模与停止条件，包含恢复重放成本；已有授权覆盖时直接执行。尚无授权时一次说明推荐安排和可能费用，请用户确认，常规技术参数由 Agent 配置。研究版本可用 `LLMPolicy(max_turns=..., max_action_calls=...)`、提供方 `max_attempts`、输出参数与超时表达已接受的合同，记录到 RunContract；保留正常推理空间，禁止为提速擅自收紧既有预算。若原样使用无限轮次配置，明确其成本风险，优先采用提供方费用上限；无硬上限能力时说明监督与停止的延迟窗口，再取得该风险的明确授权。

每请求 token 上限按提供方真实支持的参数放入 `SOCIETY0_LLM_OPTIONS`；它同时影响共用模型的提取。模型或 SDK 拒绝参数时核查原始错误，按原研究合同修订新版本。预算耗尽、截断与取消均保留未完成状态。停机后按可信完整点新建恢复运行。

## 二、零请求检查

检查模式安装真实服务、发布初始完整点并关闭资源，业务时间序列为空。下列子 shell 使用离线占位配置，不改变外层真实环境；测试维度 2 仅用于初始化。模型、提取和 embedding 的真实服务能力留待试跑验证。

```sh
(
  export SOCIETY0_LLM_MODEL='offline-check'
  export SOCIETY0_LLM_BASE_URL='http://unused.invalid/v1'
  export SOCIETY0_LLM_API_KEY='unused'
  export SOCIETY0_LLM_OPTIONS='{}'
  export SOCIETY0_EMBED_MODEL='offline-check'
  export SOCIETY0_EMBED_BASE_URL='http://unused.invalid/v1'
  export SOCIETY0_EMBED_API_KEY='unused'
  export SOCIETY0_EMBED_DIMENSIONS='2'
  "$SOCIETY0_PYTHON" "$SOCIETY0_STUDY/versions/v001/experiment.py" \
    --check --run-dir "$SOCIETY0_STUDY/versions/v001/runs/check-001"
)
```

验收完整步骤为 0、无物理模型/embedding 请求。每次重试换新目录；目录已存在时核对原记录。检查通过后保存解释器与依赖身份、实际源码、公开配置、检查命令和退出码。环境记录保存凭据变量名称，凭据值留在进程环境；同时检查 URL、额外请求选项中是否夹带授权信息。

## 三、真实试跑

用户批准预算且研究脚本已落实所选策略后，执行一主体、两业务时点的真实最小试跑。下面先检查变量是否存在，错误信息不打印值。无凭据或未批准时停在本节入口，环境记录写明待补配置与“真实端点试跑未执行”。

```sh
: "${SOCIETY0_RELEASE:?需要实际源码身份}"
: "${SOCIETY0_LLM_MODEL:?需要决策模型}"
: "${SOCIETY0_LLM_BASE_URL:?需要决策端点}"
: "${SOCIETY0_LLM_API_KEY:?需要运行时凭据}"
: "${SOCIETY0_EMBED_MODEL:?需要嵌入模型}"
: "${SOCIETY0_EMBED_BASE_URL:?需要嵌入端点}"
: "${SOCIETY0_EMBED_API_KEY:?需要嵌入凭据}"
: "${SOCIETY0_EMBED_DIMENSIONS:?需要实际向量维度}"
"$SOCIETY0_PYTHON" "$SOCIETY0_STUDY/versions/v001/experiment.py" \
  --run-dir "$SOCIETY0_STUDY/versions/v001/runs/pilot-001"
"$SOCIETY0_PYTHON" -m society0.kernel.observation "$SOCIETY0_STUDY/versions/v001/runs/pilot-001"
```

核对 `runner-status.json` 和 Observation 的完整步骤为 2，检查实际 `news.view_details` 完成事实、原始工具参数/返回、提取记忆与下一轮召回、结构化可信度结果及物理请求用量。`runner.json` 保存公开合同，`timings.jsonl` 保存阶段数字。成功受理、业务完成、等待与未完成分别判断。提取返回空记忆时记录实际结果，额外重复试验须在预算内进行。方法与结果检查见 [运行、监测与分析](run-monitor-analyze.md)。

## 四、完整点恢复

恢复使用同一研究版本与真实配置，在新目录从已发布第 1 步继续第 2 步。该操作会再次请求访谈模型，并可能调用 embedding，费用计入获批预算。下面是原样 starter 的完整恢复命令。已修改模型接线的研究版本应复用其实际计划工厂，保持恢复前后合同一致。

```sh
"$SOCIETY0_PYTHON" - <<'PY'
import asyncio
import json
import os
import runpy
from pathlib import Path
from society0.kernel.runner import run_plan

root = Path(os.environ['SOCIETY0_STUDY']) / 'versions' / 'v001'
starter = runpy.run_path(str(root / 'experiment.py'))
def endpoint(prefix):
    return {'id': prefix.lower(), 'model': os.environ[prefix + '_MODEL'],
            'base_url': os.environ[prefix + '_BASE_URL'],
            'api_key': os.environ[prefix + '_API_KEY'], 'concurrency': 1, 'timeout': 60}
model = {'endpoints': [endpoint('SOCIETY0_LLM')],
         'request_options': json.loads(os.environ.get('SOCIETY0_LLM_OPTIONS', '{}'))}
embedding = {'endpoints': [{**endpoint('SOCIETY0_EMBED'), 'send_dimensions': False}],
             'dimensions': int(os.environ['SOCIETY0_EMBED_DIMENSIONS'])}
plan = starter['build_plan'](release={'commit': os.environ['SOCIETY0_RELEASE']},
                             model=model, embedding=embedding, moments=(1, 2))
print(asyncio.run(run_plan(root / 'runs' / 'resumed-001', plan,
                          source=root / 'runs' / 'pilot-001', step=1)))
PY
```

保留 `moments=(1, 2)` 完整时间序列，SequenceSchedule 从第 1 步之后继续。通过 Observation 检查新运行完整步骤为 2、来源为原第 1 步、浏览事实与记忆继承且未再次执行浏览动作、访谈新结果可读；原运行保持不变。模型结果可能因重新请求而变化，记录与原结果的实际差异。真实故障时以完整描述符与 Thread 共同判断恢复点；模型/研究配置或代码改变则创建新版本，明确恢复来源与变更。

SNS 研究可读取已克隆仓库的 `$SOCIETY0_REPO/examples/core_next/sns_community/README.md`。确保每位主体取得真实对象引用与动作格式，再检查推荐流和入口来源各自的记录；技术验收完成后回到已批准的研究设计开展正式实验。
