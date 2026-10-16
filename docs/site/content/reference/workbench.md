# 工作台导出

`from society0.kernel.workbench import RunSelection, export_payload`。导出器读取正式运行产物中明确选择的完整步骤与主体，生成静态工作台数据；适合离线展示、对照和检查原文。选哪些运行、版本、步骤和主体由调用者决定；导出只做读取，运行产物保持原样，返回的数据由调用者保存成文件。

## 选择展示范围

每个 RunSelection 指向包含 runner 清单的正式运行目录，并明确展示版本名、步骤和主体。

<!-- api: society0.kernel.workbench.RunSelection -->
<!-- api: society0.kernel.workbench.export_payload -->

export_payload 返回含 study 和 versions 的字典。steps 必须非空且为非负整数，重复步骤或主体被拒绝。相同展示 version 下的配置和实体选择须一致；不同运行可合入同一版本，差异配置使用不同名称。空 selections 返回真实空态。

每个运行物化一次所选最大完整步骤，再读取已保存 Results 和对应事件前缀。Thread 按主体、Moment 与 publish_step 定位，因此同一时点后来追加的消息不会进入较早步骤。运行与 runner 身份不符、完整链缺失、选择结果不可读时导出失败。

## 展示范围

环境视角展示阶段指标、表和结果头；主体视角展示实际激活结果及完整 Thread 事件。原请求保持水位引用，避免把每次请求历史再次展开。数值指标形成所选步骤曲线，保留步骤与业务时间；非数值和超出 JavaScript 精确整数范围的值仍可在 JSON 原文中读取。

行动与物理用量累计标在所选最大完整步骤，主体共享批次仍采用不可相加的归属口径。导出保留运行、步骤、阶段、Thread 和事件序号来源。领域 World/FoV 的重建通过相应领域接口表达，通用导出器按正式结果读取。

静态 HTML 包含选定展示数据及界面代码，可离线打开。配置编辑生成待核对的修改请求，启动实验和保存新事实通过正式运行入口执行。外部工件引用原样保留，二进制正文不自动嵌入 HTML。

## 调用与成本

以下代码只做选择并离线读取，path 应指向实际完整运行：

```python
from pathlib import Path
from society0.kernel.workbench import RunSelection, export_payload

def selected_payload(path):
    return export_payload(
        [RunSelection(Path(path), "方案A", (1, 2), ("alice", "bob"))],
        title="对照研究", question="两轮中发生了哪些行动？",
    )
```

CLI 可用 `python -m society0.kernel.workbench --run RUN_DIR --version NAME --steps 1 2 --actors alice bob --output data.json` 生成数据，再用仓库渲染器生成单 HTML，步骤见[工作台指南](../guide/workbench.md)。

内部读取 Results 时分页，大记录按范围重组；最终仍会物化全部选定结果与 Thread 原文，内存及 HTML 大小随选择范围增长。临时完整视图在转换后清理，准备期间需要数据库及工件空间。持续大规模读取使用[Observation](observation.md)，完整归档使用[存储生命周期](storage-lifecycle.md)。源码入口为 [export_payload](https://github.com/lavapapa/society0/blob/@COMMIT@/src/society0/kernel/workbench.py#L141)。
