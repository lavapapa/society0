# 主仓合并后验收

2026年10月5日在主仓 `research/simulation/society0core` 验收合并提交 `0e0b3682c591e443e002662a66016042f66e5545`。通过 `RUSTUP_TOOLCHAIN=1.95.0 uv sync --frozen --all-extras --python 3.12` 建立主仓长期环境，实际使用 Python 3.12.12；Society0 从主仓 `src` 导入，原生文件系统组件从主仓 `.venv` 导入，安装来源均指向主仓。产品范围 `src`、`native`、`pyproject.toml`、`uv.lock` 共59个文件与产品提交 `ab6469d` 逐字节一致，身份与依赖版本见 [main-identity.json](main-identity.json)，安装日志见 [main-uv-sync.txt](main-uv-sync.txt)。

冻结依赖环境的首轮结果为943项通过、1个模块跳过、15项真实端点测试排除；被跳过的模块包含3项独立向量后端可行性实验，额外需要 `sqlite-vec==0.1.6`。显式安装既有实验依赖后，最终完整范围 `tests/primary`、`tests/e2e`、`tests/experiments` 共946项通过、15项排除，用时39.09秒。该依赖属于研究候选的额外测试环境，产品锁文件保持原字节；CI工作流按冻结依赖口径跳过该实验模块。首轮证据见 [main-frozen-full.txt](main-frozen-full.txt) 和 [main-frozen-full.xml](main-frozen-full.xml)，最终证据见 [main-full.txt](main-full.txt) 和 [main-full.xml](main-full.xml)，额外依赖安装见 [main-experiment-dependency.txt](main-experiment-dependency.txt)。

正式 `conversation_pilot.build` 计划在当前主仓运行两轮，运行身份为 `1643057d3b324899aca6cd4aea3c56aa`，结果为 `completed`、完整步骤2、诊断错误0。从该完整运行导出两个步骤与四个主体的工作台数据，实际渲染测试共9项通过、0项跳过；其中当前运行结果形成8行消息表与两个时点的指标曲线。证据见 [main-pilot.txt](main-pilot.txt)、[main-workbench-payload.json](main-workbench-payload.json) 和 [main-node.txt](main-node.txt)，复现命令见 [main-command.sh](main-command.sh)。

本次验收覆盖主仓安装身份、确定性跨组件行为和实际运行数据渲染。全部验收进程已结束，运行工件保存在本地 `main-pilot/`；真实模型端点未在本轮调用，其证据沿用对应冻结源码的既有独立验收。
