# Study Patterns

Use this reference when a researcher has a broad social phenomenon but not yet a runnable Society0 design. Treat these as starting patterns, not promises that every environment is built in.

## Communication And Platform Visibility

Question shape:

```text
How do recommendation, endorsement, relationship, or visibility rules affect trust, polarization, silence, diffusion, or misunderstanding?
```

Start with:

- environment: `social_network` when posts, feeds, likes, comments, reposts, follows, or recommendations matter.
- agents: LLM readers, sharers, opinion leaders, lurkers, moderators; optional rule accounts for baselines.
- FoVs: recommended feed, trending posts, notifications, source labels, friend endorsements.
- actions: publish, like, comment, repost, follow, do nothing.
- measures: stance distribution, trust score, sharing intent, cross-group exposure, reason text.

MVP: compare two feed strategies with 6-20 agents for 5-10 ticks and inspect one curve before scaling.

## Interview, Survey, And Deliberation

Question shape:

```text
How do different personas, exposures, or conversation partners change answers, reasons, or deliberation outcomes?
```

Start with:

- environment: `plain` for first surveys; `round_robin_conversation` for structured pairings or discussion.
- agents: LLM participants with clear persona and visible state; rule moderator if protocol must be deterministic.
- FoVs: stimulus, partner message, prior round summary, local conversation context.
- actions: use `interview(...)` for measurement; use `instruct(...)` with typed env actions when agents speak, critique, rank, vote, revise, or submit a group statement.
- measures: structured survey outputs plus qualitative explanations; for deliberation also record messages, critiques, ballots, rankings, and final statements.

MVP: one stimulus, two or three participant types, one interview schema, repeated runs. For LLM respondents, survey experiments, social psychology experiment replication, silicon samples, focus groups, Habermas Machine-style common ground, or deliberation, load `interview-survey-deliberation-simulation-design.md` and include a human benchmark, validity-rate check, or repeated-seed validation plan.

## Governance, Institution, And Permission

Question shape:

```text
How do rules, permissions, sanctions, or public signals change expression, compliance, cooperation, or legitimacy?
```

Start with:

- environment: custom env or `plain` plus code steps until rules stabilize.
- agents: citizens, platform users, administrators, organizations, observers.
- FoVs: public announcements, enforcement outcomes, visible peer behavior, resource or status changes.
- actions: speak, comply, report, moderate, allocate, appeal, withdraw.
- hosted constraints: mute status, resource access, role permission, penalty state.
- measures: expression rate, compliance, trust, perceived legitimacy, distribution of sanctions.

MVP: one policy change, one control condition, one visible enforcement record. For legislative, coalition, commons, norm-emergence, moderation, election, roll-call, accountability, or public-policy designs, load `governance-institution-public-policy-simulation-design.md` and define permissions, aggregation, enforcement, records, and validation before personas.

## Organization, Market, And Recommendation Systems

Question shape:

```text
How do role differences, information access, tool use, or market rules shape coordination and decision outcomes?
```

Start with:

- environment: `plain` for a controlled prototype; custom env after candidate pools, inventories, or workflows become reusable.
- agents: consumers, workers, firms, assistants, managers, banks, regulators.
- FoVs: current offers, candidate items, prices, inventory, public metrics, previous outcomes.
- actions: buy, work, recommend, rank, negotiate, approve, reject.
- measures: hit rate, average turns, allocation efficiency, price changes, unemployment, satisfaction.

MVP: one bounded task, such as whether a recommendation assistant can identify a hidden target item within a fixed number of turns.

## City, Community, And Emergency Behavior

Question shape:

```text
How do public warnings, local context, mobility constraints, and social ties affect behavior under a shared event?
```

Start with caution: map or city-specific environments may need custom implementation. Use `plain` first if geography is not essential to the first mechanism.

- agents: residents, officials, service workers, vulnerable groups, community leaders.
- FoVs: official warning, neighborhood condition, family or workplace messages, resource status.
- actions: travel, shelter, share information, request help, provide support.
- hosted constraints: location, mobility, resource access, risk status.
- measures: compliance, movement, unmet need, rumor spread, trust in official information.

MVP: two neighborhoods, one warning strategy, one mobility/resource constraint.

## Economy And Macro-Micro Aggregation

Question shape:

```text
Can heterogeneous household or firm decisions aggregate into macro patterns under policy or market rules?
```

Start with:

- environment: custom env if price, inventory, tax, bank, or government rules are central.
- agents: households, firms, government, bank; use rule agents for institutions unless language judgment is part of the study.
- FoVs: wages, prices, inventory, employment status, policy announcements, macro indicators.
- actions: work, consume, save, hire, invest, set policy.
- measures: inflation, unemployment, consumption, inequality, inventory, welfare.

MVP: rule-based macro mechanics first, then add LLM agents only where interpretation or expectations matter.

## Pattern Selection Rule

Pick the simplest pattern that makes the research mechanism visible:

- Use `plain` when the first problem is measurement, not environment complexity.
- Use `social_network` when visibility and diffusion are the mechanism.
- Use `round_robin_conversation` when interaction protocol is the mechanism.
- Write a custom env only when FoVs, actions, hosted constraints, or records will be reused across steps or studies.
