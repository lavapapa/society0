# 从零打开工作台

工作台在第一次讨论时就可以出现。资料不全、还没有运行、甚至还没有配置版本都不影响打开。

## 一、确定入口

先确认当前项目是否已有工作台：

```text
<project>/workbench/
  project.json        # 项目身份与装配
  data.json           # 展示资料
  index.html          # 生成的页面
```

已有 `index.html` 时直接打开并要求刷新，不重新生成结构。没有时按下面创建。

## 二、创建最小项目

```bash
python3 <skill>/scripts/create_workbench.py \
  --output-dir <project>/workbench \
  --title "实验名称"
```

该命令写入 `project.json`、`data.json`（最小空资料）和 `index.html`。生成后立即打开 `index.html`，确认页面可读、实验设置为空态。

## 三、对话中的第一步

空壳只需要三件东西就能开始：

1. 实验标题；
2. 用户已经说清的问题或目标；
3. 当前要讨论的方向（插件、世界或主体）。

把它们写进 `data.json` 的 `study`，重新生成，再刷新页面。其余集合保持为空数组，不要填占位主体、假运行或示例指标。

## 四、什么时候读更多

| 用户接下来要做什么 | 再读 |
|---|---|
| 讨论机制与参数 | `catalog.md`、`components/configuration.md` |
| 计划平行世界 | `components/runs-worlds.md` |
| 开始查看某次运行 | `components/runs-worlds.md`、`time-calendar.md` |
| 还没有明确问题 | 停在这里，先让空壳承担对话背景 |

空壳阶段不需要读完整数据合同、组件目录或场景配方。
