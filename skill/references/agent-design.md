# 主体设计

主体共享一个环境。ActorRecord 的人格、主观状态、配置与角色表达主体差异；资产、资格和领域对象由环境机制维护。ActorStore 的驱动名称映射到轻量工厂，规则直接传映射，模型驱动通过 actor_plugin 的 requires 与 driver_factory 在安装时取得共享服务。按实际激活构造 Driver，完整人格和主观状态按认知合同进入 LLM。

## 驱动

RuleDriver 使用相同 Session 信息与行动门面执行确定规则。LLMDriver 维护完整 Thread、动态元工具、行动预算与结构化测量；CognitiveInput 保留单来源 tuple 协议，多来源通过 cognition_plugin 的 SourceSpec 显式绑定独立位置。常用配置沿 [规则与模型配方](../../docs/core-next/builtin-composition.md) 装配，完整运行见 [公共组合示例](../../examples/core_next/README.md)。临时提醒通过激活信号传递，不隐式积累到人格或权威主观状态。

## Persona Design

Good persona text is specific but not overloaded:

```text
A middle-aged parent who often reads neighborhood social media posts, cares about school safety, and distrusts anonymous sources.
```

Avoid:

- hiding treatment assignment in persona.
- packing many unrelated psychological traits into one paragraph.
- using stereotypes as a substitute for constructs.
- changing persona mid-run unless the design explicitly studies identity shifts.

## State Design

Use small numeric or categorical state for variables that need to change over ticks:

```python
"state": {
    "trust": 0.45,
    "attention": "medium",
    "topic_familiarity": 0.2,
}
```

Keep scales documented in the step or output schema. If a variable is measured from an interview, write it to tables and metrics; only copy it back into state when the simulation mechanism requires future behavior to depend on it.

When using LLM agents, state is part of the agent's self-description. That is useful for attention, belief, emotion, resources, and current role. It is dangerous for hidden variables. If a participant should not know they are in the treatment group, do not put `"condition": "treatment"` in `state`.

## 经验与阶段

LLMDriver 按主体、Moment 与模式分别保存 Thread。跨轮已有原文可供研究者追查；下一轮的自动经验输入由记忆提取和召回机制提供。向研究者解释时分别说明会话记录、跨轮记忆和当前感知材料。

行为实验若要求每次激活至少完成某类真实动作，同时设置 `required_tags` 与 `completion_tags`，例如两者均含 `social_write`：前者要求该类动作成功，后者在成功后结束激活。单独设置 `completion_tags` 仍允许纯文本自然结束。试运行后从动作记录核对实际次数；允许不行动的研究设计应单独说明其完成条件。

MemoryPolicy 的 auto_write、auto_recall、active_tools 独立，policy_selector 每激活冻结。关闭召回保持当前感知材料；提取空结果与提供方失败分别处理。默认衰减时钟为完整步骤序号，业务日期原文保留。需要显式经验保存时使用 starter 的完成钩子。

reasoning_stages 提供阶段名称、指导与可选输出标记，保留原始响应和片段引用，不额外调用模型。模型选择可按激活、主体、类型和默认配置明确优先级，使用现有共享提供方实例。
