# 运行、观察与分析

正式运行保存代码/依赖身份、研究配置、模型请求参数与公开 RunContract。runner.json 是运行合同，runner-status.json 是轻进度，timings.jsonl 记录稳定阶段数字。密钥来自环境，使用独立新目录保存每次尝试。

长会话在项目现有进度文档中维护一小段当前状态：项目路径、当前配置版本与运行目录、模型名称、已授权配置文件的位置（省略密钥值）、最近失败的证据位置和下一步。附件成功读取后记录其实际位置；继续任务时先读这段记录，直接恢复具体文件与动作。

## 读取运行

Observation 提供 status、list_threads、thread_tail、原文范围、result_phases/result_page、resource_usage、action_summary。实时诊断与已完成步骤分别读取。固定历史视图通过 prepare_complete 准备；巨大原文使用引用完整续读，保留总数与继续位置。Memory/Thread/业务 SQL 与工作区引用在同一个完整点恢复。

分析开始加载 society0-workbench，按其起步指引接入当前工作台；将解释正文与来源引用走资料接入路径交付，并核实更新回执。配置变更生成新版本；既有授权覆盖的构建、运行和重复自动推进，改变实验问题、机制或费用范围时先确认。原始 Thread 的信息暴露与测量结果分别解释。

使用 Observation 和规范存储读取实际事实、原文与来源，沿 Thread ID 与事件序号引用。工作台已有可验证的来源定位时可复用；缺少内容时继续读取原始工件。使用引擎提供的接口处理压缩记录，保留实际输入、工具参数与返回值的对应关系。

## 解释一条实际过程

围绕实验问题选取有解释价值的主体与时段，沿“冲击或条件变化 → 实际可见信息 → 主体选择与动作 → 环境兑现 → 下游反馈”组织材料。对照设计阶段预期的作用路径，说明哪些环节实际出现、在哪里中断、哪些尚缺记录。

每一环引用运行、业务时点、主体及事件/Thread 位置。主体自述说明其表达的理由；行动回执和权威事实说明实际做成了什么。拒绝、等待、未行动与未完成保留原状；缺少环节时呈现缺口。检查后续主体确实获得什么信息，按实际先后连接反馈。

例如采购申请被库存约束拒绝时，先说明请求、拒绝依据及后续可见信息，再检查是否换供应商或减少生产。把“申请采购”与“已交付”分开，沿记录解释这条路径实际走到哪里。

关联世界比较时注明各世界的初始化与完成范围、比较时点及仍混合的因素。过程材料用于解释运行中的发生顺序；对机制贡献的判断依赖实际对照和其他证据，沿用下文分析方法。给工作台交付过程摘要与可追查引用，图表、回放和联动由其 Skill 承担。

用户因此提出补主体或新增机制时，转入[设计修订](design-revision.md)，保留本次原始结果。

## 失败诊断

`IncompleteActivation` 是运行层摘要。先用 `Observation(run_dir).action_summary()` 找到失败主体与终止原因，再用 `list_threads`、`thread_tail` 读取该主体最后一次 provider response、前面的 request 和工具回执。大记录沿 `payload_ref` 完整读取；诊断使用 live 数据，保留 `complete_through` 与未完成事件的区别。先记录具体 `finish_reason`、错误内容和事件位置，再决定检查哪段源码。

例如上游返回 `content_filter` 时，报告实际过滤状态及记录位置；空白文本探针成功无法验证该次完整请求。工具返回空动作目录时，先核对主体收到的实际目标引用，避免让模型反复猜测目标或路径。未完成步骤里的动作属于诊断事实，完成步骤与正式结果另行确认。

动作分布单一、无人转发、关系未变化等可能就是本次观察结果。展示这些真实分布，并检查是否符合原研究设定。为了增加动作种类而加入“优先评论”“不要重复发帖”等提示，会改变行为条件；向研究者说明这种改动并确认后，再建立新版本。界面缺少某类事件时显示空态即可。

## 解释成本

区分模型端等待、记忆检索、领域计算、权威存储与展示导出；资源用量的未知值不推算为零。Activation、工具和物理调用计时有包含关系，避免重复累加。范围读取和按需字段保留完整内容的继续访问路径；显式全人口分析与全图构建仍按请求规模计成本。

## Quantitative Analysis

统计前分清初始投放、主体动作尝试、成功调用和实际新增事实。种子帖计入帖子总量，单列于主体发帖；同一主体重复点赞可能返回成功而不新增点赞关系，报告时把调用次数与形成的点赞关系分开写。核对分类合计与总数，正文引用脚本的同一份汇总结果。模型物理请求、重试与主体激活分别计数；记忆存量与累计召回条次分别报告。

跨轮统计同样要分清三类内容：长期记忆（每轮提炼的短句，注入上限为 `recall_top_k`）、当轮重新感知的帖子状态（平台在该轮激活时重新读出的正文与赞/评论数），以及上一轮会话原文（不进入本轮输入）。主体在后一轮提到前一轮的细节，不能直接当作“记得对话原文”，需先核对当轮召回条目与当轮帖子正文。

Anchor each explanation to the comparison actually made. Report group sizes, observed values and uncertainty before proposing a mechanism. Agent reasons are self-reported explanations that suggest hypotheses; repeated mentions of one cue do not establish that it caused the outcome or suppressed another cue. A small or absent difference also leaves multiple explanations open.

For each proposed follow-up, state the target factor, what varies, what stays fixed, and which contrast tests the claim. For example, comparing two messages both forwarded by a familiar person while changing official endorsement tests the endorsement condition. To study familiarity at a fixed endorsement level, compare familiar and unfamiliar senders under that same level; to study how the two factors interact, cross both factors. Choose follow-ups to distinguish explanations, including a possible null result. Execute repetitions already covered by the approved plan; obtain agreement for new experimental factors, scope or spending.

Keep the underlying message content fixed when isolating a source cue. Adding an official debunk changes both the authority cue and the corrective information; that comparison measures a combined treatment, not an isolated familiarity or authority effect.

Before handing off an analysis, recompute group totals and derived estimates, and check that each claimed inference follows from its comparison. A rule baseline needs a justified substantive meaning; exceeding an arbitrary fixed score does not establish a reasoning mechanism. An effect disappearing in a repeat weakens evidence for stability, without by itself proving the original difference was noise. State the uncertainty and competing explanations that remain.

Typical checks:

- trends over ticks.
- treatment/control differences.
- persona or group differences.
- missing/failed agent calls.
- variance across repeated runs.
- for recommendation experiments: active pool size, pruning thresholds, scoring weights, final displayed post count, and exposure/impression counts.

### 读取完整步骤的指标与表

下面代码可在完成[安装验收](installation.md)后直接运行，沿用 `SOCIETY0_STUDY` 环境变量，读取 `environment-check/resumed` 的第 2 步。分析其他运行时修改 `run_dir`、`step` 与 `table_name`。LLM starter 的运行位于 `$SOCIETY0_STUDY/versions/v001/runs/pilot-001` 或 `resumed-001`，表名为 `responses`；先通过阶段结果头部的 `tables` 查看实际表名。代码准备一个选定完整步骤的只读目录，逐页读取结果，大行按引用拼接完整 JSON，并保留阶段序号。显式分析全部所选行的成本随行数与原文大小增长。

```python
import base64
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from society0.kernel.observation import Observation
from society0.kernel.storage import StageStore

run_dir = Path(os.environ['SOCIETY0_STUDY']) / 'environment-check' / 'resumed'
step = 2
table_name = "decisions"


def values(reader, reference):
    cursor = None
    while True:
        page = reader.result_page(reference, cursor=cursor)
        for item in page['items']:
            if 'value' in item:
                yield item['value']
            else:
                body = bytearray()
                offset = 0
                while True:
                    part = reader.read_result_record(item['payload_ref'], offset=offset)
                    body.extend(base64.b64decode(part['data']))
                    offset = part['next_offset']
                    if offset is None:
                        break
                yield json.loads(body)
        cursor = page['next_cursor']
        if cursor is None:
            break


metric_rows, table_rows = [], []
with TemporaryDirectory() as temporary:
    view = Path(temporary) / 'complete'
    with StageStore.prepare_readonly(run_dir, view, step=step):
        pass
    with Observation(view) as reader:
        cursor = None
        seen = 0
        while True:
            phases = reader.result_phases(step=step, cursor=cursor)
            for phase in phases['items']:
                identity = {'step': step, 'phase': phase['name'], 'phase_index': phase['ordinal']}
                header = next(values(reader, phase['reference']))
                metric_rows.extend({**identity, **metric} for metric in values(reader, header['metrics']))
                if table_name in header['tables']:
                    table_rows.extend({**identity, 'table': table_name, 'value': value}
                                      for value in values(reader, header['tables'][table_name]))
            seen += len(phases['items'])
            if seen == phases['total']:
                break
            cursor = phases['cursor']

print('指标：', metric_rows)
print('表行：', table_rows)
```

需要 DataFrame 时，可在研究环境安装 pandas 后将 `metric_rows` 或 `table_rows` 传给 `pandas.DataFrame`。`value` 保留表行的原始 JSON，包括空值、布尔值、数组和对象；按实际研究表结构再选择字段。大型分析也可逐页消费 `values`，避免把所有行同时放进列表。

## Qualitative Analysis

LLM-agent simulation often produces important qualitative material:

- stated reasons.
- generated comments.
- memories cited.
- interview answers.
- failed or surprising cases.

Code outputs can be coded inductively, but the researcher should review categories. Do not outsource substantive interpretation entirely to another LLM without audit.

## Report Shape

Recommended report:

1. 具体实验问题及世界比较的口径。
2. Simulation design: agents, environment, providers, steps, conditions.
3. Measurements and output schemas.
4. Quantitative results.
5. Qualitative patterns.
6. Robustness checks.
7. Limitations and next run.

Always mention model/provider dependence, prompt sensitivity, and the distinction between simulation outputs and empirical observation.

For `social_network` recommendation studies, explicitly report the recommendation condition: `full_scan_until`, pruning settings, chronological/engagement/similarity/network weights, whether embedding similarity was enabled, and `post_count`. These settings shape exposure and should be interpreted like experimental design choices.
