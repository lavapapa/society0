# 从完整运行生成静态工作台

工作台把已保存的配置、阶段结果和主体 Thread 放入可离线打开的 HTML。先完成[双机制对话](../tutorial/conversation.md)或准备自己的正式运行，确认要展示的完整步骤及主体 ID。实时诊断使用[观察接口](../tutorial/observation.md)。

## 一、选择并导出运行

下面命令假设对话运行位于 `runs/conversation/run`，主体是 a、b、c、d，并已完成步骤 1、2。先读取状态，再导出选定范围：

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

在浏览器打开生成的 HTML。界面数据来自选定完整步骤，稍后运行继续推进时页面保持这次导出的范围。重新导出即可生成新的展示快照。

## 二、核对页面与来源

先选同一个版本和运行，查看阶段指标、表格、主体激活与会话。对照[结果读取](../tutorial/results.md)中的原始值，至少核对一条表记录及一条 Thread 事件的步骤、主体和业务时间。规则运行可以没有模型会话；空页面部分应与真实保存内容对应。

导出器读取阶段表的分页和大行原文，保留完整 JSON 与来源。曲线使用所选完整步骤的数值指标；非数值、布尔值以及超出 JavaScript 精确整数范围的值继续保留在原文中。主体历史输入按实际事件前缀呈现，较晚步骤的会话不会进入较早步骤。

## 三、比较多个配置版本

多版本使用 Python 导出接口。下面是完整转换程序，先将路径和选择替换为实际已有运行；两个版本可具有不同配置。

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

再将 `comparison.json` 交给同一个渲染器。同一版本下的公开配置与实体选择应一致；配置条件改变时使用不同版本标识。保持共同时间范围并核对实验来源，避免把不同终点的累计结果直接比较。

## 四、控制导出范围

导出会准备一次所选最大完整点的只读视图，并物化选中的结果与 Thread，临时空间、内存及 HTML 大小随原文规模增长。先选短时间和少量主体检查，再按阅读需要扩展；大型连续分析使用 Observation 的分页读取。

外部二进制工件保留引用，正文不会自动嵌入 HTML。需要完整归档时同时交付[运行导出](storage.md)，并检查展示数据中是否包含不适合分享的主体资料和提供方原文。

工作台的配置编辑整理成待核对请求，由研究者审核后在新版本配置中应用；静态页面不直接启动仿真。适配器和渲染边界见[工作台参考](../reference/workbench.md)与 [export_payload 源码](https://github.com/lavapapa/society0/blob/@COMMIT@/src/society0/kernel/workbench.py)。
