# 安装与原生扩展

基础规则运行使用 Python 3.12 及以上版本，依赖 APSW、jsonschema 和 python-rapidjson。模型、记忆、社交与 shell 按功能选择安装，研究脚本可以组合这些插件。

## 一、安装范围

项目按能力提供可选依赖组。基础安装支持共享状态、规则驱动、调度和恢复；`llm` 增加提供方适配，`memory` 增加向量检索与提取协议，`social` 增加社交机制的图与向量依赖，`shell` 增加 Bashkit 与 Society0 文件系统适配器；`datasets` 增加不可变批次所用的 zstandard，普通行结果不需要该组。开发依赖包含确定性测试实际使用的模型、向量与图组件；纯基础安装的验收使用独立环境。

当前源码工作树可使用 `uv sync --extra shell`。`tool.uv.sources` 将原生扩展指向 `native/society0-filesystem`；它是本项目工作树中的独立包，尚未作为公共包发行。基础规则安装无需构建这个扩展。

## 二、构建与使用

原生扩展通过成熟 Bashkit 的文件系统 ABI 接入，使用 maturin 构建 wheel。源码构建需要 Rust 1.95.0 与对应平台工具链，已构建 wheel 的安装和运行无需 Rust。

```sh
cd native/society0-filesystem
maturin build --release --out /tmp/society0-wheels
```

在已经取得扩展 wheel 的环境，可从项目根目录安装本地包：

```sh
uv pip install --no-sources /tmp/society0-wheels/society0_filesystem-*.whl '.[shell]'
```

`--no-sources` 使这次安装明确使用给出的 wheel。源码工作树日常开发仍可使用 uv 的本地包声明。wheel 按操作系统和架构构建，已验证 macOS arm64 与 Linux x86_64；后者 wheel 的 manylinux 标签为 `manylinux_2_34_x86_64`，在 Python 3.12.9 的干净环境完成真实文件写入和恢复。其他平台尚待实际构建与运行验证。

## 三、验证

`benchmarks/core_next_clean_shell.py` 在独立环境执行真实私有文件写入、完整步骤发布与新运行恢复，并检查模型、向量及图依赖未被安装。基础安装的独立复核另执行规则 Driver、Runtime、Observation 和状态恢复，检查未加载旧 World 或模型模块。原始结果见 `research/core-next/base-install-20261004.txt`、`shell-clean-install-20261004.txt` 、`packaging-independent-20261004.txt`、`native-linux-build-20261004.txt` 与 `native-linux-wheel-check-20261004.txt`。

这些记录验证当前源码及指定平台的安装闭环；公共发行仍需将 Core 与原生包按同一版本计划提供给用户。文件级读写成本和共享目录语义见 [工作区合同](workspace-contract.md)。

存储使用 POSIX 文件锁，当前支持 macOS 与 Linux。Windows 未提供此运行合同。版本以 pyproject.toml 和包内 __version__ 为准，发行就绪状态见验收清单；工作树可运行与公共发布分别确认。

运行中的 SQLite 与 WAL 置于本地文件系统。网络挂载用于完整封存工件的传输和存放；正式运行先建立本地工作目录，再按完整点合同导出。当前接口没有为 FUSE、CephFS 或其他网络挂载提供在线 WAL 运行保证。
