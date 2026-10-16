# 从完整运行生成静态工作台

要把某次运行的配置、阶段结果和主体 Thread 拿给别人看，或者留一份能离线打开的记录，就用工作台导出 HTML。先完成[双机制对话](../tutorial/conversation.md)，或者准备自己的正式运行，并确认要展示哪些完整步骤、哪些主体 ID。实时诊断走[观察接口](../tutorial/observation.md)。

## 选定范围并导出

下面的命令假设对话运行在 `runs/conversation/run`，主体是 a、b、c、d，步骤 1、2 已经完成。先读状态，再导出选定的范围：

```bash
uv run python -m society0.kernel.observation runs/conversation/run
uv run python -m society0.kernel.workbench \
  --run runs/conversation/run --version baseline \
  --steps 1 2 --actors a b c d \
  --output runs/conversation/workbench.json
uv run python skill/scripts/render_workbench.py \
  --data runs/conversation/workbench.json \
  --output runs/conversation/workbench.html
```

用浏览器打开生成的 HTML。页面里的数据来自选定的完整步骤，之后运行继续往前走，页面仍停在这次导出的范围上；重新导出一次，就是一份新的展示快照。

## 把页面和来源对一遍

先选好同一个版本和运行，再看阶段指标、表格、主体激活和会话。拿[结果读取](../tutorial/results.md)里的原始值对照，至少核对一条表记录和一条 Thread 事件的步骤、主体和业务时间。规则运行可能没有模型会话，页面上空着的部分应该和真实保存的内容对得上。

导出器会读阶段表的分页和大行原文，把完整 JSON 和来源一起留下。曲线用的是所选完整步骤里的数值指标；非数值、布尔值以及超出 JavaScript 精确整数范围的值仍然留在原文里。主体历史输入按实际事件的前缀呈现，后面步骤的会话不会跑进较早的步骤。

## 把几个配置版本放在一起比较

要放多个版本，用 Python 导出接口。下面是完整的转换程序，先把路径和选择换成你实际已有的运行；两个版本的配置可以不同。

```python
import json
from pathlib import Path
from society0.kernel.workbench import RunSelection, export_payload

payload = export_payload([
    RunSelection("runs/baseline/run", "baseline", (1, 2), ("alice", "bob")),
    RunSelection("runs/treatment/run", "treatment", (1, 2), ("alice", "bob")),
], title="资料呈现对照", question="不同资料呈现怎样影响主体判断？")
Path("comparison.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
```

再把 `comparison.json` 交给同一个渲染器。同一个版本里的公开配置和实体选择应该保持一致；配置条件变了就换一个版本标识。两边要保持共同的时间范围，并核对实验来源，不同终点的累计结果不要直接比较。

## 控制这次导出多大

导出会为所选范围内最大的那个完整步骤准备一次只读视图，并把选中的结果和 Thread 物化出来，临时空间、内存和 HTML 大小都随原文规模增长。先用短时间和少量主体试一次，再按阅读需要往外扩；要连着看很多数据，就用 Observation 的分页读取。

外部的二进制工件只保留引用，正文不会自动塞进 HTML。要完整归档就同时交付[运行导出](storage.md)，并检查展示数据里有没有不适合分享的主体资料和提供方原文。

在工作台里改的配置会整理成待核对的请求，由研究者审核后应用到一个新版本的配置里；静态页面不会直接启动仿真。适配器和渲染边界见[工作台参考](../reference/workbench.md)与 [export_payload 源码](https://github.com/lavapapa/society0/blob/@COMMIT@/src/society0/kernel/workbench.py)。
