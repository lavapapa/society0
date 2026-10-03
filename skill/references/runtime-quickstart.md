# 首次实验的技术起步

AI 在第一次编写或运行实验时读取本页；研究者继续用自然语言讨论研究问题。本页配套的 `../assets/minimal_experiment.py` 包含真实 LLM 动作、FoV、显式保存经验、次轮测量和状态声明。复制到实验版本目录后按研究设计改动，保持每项机制的含义明确。

## 先确认现有环境

在现有 Python 环境中检查 `society0` 的版本、安装路径及本次要调用的接口。确认 Python ≥3.12、LLM 与 embedding 两套模型配置。安装 skill 提供的是指导和模板；实验还需 Python 引擎。导入失败的结论限定于该解释器；接着检查项目已有虚拟环境或环境管理器登记的环境，找到适用安装后复用。确实缺少或接口不匹配时再安装或更新。

```python
import inspect
import importlib.metadata
import society0
from society0.schedule import AgentGroup

print(importlib.metadata.version("society0"), society0.__file__)
for name in ("instruct", "interview", "extract_thread_memories"):
    print(name, inspect.signature(getattr(AgentGroup, name)))
```

需要安装引擎时，在选定解释器中从 Society0 源码安装。复用已有 Society0 仓库；若只有 skill，则从 `https://github.com/lavapapa/society0` 克隆源码到项目中的持久目录，再用选定解释器执行 `python -m pip install -e /absolute/path/to/society0-source`。可编辑安装持续依赖该源码目录，应与实验一起保留。下载长时间无进展时先看进程、连接和代理，避免在同一环境重复启动安装。用户取消回复后，也核对后台安装或实验进程是否仍在执行。

## 起步配置与两轮协议

自定义 `environment.state` 的字段在 `environment.state_schema` 中声明；Agent 的 `state` 字段在其 `agent_types` 的 `state_schema` 中声明。每个保存单元带 `persistence`，例如可替换数值或短文本用 `{"kind":"replaceable"}`，持续追加的记录列表用 `{"kind":"append_only_list"}`。容器整体声明保存方式即可覆盖子树；根据真实写入方式选择，细节见 `environment-design.md`。Agent state 会进入提示词，研究条件标签放在 `properties` 或分析资料中。

`engine.run(steps=2)` 执行两个 tick，每个 tick 会执行全部注册步骤。两阶段实验可在一个步骤中按 `ctx.step == 0` 分支：第一轮暴露与动作，第二轮测量。不要把两轮各写一个无条件步骤后再运行两 tick，以免将模型操作翻倍。

当前 CodeSchedule 接口的记忆检索参数为 `retrieve_memory`。保存一轮经验需显式打开线程、传入 `thread_ids_by_agent`，交互成功后调用 `extract_thread_memories(...)`；该操作在原会话上提炼并写入记忆。配套 Python 示例给出了完整流程。`interview(...)` 读取已有记忆并测量，默认保持测量内容不写入记忆。参数说明见 `step-dsl.md`；以实际安装包的签名为准。

配套示例成功完成记忆提炼后会关闭显式线程，后续测量另建交互记录。修改研究字段时保留这段生命周期。提炼失败时检查返回状态和线程状态；可重试的记忆提交失败可能保留线程，应按记忆接口的约定处理。若已确认研究设计是独立的单次判断、没有后续经验检索，才按该设计省略记忆；手动打开的线程需在交互结束后用 `ctx.log.close_agent_thread(thread_id)` 收尾。仍处于打开状态的线程无法进入可恢复检查点，已有评分也不足以判定整个运行成功。

起步示例测量对消息的判断，因此第二轮通过 FoV 再次呈现同一条消息，记忆用于补充此前经验。若研究问题涉及遗忘，需要另行设计允许“未记住／无法评价”的测量字段与缺失值处理，并核对实际检索结果。提炼成功可能返回空记忆；记忆检索异常也可能留下空结果，技术成功和测量有效性分别检查。每次创建引擎传入 `copy.deepcopy(config)`，使同一进程内的多次运行各自从独立初始状态开始。

首项小实验可以用一个简单、平坦的 Pydantic 测量模型。输出包含嵌套模型时，先验证生成的工具 schema 与当前引擎、模型端点兼容，再扩大结构。每次模型操作检查 `error_count` 和 `error_samples()`，防止 tick 执行完成掩盖 Agent 测量失败。

步骤结果写入普通数据。例如 `copy.deepcopy(ctx.env.state["detail_views"])` 将当前状态代理转成独立列表；对评分等标量直接取值。把运行时代理放进表格或跨 tick 留存，会把状态访问和结果序列化混在一起。当前状态代理支持标准库 `copy.deepcopy`，无需另写转换器。

## 检查、连接验证与试运行

先检查配置能初始化，再用小规模真实试运行验证模型服务与实验过程。配套示例支持：

```bash
python versions/v001/experiment.py --check --run-dir versions/v001/runs/check-001
python versions/v001/experiment.py --run-dir versions/v001/runs/pilot-001
```

以上路径相对于实验项目根目录。宿主把命令转为后台任务时，显式指定该工作目录，或同时使用解释器、实验文件和输出目录的绝对路径。先确认进程已进入实验并产生本次记录，再报告“试运行已开始”。

`--check` 调用真实 `engine.run(steps=0)`，检查初始状态、保存声明与注册步骤，生成零 tick 的检查记录。这一步没有验证实验分支中的全部调用参数、工具行为或服务连通性。核对本次使用的接口签名后，用配套的小规模真实试运行同时验证 LLM 动作、结构化测量、记忆写入与 embedding。研究者提供的渠道和向量维度已核实且费用估算适合该小试验时，可直接进入试运行，省去另写连接探针。维度未知或真实运行出现连接问题时，再按具体错误做最小核验。检查记录和试运行使用不同目录；模型调用已经发生的失败运行也保留原目录，用新 run ID 重试。

每次调用模型前，在该 run 目录保存本次实验定义和非敏感的生效参数，例如 `experiment-source.py` 与 `config-used.json`。修复实现问题后为新尝试选新 run ID 并再保存源码；改变 persona、FoV、动作、记忆或测量规则时创建新配置版本。按 `run-monitor-analyze.md` 分析时用各次运行自己的源码和参数，保留成功与失败记录。

核对条件标签是否影响 Agent 时，检查实际注入的提示词、FoV 和工具返回。目录名、版本名和研究者侧的分析标签用于组织资料，它们出现在运行元数据中不等于 Agent 接触了该标签。发现问题后保留原记录，按实际暴露路径判断是否需要新版本或新试运行。

示例读取 `SOCIETY0_LLM_MODEL`、`SOCIETY0_LLM_BASE_URL`、`SOCIETY0_LLM_API_KEY`、`SOCIETY0_EMBED_MODEL`、`SOCIETY0_EMBED_BASE_URL`、`SOCIETY0_EMBED_API_KEY`、`SOCIETY0_EMBED_DIMENSIONS`。AI 可从研究者提供的本地配置文件加载到启动进程，不打印配置文件、密钥或密钥片段。embedding 维度采用实际模型值；示例设 `send_dimensions=False`，使用服务原生维度。代理沿用用户已有环境，按真实请求结果核对 LLM 与 embedding。

费用估算跟随研究者选择，按 `researcher-onboarding.md` 计算，包含动作多轮调用、显式记忆提炼、测量和可能的重试。运行后从 `summary.json` 核对 `failed`、`steps_completed`、Agent 成功数、动作成功数、记忆提炼结果及实际 token 用量。按报价换算的金额标为“按此费率估算”；实际扣费需账户账单确认。

低成本起步优先减少 Agent 数量、条件数量和重复次数，保留完整实验过程。单次输出上限应容纳解释、工具参数和记忆提炼；首次试验沿用配套示例各阶段的上限，再根据成功记录调整。把上限压低到模型来不及完成输出会使调用失败，仍会产生费用。遇到输出被截断时，保留失败记录，调整对应阶段的上限并重新估价后再试。

## 静态工作台与解释

配置初稿即可提议创建工作台；采用 `workbench-guide.md`，为本实验写转换脚本，提取实际配置并保持每个版本独立。Python 配置模块同名时用 `importlib.util.spec_from_file_location` 按文件路径加载各版本，避免 `import config` 的缓存将多版配置全部变成第一版。模型凭据留在启动配置中，工作台呈现模型名称、参数与来源。

试运行完成后把已保存记录加入同一 HTML。优先整理研究者需要的指标、原文理由、真实会话、FoV 机制及其依据。生成后在研究者使用的浏览器里核对配置字段能显示和滚动、版本与运行切换正确、真实会话可读。重新生成文件后刷新页面；预览仍保留旧数据时关闭并重新打开。页面修改由顶部复制变更按钮整理，发送给 AI 后才能生成新的实验版本。

单次小实验用于检查刺激、工具、记忆和测量是否按设计发生。理由文本提供分析线索；机制归因还需对照、重复运行或消融。表述判断时区分已观察到的行为、可能解释与下一步检验，不把理由文本当成机制证明。
