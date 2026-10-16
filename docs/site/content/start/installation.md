# 安装 Society0

照着这一页装好基础包，你就能在本地跑起一次规则运行，并把结果读出来。等研究需要模型、记忆或文件工具时，再回到这里加对应的可选依赖。下面的命令都用仓库自带的锁文件，适用于 macOS 和 Linux。

## 取得与本站一致的源码

准备 Git、Python 3.12 或以上，以及 [uv](https://docs.astral.sh/uv/getting-started/installation/)。在准备保存项目的目录执行：

```bash
git clone --branch next https://github.com/lavapapa/society0.git
cd society0
git switch --detach @COMMIT@
uv sync --frozen
uv run python -c "import society0; print(society0.__version__)"
```

最后一行会打印已安装包的版本，可以拿去和[本站构建身份](version.md)核对。命令里的这个提交由本站构建时填入。`next` 与旧稳定线分开维护，显式切到这个提交，后面的模块和示例才和文档一致。本地已经有这份提交的源码时，从 `uv sync --frozen` 继续。

uv 会在仓库里建一个独立的 `.venv`，后续命令通过 `uv run` 使用它，并假定终端仍停在仓库根目录。基础安装已经包含规则主体、共享存储、插件组合、结果与恢复，这条路径不需要模型账号，也不需要编译 Rust。

## 按功能增加可选依赖

这些可选依赖装的是对应的第三方库。真正运行时，还要在计划里装配相应插件，并给实际用到的模型或机制提供配置。

| 依赖组 | 用途 | 安装示例 |
| --- | --- | --- |
| `llm` | 模型驱动、OpenAI 兼容与订阅适配、模型文件工具 | `uv sync --frozen --extra llm` |
| `anthropic`、`google` | 对应模型提供方，包含 LLM 基础依赖 | `uv sync --frozen --extra google` |
| `memory` | Chroma 检索及记忆解析依赖 | `uv sync --frozen --extra llm --extra memory` |
| `social` | 社交机制的图与向量依赖 | `uv sync --frozen --extra social` |
| `shell` | Bashkit 命令与原生文件系统 | `uv sync --frozen --extra shell` |
| `observe` | HTTP 观察服务 | `uv sync --frozen --extra observe` |

每执行一次 `uv sync`，都要把这次仍然需要的 extra 一起列出来。例如：

```bash
uv sync --frozen --extra llm --extra memory --extra social --extra observe
```

安装依赖不会向模型服务发出请求。模型地址与凭据由你自己的运行环境提供，接入步骤见[提供方指南](../guide/models.md)。

## 原生组件与数据存放位置

`llm` 和 `shell` 会用到原生文件系统组件。从源码安装时，仓库的 uv 配置负责选择本地组件；需要哪个版本的 Rust 工具链，以[组件工具链声明](https://github.com/lavapapa/society0/blob/@COMMIT@/native/society0-filesystem/rust-toolchain.toml)为准。装好 [rustup](https://rustup.rs/) 之后，按[安装参考](../reference/installation.md)决定是从源码构建，还是使用适配本平台的 wheel。

运行期间的 SQLite 与 WAL 放在本地文件系统上。完整工件可以导出，再传送到别的存储位置；原生文件锁和在线 WAL 各自支持到什么范围，见[存储参考](../reference/storage.md)。

装好之后就进入[第一次运行](quickstart.md)，让两位规则主体留下可以读取的结果。
