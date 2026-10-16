# 依赖与平台范围

这一页说明从哪条源码线安装、基础包能做什么，以及哪些能力要另外装依赖。安装时你要自己选定源码分支或固定的提交，并按实际用到的组件补装依赖组；依赖装好之后，研究权限、领域规则和调度顺序仍要由你自己提供，安装过程不会替你生成这些。第一次安装的完整步骤见[安装指南](<../start/installation.md>)。

## 基础包能做什么

基础包可以执行规则主体，使用共享 SQL 状态，从完整步骤恢复，也支持不可变数据集。只跑规则主体时，用规则驱动和公开的运行计划就够了，不需要模型账户，也不需要本项目提供的 Rust 文件扩展。

```python
from society0.kernel.drivers import RuleDriver
from society0.kernel.runner import RunPlan, run_plan
```

<!-- api: society0.kernel.drivers.RuleDriver -->

RuleDriver 接受返回 DriverResult 的同步或异步回调，也可以组合已经装配好的激活扩展；运行时的约定见 [Runtime](<runtime.md>)。

Python 版本要求和依赖约束由项目元数据定义，需要 Python 3.12 或以上。基础依赖提供 SQLite Session、参数 schema、原生 JSON 与 zstd 编码。存储使用 POSIX flock，运行时支持 macOS 与 Linux，Windows 不在支持范围内。

## 按能力选择依赖组

依赖组按实际调用路径安装，几组能力可以一起装。

| 依赖组 | 装了以后能用的能力 | 仍需显式提供 |
|---|---|---|
| `llm` | Pydantic AI 的 OpenAI/Responses、订阅接入及原生文件搜索 | 提供方、模型、凭据环境与输入构建器 |
| `anthropic` / `google` | 相应普通 API 提供方，包含 llm 基础 | 对应模型服务配置 |
| `memory` | 向量检索与提取协议依赖 | 记忆服务、嵌入及独立策略 |
| `social` | 图及向量相关依赖 | 所需社交叶机制与模型配置 |
| `shell` | Bashkit 与原生文件系统适配器 | 工作区、挂载与 shell 工厂 |
| `observe` | HTTP 观察服务 | 实际运行目录与端口 |

在选定的 Next 工作树里可以执行 `uv sync --extra llm`，需要 Shell 时再加 `--extra shell`。装了 extra 以后，相应的插件仍然要你自己写进 RunPlan。规则运行、数据集和普通 Python 结果读取用基础依赖就能完成；HTTP 观察服务要另装 observe。

## 原生组件与运行目录

模型的文件工具和 shell 依赖独立的 society0-filesystem 包。

工作树的 uv sources 指向仓库内的原生包。从源码构建需要该工具链指定的 Rust 与 maturin；安装已经构建好并且和平台匹配的 wheel 不需要 Rust。用 `pip install society0` 装到的是发布版本，走这条源码路线时要另外确定原生包的取法和核心源码的提交。wheel 必须匹配目标平台和 Python 环境，工具链版本与构建步骤见[安装指南](<../start/installation.md>)。

运行中的 SQLite 数据库和 WAL 放在本地文件系统。网络挂载适合传输数据、长期存放完整的产物文件；把在线 WAL 跑在 FUSE、CephFS 这类文件系统上，运行时不给保证。恢复和只读分析都使用新目录，复制带来的费用与状态边界见[存储生命周期](<storage-lifecycle.md>)。

要验证基础路径能不能跑通，可以按[首次运行教程](<../start/quickstart.md>)走一遍；模型连接和嵌入调用需要另行取得具体端点的授权。源码中的基础驱动入口见 [RuleDriver](<https://github.com/lavapapa/society0/blob/@COMMIT@/src/society0/kernel/drivers.py#L6>)。
