# Society0 网络工作台交互原型

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

社会科学研究者、学生，以及协助其创建实验的 agent。

## Product Purpose

用网络主画布、浮动工具条和详情侧栏，检查基于 LLM Agent 的社会模拟中的主体、帖子和关联关系。

## Operating Context

用户已确认先交付多个可操作方案，再选择正式工作台设计。独立原型采用静态单文件 HTML，在浏览器本地打开。当前阶段聚焦网络交互，不改正式模板。

## Capabilities and Constraints

- 展示多种关系及其叠加、节点和边的详情、筛选、缩放、定位与重新布局。
- 支持查看配置初始网络及保存的实验时点。页面无实验控制与实时数据接入。
- 原型使用明确标注的合成数据；其记录用于验证交互，不作为研究发现。
- 正式产品选型未定；本轮比较桌面布局。窄窗口保留核心操作。

## Evidence on Hand

现有 React/Vite 工作台位于 ../workbench-template；用户明确选择 Impeccable、Laws of UX、Pick UI Library、Emil Design Engineering、Prototype。

## Product Principles

- 每种关系保留方向、类型、来源及时间含义。
- 对象选择与详情、关系记录保持一致。
- 时间切换保留用户的空间上下文。
- 组件可由 agent 扩展；静态交付包含数据与依赖。
