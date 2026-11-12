# Society0

Society0 提供通用多主体社会仿真的运行内核与可组合机制。本文说明公开项目的范围、结构和版本做法；使用方法见[文档索引](docs/README.md)。

## 一、范围

公开范围包括完整的 Core、Extras（数据集、记忆、向量与 workspace）和全部通用 Social 插件（关系、内容、传播、推荐与测量），以及插件宿主和 `society0` 命令行。主体共享一个世界，插件通过信息、行动和服务实现机制；使用者定义初始资料、主体、时间安排与测量。

运行可以使用规则驱动或模型驱动。模型驱动保留完整 Thread、工具回执和未完成原因；模型地址与凭据由实际运行环境提供，不进入源码和公开产物。仿真设定和现实解释由使用者依据问题与证据确定。

## 二、结构

源码位于 `typescript/`：

- `packages/host`：插件装配、生命周期与宿主 ABI。
- `packages/core`：运行内核、调度、SQLite 原生存储、完整点、恢复与分叉。
- `packages/extras`、`packages/social`：通用机制插件。
- `packages/sqlite-native`、`packages/random-native`：原生存储与随机数。
- `apps/cli`：`society0` 命令行，输出 JSON，供脚本和上层平台调用。

规范写入器在 SQL 事务中同步维护事实、当前投影与索引。完整步骤覆盖状态、Thread、结果及声明的外部工件；恢复从完整点继续，分叉从同一完整点建立新的平行世界。

## 三、版本

开发分支为 `next`。构建依赖由 `typescript/package-lock.json` 固定，构建与验收命令见[构建与验收](docs/build.md)。复现实验时使用固定源码、依赖锁与完整 profile，每次运行和分叉选择新目录。
