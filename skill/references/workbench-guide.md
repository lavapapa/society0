# Society0 静态实验工作台

实验配置已有初稿，或者已有保存结果，且研究者选择创建工作台后，先编写该实验的数据转换脚本，再用 `../scripts/render_workbench.py` 把转换后的数据写入 `../assets/workbench-template.html`，产出一个静态 HTML 文件。工作台可在首次试运行之前展示配置，随后把各配置版本的试运行结果纳入同一页面。页面中的配置修改只是暂存提议；研究者复制变更并发送给 agent，经核对后才会修改实验文件。数据或配置版本有更新时，需要重新执行转换并生成文件。页面不连接运行中的实验，也不负责启动、暂停或调整实验进程。

页面沿用三栏：左侧按版本列出 Agent、环境与制度等视角；中间在配置模式下显示字段检查，在结果模式下默认显示选中 Agent 当前 tick 的 FoV 机制及专题标签；右侧在配置模式下显示待发送变更，在结果模式下默认显示 Agent 会话。顶部先选配置版本，再选该版本下的试运行；选择“配置视图”即可回到版本配置。可按研究需要修改模板的样式和组件。

## 生成流程

为每项实验保留一个可重复执行的数据转换脚本，例如 `analysis/build_workbench.py`。脚本读取各版本实际使用的配置和各版本下已有的试运行产物，按下面的数据格式生成 `workbench-data.json`，再调用 skill 中的 HTML 生成脚本。尚无试运行时，`runs` 写空数组。资料与呈现逻辑由实验自己的转换脚本决定；通用生成脚本只负责检查基本结构并把 JSON 嵌入单文件模板。

```text
各版本的实际配置 + 各版本下已保存的试运行结果（可以为空）
  → 实验目录中的 build_workbench.py
  → workbench-data.json
  → skill/scripts/render_workbench.py
  → workbench.html
```

生成 HTML 的脚本这样调用：

```bash
python skill/scripts/render_workbench.py \
  --data experiments/study/analysis/workbench-data.json \
  --output experiments/study/workbench.html
```

上例从 Society0 仓库根目录执行；skill 安装在其他目录时，用该 skill 的实际路径替换脚本路径。

可以用 `--template` 指向按本实验修改过的 HTML 模板。若添加了专用组件，把改动留在该模板副本中，重新生成时便不会丢失。将生成 HTML 的步骤写入 `build_workbench.py`，让更新工作台只需执行一个实验脚本。交付给研究者的是生成后的 HTML；浏览版本、试运行、tick 和主体仅会切换该文件内已保存的数据。页面暂存的配置修改在刷新或关闭后可能丢失，务必复制并发给 agent；复制动作本身也不会保存实验文件。

更新通用模板时，保留实验数据转换脚本与原始记录，确认数据格式仍适用后采用新版模板并重新生成页面。有专用组件时在其可读源码中核对和整合改动；压缩 HTML 的逐字符比较可能耗时很长，选择结构、具体样式规则或组件源码来定位差异。遇到长时间比较或取消回复后仍在执行的命令，核对并停止该次后台任务，再继续生成；更新模板沿用已保存资料即可。

## 版本目录与变更回传

强烈建议在单个研究项目内按配置版本组织文件，让试运行归属于生成它的版本：

```text
experiments/study/
  versions/
    v001/
      experiment.py
      runs/
        pilot-001/
          summary.json
          steps.jsonl
    v002/
      experiment.py
      runs/
  analysis/
    build_workbench.py
    workbench-data.json
  workbench.html
```

版本目录名称可以按项目习惯调整。若配置分散在 Python 代码、JSON、环境定义和模型参数中，转换脚本应提取该版本的实际配置快照，写入 `version.config`，并在 `configSource` 标明真实来源；不要只复制一份与实际运行脱节的展示文件。每个版本的 `entities`、`runs` 都写在该版本内，未运行版本也可以独立检查。目录规范由 skill 强烈建议，不要求 Society0 运行时强制执行；已有项目可保留自身结构，只要转换脚本准确表达版本与运行归属。

收到 `society0_config_change_request` 时，agent 先核对 `baseVersionId`、`configSource` 和每条 `before` 与当前源文件是否一致，再按 `op`（`add`、`remove`、`replace`）判断 `after` 是否符合实验约束。若源文件与页面快照已有差异，先向研究者说明冲突；若变更成立，创建新的版本目录并修改其中的实验定义，保留旧版本及其运行结果，重新提取配置、生成工作台。JSON 路径指向工作台提取出的配置树；当源配置在 Python 中时，agent 负责把路径映射回真实代码。除非研究者另行要求，不自动启动试运行。

复制出的请求例如 `{"type":"society0_config_change_request","baseVersionId":"v001","configSource":"versions/v001/experiment.py","changes":[{"op":"replace","path":"/agents/0/persona","before":"谨慎读者","after":"热心读者"}]}`。研究者可以在粘贴后附上原因或补充要求；agent 仍需以实验文件为准核对，不把浏览器里的草稿当作已生效配置。

同名 Python 配置模块按各自绝对文件路径加载，例如用 `importlib.util.spec_from_file_location(f"config_{version_id}", path)`；先确认导入配置不会启动实验。逐版本核对实际字段，避免模块缓存把后续版本全部读成第一版。初始化检查的零 tick 记录标为“配置检查”，失败记录标明失败及其原因，完成的试运行才提供相应结果。根据 `summary.json` 的 `failed`、`steps_completed` 和 Agent 批次结果判断，目录存在本身不足以判断成功。

## 首次交付给研究者

首页标题写研究项目名称，标题下写一句研究问题。左栏先放研究者最关心的主体，也可放“实验整体”“环境”“制度实体”等视角；各主体标明类型。配置版本尚无运行时，中间默认展示所选视角对应的配置树，研究者可改动字段，必要时展开完整 JSON 编辑器增删字段；右栏显示待发送变更。已有保存结果时，为 LLM Agent 准备默认的“可接触的信息”视图，按机制说明它在当前 tick 能接触哪类资料；右侧优先展示该 Agent 在当前 tick 的真实会话。没有记录时显示空状态，缺少依据的标签不添加。无论是否已有运行，顶部都要明确显示当前版本和试运行范围。

向第一次打开页面的研究者，简短介绍三种阅读路径：先选配置版本，检查主体和环境设定；如想提议修改，编辑字段并复制顶部变更请求发给 agent；如有试运行，切换到该版本的试运行，沿 tick 查看机制、会话与变化。对复杂机制的解释，可补充几个“你可能还想看”的入口，指向相关主体、相邻 tick 或专题视图。页面中的说明应围绕研究问题，不使用界面实现术语代替解释。

## 转换的数据格式

转换脚本输出的 JSON 有 `study`、`versions` 两个顶层字段。HTML 生成脚本会替换模板中的 `society0-workbench-data` 数据块，并处理 JSON 中可能出现的 HTML 结束标签。版本 ID 在整个工作台内唯一，其余 ID 在同一版本、同一次运行等各自范围内保持唯一。`config` 是可序列化的完整配置树；`configPath` 是该观察对象在 `config` 内的 JSON 路径，省略时展示整个配置树。若路径穿过数组，给数组元素保留稳定的 `id`，以便研究者调整数组顺序后仍指向同一主体。环境参数、Agent 字段和实验条件都可放进同一配置树，转换脚本不要擅自遗漏需要研究者核对的字段。

```json
{
  "study": {"title": "研究项目名称", "question": "这项研究要解释什么？"},
  "versions": [
    {
      "id": "v001", "name": "初稿", "configSource": "versions/v001/experiment.py",
      "config": {"agents": [{"id": "agent-a", "persona": "..."}], "environment": {"type": "plain"}},
      "entities": [
        {"id": "agent-a", "name": "主体 A", "kind": "llm-agent", "group": "参与者", "configPath": "/agents/0"},
        {"id": "environment", "name": "环境", "kind": "overview", "group": "实验设定", "configPath": "/environment"}
      ],
      "runs": [
        {"id": "pilot-001", "name": "试运行 1", "ticks": [{"id": "1", "label": "Tick 1"}],
         "snapshots": [{"tickId": "1", "entityId": "agent-a", "mechanisms": [], "tabs": [], "sessions": [], "sideTabs": []}]}
      ]
    }
  ]
}
```

`kind` 可用 `llm-agent`、`rule-agent`、`institution`、`overview`，也可按研究需要扩展。每个 snapshot 归属于一个配置版本下的一次试运行，并对应一个 tick、一个观察对象。切换时只读取完全匹配的 snapshot，缺失的记录保持空白；不要把最终状态填到早期 tick，也不要跨版本或试运行复制数值。非 LLM 实体可以有中间专题标签和右侧事件标签；工作台不会替它生成 Agent 会话。

## FoV 机制与专题模块

`mechanisms` 中每项对应一种 FoV 机制。机制卡片的 `access` 取 `available`、`unavailable` 或 `unknown`，分别表示已确认当前可接触、已确认当前不可接触、尚无足够依据。填写 `description` 说明机制能提供哪类资料，`reason` 说明当前状态下的适用条件，`source` 写实验定义或状态记录的具体出处。只看到能力目录登记时，使用 `unknown`；只有确认主体、条件和 tick 的适用规则后，才使用 `available`。

```json
{
  "id": "fov-example",
  "name": "机制名称",
  "access": "unknown",
  "description": "这个机制能提供哪类资料。",
  "reason": "当前主体与 tick 的适用条件尚待核实。",
  "source": "实验定义中的 FoV 注册与调用条件",
  "views": [
    {"id": "explain", "title": "机制说明", "type": "text",
     "paragraphs": ["机制提供资料的范围与限制。"]}
  ]
}
```

默认机制卡片展示信息边界。会话中真正执行过的 FoV、注入的具体资料及 Agent 后续动作，需要依据真实交互记录，在会话或专题标签中单独说明。机制 `views` 可以有多个标签，用不同方式呈现同一机制的结构或相关记录。研究者需要的其他内容放在 `tabs`；每个标签含 `id`、`title` 和 `modules`。模块含 `id`、`title`、可选 `description`、`source`、`wide` 与 `views`。右侧 `sideTabs` 采用同样的标签与模块结构；没有资料支持时直接省略。

每个 `view` 都有 `id`、`title`、`type`。预设呈现方式如下，agent 可按资料性质选择，也可以扩展：

| type | 主要字段 | 适用资料 |
| --- | --- | --- |
| `text` | `paragraphs: [字符串]` 或 `text` | 机制解释、定性记录 |
| `metric` | `items: [{label, value, unit?, note?}]` | 少量关键数值 |
| `timeseries` | `rows: [{x, y}]`、可选 `unit` | 随时间变化的单变量 |
| `candlestick` | `rows: [{x, open, high, low, close}]`、可选 `unit` | 具有开、高、低、收语义的序列 |
| `network` | `nodes: [{id, label, description?, x?, y?}]`、`edges: [{from, to, label?}]` | 主体或概念之间的关系；可选坐标取 0 到 1 |
| `table` | `columns: [{key, label}]`、`rows: [对象]` | 较长的事件、观察或结果清单 |
| `custom` | `renderer` 及自定义字段 | 预设组件无法恰当表达的资料 |

`table` 只渲染滚动视口附近的行；全部行仍在 HTML 的 JSON 中，文件大小和浏览器解析时间会随数据增长。K 线和时间序列默认展示最近 500 个时点并标明截取数量，可用 `maxPoints` 调整。数据量很大时，先按研究问题筛选或汇总，再决定是否开发分页、分层网络等更合适的组件。K 线需要真正的开、高、低、收含义；普通时间序列使用 `timeseries`。

自定义组件可以直接放在 HTML 的 `window.SOCIETY0_RENDERERS = {}` 代码块中。例如，把 `window.SOCIETY0_RENDERERS.matrix = (element, view, context) => { ... }` 定义为渲染函数，view 写 `{"type":"custom","renderer":"matrix"}`。函数接收容器、当前 version／run／tick／主体／snapshot；如需清理事件监听或图形资源，可返回清理函数。agent 可以查询和使用更适合的可视化方式，不受预设类型限制；需要离线单文件交付时，将依赖和样式一并放进 HTML。

## 会话与资料来源

`sessions` 中每次触发单独一项，`id` 唯一，`label` 建议包含交互名称和顺序。右栏会按这些真实会话给出选择菜单。会话的 `events` 依次填写输入、可见输出、动作或工具、观察结果、错误等记录；每项可用 `kind`、`title`、`text`、`data`、`time`、`source`。只展示原始记录中可核实的内容，工具名写真实名称，不补写隐藏推理。没有会话的 tick 留空数组。

会话转换优先复用引擎的读取接口，它会取回存为独立 payload 文件的长消息。先从原始 Thread 首行获取 `thread_id`、`agent_id`、`scope` 和 `checkpoint_step`，核对 tick 映射，再按该线程创建一个 session。以下片段可放进实验转换脚本：

```python
from society0.agent.thread_store import AgentThreadStore

store = AgentThreadStore(run_dir, create=False)
messages = store.read_messages(thread_id)
events = []
for index, message in enumerate(messages):
    role = message["role"]
    events.append({
        "kind": {"system": "input", "user": "input", "assistant": "output", "tool": "tool"}.get(role, "observation"),
        "title": {"system": "系统提示", "user": "输入", "assistant": "Agent 输出", "tool": "工具返回"}.get(role, role),
        "text": message.get("content") or "",
        **({"data": {"tool_calls": message["tool_calls"]}} if message.get("tool_calls") else {}),
        "source": f"agent_threads / {thread_id} / message {index}",
    })
session = {"id": thread_id, "label": interaction_name, "events": events}
```

测量表和规则转发记录放入 `tabs` 或 `sideTabs` 的表格组件；`sessions` 用实际 LLM 会话。成功或失败的动作可从 `store.read_events(thread_id, materialize_payloads=True)` 中的工具执行记录补充。存在多个线程时按 `thread_id` 分开，保持原始顺序。取到的消息数量为零时展示缺少会话记录；已存在测量表仍可独立展示。将“仅有结果表”“完整会话”“截取的消息预览”等资料范围如实写在模块说明中。

数据来源按用途读取：`summary.json` 给实验概况和能力目录，`steps.jsonl`、`metrics.jsonl` 给研究者设计的表格与指标，checkpoint 给当前状态，`events.jsonl` 给事件线索，`agent_threads/` 给会话细节。`resource_calls.jsonl` 用于模型调用与用量，不能替代会话。FoV 能力目录是全局登记信息；需要结合实验代码、调用条件和当时状态，判断当前主体能否接触某机制。会话线程中的 `checkpoint_step` 与事件中的 tick 可能采用不同计数位置，映射时核对实际交互顺序与记录，不靠字段名直接相等。

完成后，检查版本、试运行、tick 和主体切换时三栏内容一致；无结果版本仍能展示完整配置；字段与完整 JSON 修改能正确显示差异、复制请求，且刷新前未写入源文件；机制的适用状态有依据；多次会话能逐一查看；非 LLM 实体和缺失数据没有虚构内容。然后在研究者常用的浏览器中直接打开生成的 HTML，检查页面宽度、长配置、长表格、长会话和键盘操作。原始配置或结果有变化时，重新运行转换脚本并刷新页面。

## 维护模板

可维护源码位于仓库的 `tools/workbench-template/`，使用 React、Vite 和原型的样式。需要修改预设组件或三栏交互时，在该目录运行 `npm ci`、`npm test`、`npm run build`，将 `dist/standalone/index.html` 复制到 `skill/assets/workbench-template.html`。普通实验更新数据时，修改或重跑实验的数据转换脚本，不必安装前端开发依赖。
