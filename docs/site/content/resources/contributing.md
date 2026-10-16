# 贡献机制、修复与文档

研究需要一项现有机制没有的能力，或者运行结果与预期不同时，就要改这个项目。改动从一个真实消费者开始：研究需要哪项能力、在哪一步出现差异、怎样从保存的事实判断修复完成。Next 的机制和运行能力维护在 next 线上，旧稳定线的产业消费者有独立版本边界。

按下面的顺序做：先确认目标分支和工作树，再定位改动位置，然后验证改动，最后同步文档并提交。

## 建立可复现入口

在选定源码仓库检查身份与修改状态：

```bash
git remote -v
git branch --show-current
git rev-parse HEAD
git status --short
```

新问题附上最小配置、命令、预期行为、实际行为及异常发生位置。保存相关完整步骤、阶段结果和必要的 Thread 片段；分享前移除凭据及不适合公开的研究材料。模型端点失败需要说明提供方与调用参数，避免将账户密钥放进复现程序。

机制需求先给一个小型规则消费者：定义主体看到的资料、可以执行的行动、写入事实与成功标准。已有机制能通过服务引用组合时，沿[组合指南](../guide/composition.md)使用现有入口。

## 定位改动位置

按职责找到修改位置：

| 需要改变的能力 | 源码入口 | 接口参考页 |
| --- | --- | --- |
| 插件声明与生命周期 | [Plugin / PluginHost](https://github.com/lavapapa/society0/blob/@COMMIT@/src/society0/kernel/plugins.py) | [插件参考](../reference/plugin.md) |
| 主体资料与驱动绑定 | [ActorDirectory / ActorStore](https://github.com/lavapapa/society0/blob/@COMMIT@/src/society0/kernel/actors.py) | [主体参考](../reference/actor.md) |
| 模型决定循环 | [LLMDriver](https://github.com/lavapapa/society0/blob/@COMMIT@/src/society0/kernel/llm.py) | [LLM 参考](../reference/llm.md) |
| 资料和领域行动 | [Information / Actions](https://github.com/lavapapa/society0/blob/@COMMIT@/src/society0/kernel/interaction.py) | [交互参考](../reference/interaction.md) |
| 多来源认知 | [cognition_plugin](https://github.com/lavapapa/society0/blob/@COMMIT@/src/society0/plugins/cognition.py) | [认知参考](../reference/cognition.md) |
| 完整步骤与恢复 | [StageStore](https://github.com/lavapapa/society0/blob/@COMMIT@/src/society0/kernel/storage.py) | [存储参考](../reference/storage.md) |

领域规则放在拥有该事实的机制中。通用运行层负责主体、工具、Thread 和完整步骤，研究中的价格、政策或具体制度由环境实现。需要新增对外能力时，同时给出一个正式消费者和对应的失败检查。

## 验证行为

先让最小复现暴露问题，再验证改动涉及的路径。安装开发依赖后运行确定性测试；真实模型使用显式授权环境另行执行：

```bash
uv sync --frozen --all-extras
uv run pytest -m 'not real_e2e'
```

验证从实际写入和读取路径判断：是否保留原文、主体归属、行动结果、次序和恢复边界。组合变更还需检查安装失败与退出；存储变更需比较完整步骤恢复后的事实。性能改动在同一输入上分别测量计算、等待、持久化、内存与状态增量，保持主体信息和行动能力。

公开组合用例及其读取验收器见[示例指南](../guide/examples.md)。报告写明执行了哪些入口、实际差异与未覆盖范围；真实提供方参数、额度和长期研究效果保留独立验证。

## 同步文档

使用教程解释怎样完成任务，参考页保存精确签名、默认值与所有权，设计与调查记录保留其原始用途。本站手写正文位于源码中的 docs/site/content；签名、版本和工具 schema 由构建过程从对应源码生成。改接口时更新拥有该事实的正文和可执行例子，让其他页面链接到它。

重新构建后核对导航、内部链接、示例代码与实际产物；离线交付还需检查 file 打开、中文搜索、代码复制和完整资源。研究 skill 是容易遗漏的消费者，涉及教学入口、模型接线或实验流程时检查其示例和指南。

提交说明包含研究用例、行为变化、验证命令与边界。合入和发布沿目标版本线处理，运行部署的实际状态由部署工件与运行检查确认；版本来源集中查看[本站版本页](../start/version.md)。
