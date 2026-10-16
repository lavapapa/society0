# 最小概念地图

在[第一次运行](quickstart.md)中，一份工厂创建两个主体，调度两个时点，并把每次决定保存成表。下面沿着这条已经发生的运行链认识最常用的词汇。

## 一、从计划到完整步骤

```text
配置 → build(config) → RunPlan
                         ↓
                    compose 插件
                         ↓
Schedule → StepPlan → Runtime → Phase → 激活主体
                         ↓
                    发布完整步骤
```

`RunPlan` 连接运行合同、插件和调度入口。`Plugin` 声明共享环境的一部分，例如主体目录、轮次对话或结果存储。`compose` 先建立数据，再安装服务；`Schedule` 随后交给 `Runtime` 一份份步骤计划。

首次运行的 `SequenceSchedule` 给出业务时间 1、2，每步执行一次 `decide` 阶段。阶段激活 Alice 和 Bob，等待两位返回，再把值组织成 `StepResult`。整步收束后才出现可用于恢复的完整身份。

## 二、主体怎样参与

| 名称 | 在例子中的工作 | 下一层解释 |
| --- | --- | --- |
| `ActorRecord` | 保存 Alice 的身份与驱动标签 | [主体与激活](../concepts/activation.md) |
| `Driver` | 执行规则函数，或组织完整模型决定 | [主体与驱动教程](../tutorial/actors.md) |
| `Session` | 给本次激活绑定主体、时间、信息与行动入口 | [激活的作用域](../concepts/activation.md) |
| `Information` | 读取主体被允许取得的资料 | [信息与分页](../concepts/information.md) |
| `Actions` | 执行机制登记的业务操作 | [行动教程](../tutorial/actions.md) |

环境中的所有主体共享机制保存的事实。比如 Alice 给 Bob 发消息后，Bob 的收件箱随之变化；消息发送资格和写入规则由对话机制维护。研究者可以更换 Driver，让规则主体和模型主体通过同一组信息、行动接口参与。

## 三、时间与记录分别回答什么

步骤编号回答“已经完整完成了多少步”；业务时间回答“模型中现在是什么时候”。一个步骤可以代表一轮讨论、一个日期或你明确约定的时间跨度。`Moment` 结合业务时间与阶段名称，给主体交互确定当前语境。它们的关系见[主体与激活](../concepts/activation.md)。

| 记录 | 回答的问题 |
| --- | --- |
| 机制状态 | 环境里实际发生了什么？ |
| `Thread` | 主体与模型进行了哪些完整交互？ |
| `Memory` | 主体保存了哪些可再召回的经验？ |
| `Results` | 本次研究记录了哪些指标、表和决定结果？ |

规则示例直接记录结果，没有建立模型 Thread。模型与记忆需要各自的服务和生命周期。实时诊断可显示正在执行的工作；[完整步骤](../concepts/complete-steps.md)为恢复与固定分析提供确定边界。

带着这张地图进入[创建自己的运行计划](../tutorial/first-plan.md)，把主体、规则与测量写进自己的程序。需要深入理解时，再从[概念目录](../concepts/index.md)选一个问题阅读。
