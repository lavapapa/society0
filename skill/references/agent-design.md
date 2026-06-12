# Agent Design

Use this reference when configuring participants, organizations, bots, institutions, or interview subjects.

## Agent Types And Instances

`agent_types` define reusable categories. `agents` define concrete participants.

```python
config = {
    "agent_types": [
        {
            "id": "reader",
            "archetype": "llm",
            "persona": "A social media user who reads public health news.",
        }
    ],
    "agents": [
        {
            "id": "alice",
            "type": "reader",
            "persona": "A cautious reader with low institutional trust.",
            "state": {"trust": 0.35, "fatigue": 0.2},
            "properties": {"condition": "treatment"},
        }
    ],
}
```

The runtime resolves:

- `type`: links an agent to `agent_types`.
- `archetype`: `llm` or `rule`; instance value overrides type value.
- type-level `persona`: general role definition.
- instance-level `persona`: individual identity; overrides or complements type persona.
- `state`: mutable current condition and self-knowledge.
- `properties`: metadata for selection, grouping, and researcher labels.
- `model`: optional model id for future model routing.

## What To Put Where

Use `persona` for stable natural-language identity:

- social role.
- values or background relevant to the question.
- communication style.
- durable attitude if it should behave like identity.

Use `state` for mutable variables the agent may know:

- trust, fatigue, mood, knowledge, attention, belief.
- current role in a conversation.
- private memory-like variables that are meant to be visible to the agent.

Use `properties` for researcher-facing metadata:

- treatment/control condition.
- sampling strata.
- demographic labels not intended for the prompt.
- debugging tags and selectors.

Current important behavior: agent `state` is included in the LLM system prompt. `properties` are not presented as current state. Do not put hidden truth, blind condition labels, answer keys, or experimenter-only annotations in `state` for LLM agents.

Think of the split this way:

- `persona`: durable social identity and voice.
- `state`: mutable self-knowledge the agent is allowed to use when reasoning.
- `properties`: researcher metadata and grouping labels.
- env-hosted state: constraints or situational facts controlled by the environment.

For rule agents, `persona` is usually unnecessary. They can still use `state` as a deterministic data store.

## Rule-Based Agents

Use rule agents when:

- behavior should be deterministic.
- the agent is a platform actor, fixture, or baseline.
- you need a control condition without LLM variance.
- the update can be expressed as a simple function over state.

Rule agents usually act through code steps:

```python
@engine.step(name="rule_baseline")
async def rule_baseline(ctx):
    for agent_id in ctx.agents.where(type="reader", archetype="rule").ids():
        state = ctx.world.agents_data[agent_id]["state"]
        state["trust"] = max(0.0, state["trust"] - 0.02)
```

## LLM-Based Agents

Use LLM agents when the behavior depends on language, interpretation, memory, identity, persuasion, social meaning, or interviews.

For LLM agents, always check:

- `Society0(..., llm=LLMModel(...), embed=EmbedModel(...))` is configured.
- the env supplies enough FoV context.
- actions are constrained to what the agent can plausibly do.
- output schemas are simple and explicit.
- concurrency is low enough for the provider.

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

## Memory

LLM agents can retrieve and save Chroma-backed memory.

- `instruct(..., memory=True)` retrieves and saves memory.
- `interview(..., retrieve_memory=True, save_memory=False)` measures without writing memory by default.
- Use memory when past exposure or relationship history should affect later behavior.
- Avoid memory for a simple one-shot survey unless memory is part of the design.

Memory is agent-side experience, not env state. Treat it as part of the simulated subject's history. The environment may create situations that produce memories, but it should not directly read private memory to decide what the agent sees.

Use memory deliberately:

- diffusion studies: prior exposure and repeated endorsement can matter.
- conversation studies: past turns should influence later replies.
- trust studies: remembered sources can affect credibility.
- baseline surveys: memory may add noise if not part of the mechanism.

## Reasoning Stages

The agent loop supports staged reasoning. The default stages are roughly:

```python
[
    {"name": "思考", "desc": "思考当前情况，分析信息"},
    {"name": "回答", "desc": "给出回答或执行行动"},
]
```

The public `AgentGroup.instruct(...)` and `AgentGroup.interview(...)` wrappers expose `reasoning_stages`. For most user experiments, prefer clear instructions and output schemas first. Add custom stages when the study explicitly needs a decision procedure.

Reasoning stages are powerful but easy to overfit. Use them only when the study explicitly compares decision procedures, such as risk assessment, deliberation, planning, or reflection. Otherwise, simple instructions plus clear FoVs and output schemas are usually more reliable.

## Agent-Environment Interaction Pattern

Typical LLM behavior step:

```python
users = ctx.agents.where(type="reader", archetype="llm")
interaction = await users.instruct(
    "Read your feed and decide whether to like, comment, repost, follow, or do nothing.",
    fovs=["recommended_feed"],
    actions=["environment"],
    memory=True,
    max_turns=3,
)
```

Typical measurement step:

```python
survey = await users.interview(
    "Rate the credibility of the information you just saw from 1 to 5 and explain briefly.",
    fovs=["recommended_feed"],
    output=TrustSurvey,
    retrieve_memory=True,
    save_memory=False,
)
```

Use `instruct` to let agents change the environment. Use `interview` to measure agents without ordinary actions.

## Agent Design Checklist

- What role does this agent represent in the research question?
- Which parts of the role belong in type-level persona?
- Which parts make this individual different?
- Which state should the agent know about?
- Which researcher labels must stay hidden in `properties` or output tables?
- Should this be LLM-based, rule-based, or both as treatment and baseline?
- Which FoVs and actions does the environment give this agent?
- What should be measured by interview rather than baked into behavior?
