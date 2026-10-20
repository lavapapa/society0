# 基础环境验证

基础环境按 Next 自身合同验证 plain、round-robin conversation 与 social network。引擎提供机制、信息、行动、完整步骤、恢复与观察；使用方在情景层设计研究问题、控制实验、基线与科研有效性评估。路线与分支职责见 [PROJECT](../../PROJECT.md)。

## 一、当前合同

Next 分支移除旧实现、旧源码启动器、算法副本与旧版对照测试。历史实现保存在 Git 历史与 `stable/4.1`，原始运行资料继续保留其版本身份。当前测试以显式期望和公共消费者为依据。

| 环境 | 验证范围 | 当前消费者 |
|---|---|---|
| plain | 空机制组合、规则主体、RunPlan、完整步骤及新进程恢复 | [基础环境实验](../../tests/experiments/test_basic_env_oracle.py) |
| round-robin | 每对一次、确定配对顺序、消息与广播身份、完整主体资料、参与标记、历史、权限与恢复 | [轮次插件测试](../../tests/primary/test_plugin_round_robin.py)、[独立进程消费者](../../tests/e2e/basic_env_process.py) |
| social-network | 五类拓扑、发帖互动与关注、活动候选、明确评分、偏好原文、通知、全局热门、干预、曝光、原向量与恢复 | [社交插件测试](../../tests/primary/test_plugin_social.py)、[独立进程消费者](../../tests/e2e/social_process.py) |
| 认知组合 | 多来源输入选择、同 Moment 增量游标、完整 Thread、呈现效果及失败步骤边界 | [认知消费者](../../tests/primary/test_cognition_oracle_review.py) |

[基础环境实验](../../tests/experiments/test_basic_env_oracle.py) 直接断言四人三轮的每条消息、收件人、主体信息与动作结果，以及社会场景的排名、通知、曝光和事实；完整运行与新进程恢复逐项比较当前 Next 结果。推荐评分、拓扑和偏好文本的输入与期望由当前合同明确给出。

公共组合的目录委托、登记、可替换推荐、多来源认知和关闭路径见 [公共运行手册](../../examples/core_next/README.md)。真实模型、嵌入与记忆按 [真实测试合同](real-test-migration.md) 单独验证。

## 二、成本与执行

固定活动主体、当前消息或活动帖子及本步新增事件后扩大累计历史，检查当前读取和完整步骤的增长成本；再扩大活动集合，区分真实业务工作量。分别记录根初始化、纯机制步骤、含 Driver 的计算、提供方等待、发布、关闭与恢复。完整 Thread、全文读取和全图初始化按实际数据量计成本。

复用巨消息范围读取、冷正文点赞、历史增长 VM 计数、向量缓存与恢复测试。性能调整保持完整信息、行动自主性和逐笔事实。运行直接相关测试后执行确定性主套件与实验：

```sh
uv sync --frozen --all-extras
uv run pytest tests/primary tests/e2e tests/experiments -m 'not real_e2e'
```

真实端点测试使用显式环境配置与独立运行目录。长期社会效果与真实模型成本由各研究协议评估。
