# Research Design

## From Observation To Simulation

Start by translating the user's everyday observation into a research scaffold:

1. Phenomenon: what pattern or puzzle is being observed?
2. Research question: what relation, mechanism, or condition should be probed?
3. Constructs: which concepts need measurable proxies?
4. Environment: what platform, institution, media field, or interaction setting shapes visibility and action?
5. Agents: who perceives, remembers, acts, and explains inside that environment?
6. Intervention/control: what changes between conditions?
7. Step loop: what happens each tick?
8. Measurements: what numeric metrics and qualitative records are written?
9. Validity: what baselines, repeated runs, ablations, and sensitivity checks are needed?

When the user's starting point is vague, translate it through three mechanism axes:

- **Information and visibility**: who sees what, when, in what order, and with what platform or institutional filtering.
- **Institution and permission**: who can act, who is constrained, what sanctions or incentives exist, and what actions leave records.
- **Subject and time**: which heterogeneous groups exist, what they remember, and what a tick means in the study.

These axes usually map cleanly into environment design, agent design, and code steps.

## Methodological Position

Society0 should not be framed as traditional ABM with a larger behavior function. It is better understood as language-mediated multi-agent simulation:

- Agent behavior may depend on prompts, persona, memory, FoVs, model provider, and available actions.
- It can explore social meaning, interpretation, persuasion, trust, identity, and platform visibility.
- It is not direct empirical evidence. It is simulated evidence that needs careful robustness checks.

Borrow from ABM:

- explicit mechanisms.
- transparent assumptions.
- baselines and controls.
- repeated runs.
- sensitivity analysis.
- careful separation between mechanism exploration and empirical claim.

## Environment-First Design

Do not start by making a list of agents. Start by making the social situation precise:

1. What setting is being simulated?
2. What information is visible to each participant?
3. What actions are possible in that setting?
4. What traces do those actions leave?
5. What parts of the experimental condition must stay hidden?
6. What metrics and qualitative tables will be recoverable from the run?

This is especially important for LLM agents. LLMs can invent context unless FoVs and actions constrain them. A realistic env gives the agent enough situated evidence to reason from, while preventing the prompt from becoming an all-knowing description of the experiment.

A good environment-first design can be summarized as:

```text
Setting -> FoVs -> Actions -> Hosted constraints -> Records -> Measurements
```

Only after that should the agent set be expanded. The agent is meaningful because it is situated in a specific visibility and action structure.

## Agent Choice

Use LLM-based agents for:

- interpreting messages.
- interviews and surveys.
- persuasion, trust, emotion, identity, ideology.
- memory-dependent behavior.
- natural-language interaction.

Use rule-based agents for:

- deterministic baselines.
- known mechanisms.
- platform/system actors.
- fixtures and tests.
- parameter sweeps where LLM variance would obscure the mechanism.

Strong studies often use both. A rule baseline can show what the LLM layer adds.

## Environment Choice

Use `plain` when:

- the first version is a survey, interview, or state-transition prototype.
- the researcher is still clarifying constructs.
- the experiment can be expressed in code steps.

Use `social_network` when:

- exposure, feeds, posts, replies, likes, recommendations, or network structure matter.
- the research question concerns platform visibility or information diffusion.

Use `round_robin_conversation` when:

- pairings, conversations, interviews, or rotating interactions matter.

## Example Scaffold

Observation:

```text
People seem more likely to trust misinformation when it appears to be endorsed by friends.
```

Simulation scaffold:

```text
Research question:
  Does friend endorsement increase perceived credibility of a false post?

Agents:
  skeptical readers, high-trust readers, frequent sharers.

Environment:
  a feed with posts, source labels, and endorsement counts.

Treatment:
  false post with high friend endorsement.

Control:
  same false post with low or no friend endorsement.

Measurements:
  trust_score, sharing_intent, reason text, memory references.
```

Start with a small `plain` experiment. Move to `social_network` after the measurement loop is stable.

## MVP Rule

Start with the smallest run that can produce one interpretable curve, table, or qualitative contrast. Do not begin with a full city, economy, or thousands of agents unless a smaller run has already validated:

- the environment state updates.
- the FoVs show the intended evidence.
- the action space is narrow enough to audit.
- output schemas parse consistently.
- metrics answer the research question.

After the first interpretable run, add complexity one layer at a time: more agent heterogeneity, richer FoVs, memory, repeated runs, treatment matrix, then scale.

## Agent Configuration Choice

After the env is clear, configure agents:

- type-level persona for shared role.
- instance-level persona for individual variation.
- `state` for mutable variables the agent is allowed to know.
- `properties` for researcher labels, hidden conditions, and grouping metadata.
- LLM archetype for interpretation and language behavior.
- rule archetype for baselines and deterministic mechanisms.

Never put hidden treatment assignment or ground truth into visible LLM state.

## Step And Time Choice

`ctx.step` is a simulation tick, not automatically a day, hour, or real timestamp. Define it in research terms:

- one feed browsing round.
- one conversation turn.
- one public opinion cycle.
- one policy phase.
- one quarter in a macro model.

For many studies, a three-phase script is easier to reason about than realistic calendar time: shock, adaptation, stabilization. Implement each phase as code inside steps or as step params, and record which phase produced which outputs.
