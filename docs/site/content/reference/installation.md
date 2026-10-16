# 依赖与平台范围

Next 需显式选择源码分支或固定提交。基础包可执行规则主体、共享 SQL 状态、完整步骤恢复与不可变数据集；按实际消费者添加可选依赖。首次安装步骤见[安装指南](<../start/installation.md>)。

## 一、基础入口

基础运行可使用规则驱动与公开计划，无需模型账户或本项目 Rust 文件扩展。

```python
from society0.kernel.drivers import RuleDriver
from society0.kernel.runner import RunPlan, run_plan
```

<!-- api: society0.kernel.drivers.RuleDriver -->

RuleDriver 接受返回 DriverResult 的同步或异步回调，可组合已装配的激活扩展。运行合同见[Runtime](<runtime.md>)；依赖安装成功不自动提供研究权限、领域规则或调度。

Python 要求与依赖约束由项目元数据定义，使用 Python 3.12 或以上。基础依赖承担 SQLite Session、参数 schema、原生 JSON 与 zstd 编码。存储使用 POSIX flock，运行合同覆盖 macOS、Linux；Windows 不在该合同范围内。

## 二、功能选择

依赖组按调用路径安装，多个能力可以组合。

| 依赖组 | 启用的消费能力 | 仍需显式提供 |
|---|---|---|
| `llm` | Pydantic AI 的 OpenAI/Responses、订阅接入及原生文件搜索 | 提供方、模型、凭据环境与输入构建器 |
| `anthropic` / `google` | 相应普通 API 提供方，包含 llm 基础 | 对应模型服务配置 |
| `memory` | 向量检索与提取协议依赖 | 记忆服务、嵌入及独立策略 |
| `social` | 图及向量相关依赖 | 所需社交叶机制与模型配置 |
| `shell` | Bashkit 与原生文件系统适配器 | 工作区、挂载与 shell 工厂 |
| `observe` | HTTP 观察服务 | 实际运行目录与端口 |

在选定 Next 工作树可执行 `uv sync --extra llm`，需要 Shell 时再加 `--extra shell`。安装 extra 不会自动把相应插件加入 RunPlan。规则、数据集与普通 Python 结果读取可使用基础依赖；HTTP 服务另外需要 observe。

## 三、原生组件与运行目录

模型文件工具及 shell 依赖独立的 society0-filesystem 包。

工作树的 uv sources 指向仓库内原生包；源码构建需要其工具链指定的 Rust 与 maturin，安装已构建的匹配 wheel 无需 Rust。不要把 `pip install society0` 的默认版本等同于本源码路线，原生包的获取方式与核心源码身份需一同明确。wheel 必须匹配目标平台和 Python 环境，实际工具链与构建步骤以[安装指南](<../start/installation.md>)为入口。

运行中的 SQLite 和 WAL 放本地文件系统。网络挂载适合传输、存放完整封存产物；FUSE、CephFS 等在线 WAL 运行没有本合同保证。恢复或只读分析采用新目录，费用与状态复制边界见[存储生命周期](<storage-lifecycle.md>)。

验证基础路径可运行[首次运行教程](<../start/quickstart.md>)；模型连接与嵌入验证需另有明确端点授权。源码中的基础驱动入口见 [RuleDriver](<https://github.com/lavapapa/society0/blob/@COMMIT@/src/society0/kernel/drivers.py#L6>)。
