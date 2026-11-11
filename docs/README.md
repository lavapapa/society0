# 文档

这些文档面向使用 Society0 做仿真和实验的人。它们描述当前内核实际支持的 profile、命令、数据路径和分叉方式；接口细节以 `typescript/docs/` 和安装包的类型声明为准。

## 从这里开始

- [安装与第一次运行](getting-started.md)：准备环境、构建或安装、跑通一条规则驱动的 run、读取结果、续跑。
- [概念](concepts.md)：run、世界、主体、插件、profile、历史与完整点、观察之间的关系。
- [命令行](cli.md)：`society0` 各命令的参数、输出字段和常见错误。

## 做实验

- [profile 与插件](profiles-and-plugins.md)：如何写 profile，Core、Extras、Social 有哪些可装配入口，插件入口需要声明什么。
- [数据与数据集](datasets.md)：world 初始资料、数据集文件、插件内接入数据的方式。
- [把问题变成实验](experiments.md)：从研究问题到因素、主体层、ODD、插件组合和试跑。
- [因果分支](causal-branches.md)：从同一完整点建立平行世界，固定种子、主体和数据，只改一个因素。

## 实现说明

- [TypeScript 构建记录](typescript-build.md)：构建命令、依赖来源和已经验证的工程边界。

## 相关公开文档

- [公开 SDK 使用](../typescript/docs/public-usage.md)：仓外安装、`run-profile`、恢复、分叉和测量读取。
- [插件配置](../typescript/docs/plugin-config.md)：按包和入口列出的配置字段、默认值与更新方式。
- [模型上下文](../typescript/docs/model-context.md)：模型窗口取值和输出预算。
- [平台入口](../typescript/docs/platform-entry.md)：`society0` CLI 与宿主的调用约定、检查项和错误格式。
