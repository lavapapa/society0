# Society0 Next 2.0.0a1

这是 Next 独立架构的首次预发布，tag 为 `next-v2.0.0a1`。公开内容包括通用执行内核、基础插件、完整 Social 插件、两个 preset，以及独立运行和开发所需的文档、示例和测试。适合研究者开始构建可组合的多主体仿真，并验证其机制与恢复流程。

## 一、能力

本版以共享环境、显式插件合同和原生 SQL 完整步骤组织仿真。

- Plugin／compose 管理数据初始化、服务依赖及资源生命周期。
- Actor／Driver、Information／Actions、Schedule／Phase 和 RunPlan 连接主体、信息、行为与运行计划。
- StageStore 保存权威状态与完整步骤；Thread、Memory、模型路由、结果及观察接口保存可检查的过程。
- plain、cognition、round-robin 和 Social 插件提供空白环境、输入来源组合、轮流会话、帖子、关系、通知、曝光、排序、推荐、拓扑与社交认知。
- 最新完整点只读快照、表名复用、行动结果 JSON 编码、可选重复响应判定、Thread 端点亲和性和结构化动作游标已纳入本版。

经济机制和工作台由各自消费者维护。公开运行可直接通过 Python API、runner、Results 与 Observation 完成。

## 二、安装

发行支持 Python 3.12 及以上。源码安装使用锁定依赖和仓内原生文件包；wheel 安装使用随本次发行提供的独立原生包。

### 2.1 从公开 tag 安装

```sh
git clone --branch next-v2.0.0a1 --single-branch https://github.com/lavapapa/society0.git
cd society0
uv sync --frozen --all-extras --python 3.12
uv run --no-sync python scripts/release_smoke.py
```

原生包通过 maturin／Rust 构建，工具链由其 `rust-toolchain.toml` 声明。首次构建需可访问 Rust 与 Python 依赖源。

### 2.2 从发行文件安装

从 GitHub Release 下载 `society0-2.0.0a1-py3-none-any.whl`，以及匹配系统和处理器架构的 `society0_filesystem-0.1.0-*.whl`，及 `constraints.txt` 放在同一目录。约束文件从本次发行的 `uv.lock` 导出，固定已经验收的依赖组合：

```sh
uv venv --python 3.12
uv pip install --constraint constraints.txt --find-links . 'society0[llm,memory,social,shell,observe]==2.0.0a1'
```

基础规则运行可仅安装 `society0`。LLM／shell 使用 `society0-filesystem==0.1.0`；其版本独立于 Core。没有匹配平台 wheel 时，下载原生包 sdist 并先安装它：

```sh
uv pip install ./society0_filesystem-0.1.0.tar.gz
uv pip install --constraint constraints.txt --find-links . 'society0[llm,memory,social,shell,observe]==2.0.0a1'
```

本版通过 GitHub Release 分发。完整 Core sdist 同时包含原生包源码、示例、测试、验证脚本和锁文件；解包后可按源码方式安装。

## 三、使用

从[规则入门](<core-next/getting-started.md>)开始完成创建、运行和恢复，再用[结果分析](<core-next/results-analysis.md>)读取分页结果及大正文。完整接口与 Social 用法见[文档站入口](<site/content/index.md>)。

Thread 端点亲和性引入 `model_route` 状态。运行和恢复使用一致的端点顺序及权重，新版从自身 schema 创建可信完整点。旧架构的检查点保留原始发行身份。重复响应提前终止需要显式配置；默认遵循调用方的完整预算。模型服务参数、凭据和真实模型调用预算由使用方提供。

## 四、验证

公开 CI 执行确定性测试及本地实验，构建 Core 与原生文件包，检查发行内容，并在独立环境中安装 wheel，完成规则运行、Social 行动、结果读取及完整点恢复。模型协议测试使用本地可控端点；真实模型测试通过显式环境参数另行执行。

本次修复将动作游标测试与实际结构化协议对齐，并以同布局正文增量测量原生 Session 内存，消除跨布局元数据开销对断言的影响。完整执行结果可在对应提交的 GitHub Actions 页面查阅。

Next 预发布提供可独立验证的运行基础；具体科学问题、现实数据与效果评价由使用方组织。
