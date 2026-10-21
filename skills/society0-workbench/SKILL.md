---
name: society0-workbench
description: 为 Society0 及相关多主体研究创建、配置和刷新轻量 HTML 展示工作台。用户要求先打开空壳、查看配置或已有资料、装配组件、选择运行与平行世界、查看日历和主体、复制参数提议、定制面板或导出离线页面时使用，即使尚无实验和运行数据。负责呈现；实验设计、机制、运行与模型服务交给 society0。
---

# Society0 Workbench

先让用户看到可用页面，再随对话补充资料。展示数据与实验事实分开，mock 始终带可见标识。

## 开始

1. 读取 `references/quickstart.md`。确认已有工作台入口；从零时用随包空壳生成并打开 HTML，检查实际打开回执。无需等待插件、主体、世界或运行资料齐备。
2. 确认当前阅读任务和用户已给的资料。以可用目录选择组件，读取下表中的必要文件；空壳流程到此即可开始使用。
3. 按需装配页面和数据。展示组件配置、实验插件参数、浏览状态分别保存。将用户编辑留作草稿与复制提议。
4. 新资料到达后重新生成同一页面，通知刷新。恢复仍有效的选择和草稿；来源改变时显示冲突，保留原草稿供核对。
5. 按 `references/acceptance.md` 中本次路径检查页面，交付入口、资料范围、mock 状态及刷新方法。

## 按任务读取

| 任务 | 必要文件 |
|---|---|
| 从零打开 | `references/quickstart.md`、`references/empty-states.md` |
| 选择现成组件 | `references/catalog.md`，再读所选组件组 |
| 接入新资料 | `references/data-contract.md`、`references/import.md` |
| 更新已有页面 | `references/refresh.md`；资料结构变化时补读数据合同 |
| 编辑参数并复制 | `references/proposals.md`、`references/components/configuration.md` |
| 运行、世界、日历 | 对应 `references/components/runs-worlds.md` 或 `time-calendar.md` |
| 组织场景页面 | 所选 `references/recipes/` 配方及其中引用的组件组 |
| 新增组件 | `references/custom-extension.md` 及最接近的组件组 |
| 离线交付 | `references/export.md` |

`references/catalog.md` 列出当前真正可用的组件、示例与入口；按任务加载其中一组。需要的能力尚未交付时说明缺项，并在已有授权内新增本地视图。

## 语义与交付

- 原始资料保留原样，展示适配器读入并生成派生文件。使用 `mock` 标签标明演示夹具。
- 区分业务日期、完整步骤和执行时间；比较限定在可用共同范围；诊断记录独立标注。
- 区分零、确认无记录、未采集、加载中、局部数据和读取失败。已加载范围、总数与继续读取入口一并展示。
- 复制提议带 `baseVersionId`、`configSource`、原值、新值和修改路径；剪贴板受限时显示同一份完整文本。
- 本 Skill 的工作台任务保持纯展示。实验启动、恢复、模型探测以及采用配置修改需要对应的独立任务授权。
- 开发验收使用 mock 夹具；用户资料进入页面时保留其实际身份和来源。AI 批注与自动指令提交不进入本工作台。
- 用生成入口和刷新更新页面。返回可打开文件、配置与数据位置、刷新方法和当前资料范围。
