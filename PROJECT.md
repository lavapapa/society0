# Society0 Next

Society0 提供通用多主体仿真的运行内核与可组合机制。本文定义公开项目的职责和版本路线；具体接口由[技术文档站](docs/site/README.md)维护。

## 一、范围

公开范围包括完整通用 Core、plain、cognition、round_robin 与全部通用 Social plugins。主体共享一个环境，插件通过信息、行动和服务实现机制；使用者定义初始资料、主体、时间安排与测量。

运行计划可以使用规则驱动或模型驱动。模型驱动保留完整 Thread、工具回执和未完成原因；记忆写入、自动召回与主动检索分别配置。模型地址与凭据由实际运行环境提供。仿真设定和现实解释由使用者依据问题与证据确定。

## 二、架构

Plugin 分别声明数据先决条件与服务依赖。compose 先准备共享存储，再安装服务；Schedule 与 Phase 明确业务顺序，Runtime 执行主体激活与完整步骤发布。独立阶段的并发和提供方请求并发分别配置。

规范写入器在 SQL 事务中同步维护事实、当前投影与索引。完整步骤覆盖状态、Thread、结果及声明的外部工件。恢复从可信完整点建立新运行；历史分析使用独立只读视图。Results 与 Observation 提供纯 Python 读取、分页和原文续读，整条运行—分析—恢复路径无需界面。

开发接口见[参考索引](docs/site/content/reference/index.md)，可运行消费者见[示例索引](examples/core_next/README.md)。

## 三、版本

Next 的开发分支为 `next`；`stable/4.1` 保留既有稳定架构，两条路线分别发布与验证。Next 的变更保持在本路线，既有运行工件按其产生版本读取。Core 与原生文件系统包具有各自的版本，发行身份见[Next alpha 发行说明](docs/release-next-2.0.0a1.md)。

使用固定源码、依赖锁与完整配置复现实验；每次运行和恢复选择新目录。安装、结果读取与恢复的起步路径见[第一次运行](docs/core-next/getting-started.md)。
