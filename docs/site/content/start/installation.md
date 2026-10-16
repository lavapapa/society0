# 安装 Society0

先安装基础包，完成一次规则运行，再为研究所需的模型、记忆或文件工具增加依赖。以下命令使用源码仓库与仓库锁文件，适用于 macOS 和 Linux。

## 一、取得对应源码

准备 Git、Python 3.12 或以上，以及 [uv](https://docs.astral.sh/uv/getting-started/installation/)。在准备保存项目的目录执行：

```bash
git clone --branch next https://github.com/lavapapa/society0.git
cd society0
git switch --detach @COMMIT@
uv sync --frozen
uv run python -c "import society0; print(society0.__version__)"
```

最后一行会打印已安装包的版本。请与[本站构建身份](version.md)核对；命令中的提交由本站构建填入。`next` 与旧稳定线分别维护，显式选择源码可确保后面的模块和示例一致。已有该提交源码时，从 `uv sync --frozen` 继续。

uv 在仓库建立独立 `.venv`。后续命令通过 `uv run` 使用它，并假定终端仍在仓库根目录。基础安装包含规则主体、共享存储、插件组合、结果与恢复；这一条路径无需模型账号和 Rust 构建。

## 二、按功能增加依赖

可选依赖准备对应第三方库。运行时还需在计划中装配插件，并给实际使用的模型或机制提供配置。

| 依赖组 | 用途 | 安装示例 |
| --- | --- | --- |
| `llm` | 模型驱动、OpenAI 兼容与订阅适配、模型文件工具 | `uv sync --frozen --extra llm` |
| `anthropic`、`google` | 对应模型提供方，包含 LLM 基础依赖 | `uv sync --frozen --extra google` |
| `memory` | Chroma 检索及记忆解析依赖 | `uv sync --frozen --extra llm --extra memory` |
| `social` | 社交机制的图与向量依赖 | `uv sync --frozen --extra social` |
| `shell` | Bashkit 命令与原生文件系统 | `uv sync --frozen --extra shell` |
| `observe` | HTTP 观察服务 | `uv sync --frozen --extra observe` |

多次执行 `uv sync` 时，把仍需使用的 extra 一起列出。例如：

```bash
uv sync --frozen --extra llm --extra memory --extra social --extra observe
```

安装依赖不会发起模型请求。模型地址与凭据由自己的运行环境提供，接入步骤见[提供方指南](../guide/models.md)。

## 三、原生组件与数据位置

`llm` 和 `shell` 使用原生文件系统组件。源码安装由仓库的 uv 配置选择本地组件；构建所需 Rust 工具链以[组件工具链声明](https://github.com/lavapapa/society0/blob/@COMMIT@/native/society0-filesystem/rust-toolchain.toml)为准。安装 [rustup](https://rustup.rs/) 后，按[安装参考](../reference/installation.md)选择源码构建或适配平台的 wheel。

运行中的 SQLite 与 WAL 放在本地文件系统。完整工件可以导出后传送到其他存储位置；原生文件锁和在线 WAL 的支持边界见[存储参考](../reference/storage.md)。

现在进入[第一次运行](quickstart.md)，让两个规则主体留下可读取的结果。
