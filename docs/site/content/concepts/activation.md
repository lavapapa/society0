# 主体与激活

主体身份可以跨越整个研究，而一次决定拥有短暂的执行作用域。Society0 用持久目录保存前者，用 Driver 和 Session 组织后者。理解这个区别，可以明确资料何时读取、谁可以操作，以及哪些状态需要为恢复保存。

## 一、从身份到一次决定

`ActorRecord` 保存 ID、驱动标签、persona、state、config、roles 与 active。ActorDirectory 提供数据，ActorStore 把持久驱动标签解析成当前安装的工厂。实际 worker 取用某位主体时才取得记录视图并构造 Driver，休眠人口无需都驻留为活跃驱动对象。

目录选择返回符合条件的 ID。是否激活由阶段决定，具体业务资格由机制判断。例如，拥有 `organizer` 角色便于选择组织者；发送某条消息仍需对话机制确认其成员与当前配对。

激活时 Runtime 创建绑定该主体的 Session，给它信息、行动、当前 Moment、共享准备值与信号。结束、失败或取消后，交互 scope 关闭，借出的门面失效。需要后续恢复的等待事项或主观变化，应保存到目录或机制状态中。

## 二、步骤、时间和 Moment

| 身份 | 用途 | 示例 |
| --- | --- | --- |
| `Session.step` | 正在执行的连续完整步骤编号 | 1、2、3 |
| `Moment.time` | 研究者定义的业务时间 | 轮次、日期字符串、其他 JSON 时间表示 |
| `Moment.phase` | 当前阶段语境 | `talk`、`interview` |
| Thread 身份 | 同主体、同 Moment、同 mode 的交互 | Alice 在某轮讨论中的 decision |

网络等待增加真实运行耗时，业务时间继续由 Schedule 决定。同一 Moment 可以多次激活；新信号进入之后，再次调用该主体 Driver。LLM 的持久 Thread 会继续同一交互，认知来源用各自位置判断新增内容。

临时 Session 不保存全部历史。A→B→A 的 Thread 与消费位置恢复由持久服务负责，详情见[认知专题](cognition.md)。记忆默认以整数 step 管理可见版本和衰减，业务日期保留其原始含义。

## 三、信号如何合并

`activate` 提交一个主体信号。相同主体还在等待时，新信号并入同次激活；主体正在执行时，新信号进入下一次激活。Driver 从 `session.signals` 取得本轮载荷，所有实际工作在阶段结束前排空。

同主体同时拥有一个 Driver 写者，独立阶段也保留这项约束。`drain` 取回尚未消费的结果，按首次提交主体顺序和激活 round 排列，避免网络返回快慢改变结果排列。

## 四、怎样解释结果状态

| DriverResult 状态 | 对步骤的含义 |
| --- | --- |
| `completed` | 本次决定完成 |
| `waiting` | 正常暂停，允许开放事项随完整步骤保存，后续信号可再次激活 |
| `incomplete` | 本次决定未完成；默认使步骤失败，显式 `collect` 阶段可收集其真实状态 |

动作自己的 `accepted/completed/rejected` 描述业务请求；DriverResult 描述整个激活。一次拒绝后主体可以继续决定，一次受理也可能需要等待后续过程。达到预算或输出截断应留下未完成原因。

领域处理器、Thread 写入等执行异常都会使步骤失败；`collect` 处理的是返回的 incomplete。配置与实践见[调度指南](../guide/schedule.md)和[主体教程](../tutorial/actors.md)。
