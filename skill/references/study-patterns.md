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

For consumer purchase, marketing promotion, word-of-mouth, buyer/seller agentic market, search, negotiation, transaction, marketplace welfare, or manipulation studies, load `consumer-marketing-marketplace-simulation-design.md`. Treat prices, catalogs, discounts, search results, budgets, proposals, payments, and transaction validity as environment mechanics, not agent prose.

## Education, Learning, And Classroom

Question shape:

```text
How do teacher scaffolds, peer relations, classroom scenes, or educational interventions change learning traces, misconceptions, participation, or social belonging?
```

Start with:

- environment: `plain` for one lesson or tutoring pilot; custom env when curriculum graph, grouping, classroom social network, assessment, or reusable lesson records matter.
- agents: students with mutable learning state, teacher/tutor agents, optional peer leaders or rule-based assessment baselines.
- FoVs: lesson material, prior student answer, teacher feedback, peer message, visible group work, assessment prompt.
- actions: ask, answer, explain, hint, challenge misconception, assign group, invite peer, support, exclude, submit work.
- measures: mastery, weak concepts, misconception persistence, participation, peer ties, assessment score, teacher reflection.

MVP: one topic, one teacher, 3-8 students, one misconception table, and a post-lesson trace review. For AgentSchool-inspired work, load `education-learning-classroom-simulation-design.md`.

## Law, Justice, And Legal Society

Question shape:

```text
How do laws, courts, enforcement, legal costs, or legal aid shape compliance, legal recourse, rights protection, or regulatory evasion?
```

Start with:

- environment: custom env once a law registry, docket, litigation cost, legal aid, or enforcement state persists across turns; `plain` only for a tiny closed-world vignette.
- agents: affected individuals, power holders, firms, residents, judges, legislators, enforcement roles.
- FoVs: current simulated law, personal harm, visible ruling, legal options, litigation cost, trust or transparency signal.
- actions: comply, violate, sue, seek legal aid, protest, strike, report, adjudicate, enforce, amend law.
- measures: action distribution, lawsuits filed, unmet legal need, rulings, law changes, welfare, rights protection, compliance.

MVP: one closed-world dispute, one current-law table, one docket, and a monthly law-review step. For Law in Silico-inspired work, load `law-justice-crime-simulation-design.md` and keep outputs non-operational.

## Public Health, Risk, And Health Behavior

Question shape:

```text
How do public warnings, health-risk information, vulnerability, social ties, or interventions shape attitudes and protective behavior?
```

Start with:

- environment: `plain` for a message/attitude pilot; custom env when hazard phase, resources, vulnerability, or network diffusion are central.
- agents: residents, patients, caregivers, officials, community ambassadors, vulnerable groups.
- FoVs: official warning, local risk status, news item, peer message, policy incentive, resource availability.
- actions: share, discuss, seek help, help neighbor, adopt protective action, delay, refuse, comply, stay, travel.
- measures: attitude distribution, protective-action adoption, unmet need, trust, perceived risk, vulnerability-stratified outcomes, risk message diffusion.

MVP: one risk scenario, one intervention/control, 10-20 agents, warmup, and explicit exposure records. For VacSim or heatwave designs, load `public-health-simulation-design.md`.

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

## International Relations, Crisis, And Security

Question shape:

```text
How do bounded diplomatic actions, communication, strategic uncertainty, or historical context shape escalation, restraint, alliance formation, or crisis stability?
```

Start with a high-risk boundary: research planning, mechanism exploration, historical interpretation, robustness checks, or safety evaluation only. Do not produce operational military advice, conflict prediction, targeting, evasion, or policy recommendation.

- environment: custom env for crisis state, relationship graphs, fixed action schemas, severity labels, public/private messages, dynamic variables, and stopping rules; `plain` only for a tiny strategic-game prototype.
- agents: LLM nation/delegate/game agents for interpretation and communication; rule agents or fixed strategies for baselines.
- FoVs: own profile, allowed actions, public and private messages, prior actions, visible relationship state, game horizon, or local historical context.
- actions: send message, propose agreement, accept/reject, wait, de-escalate, choose attack/do-nothing in abstract games, or submit a safety/risk classification.
- hosted constraints: valid targets, action severity, visibility, turn order, terminal action, crisis clock, relationship update, and non-operational scenario boundary.
- measures: escalation score trajectory, first extreme action, alliance graph similarity, treaty/message counts, crisis duration, attack timing, prompt/temperature sensitivity, and qualitative rationale categories.

MVP: use an abstract security dilemma with `attack`, `do_nothing`, and optional public messaging before building any richer crisis env. For WarAgent, EscalAItion, BattleAgent, strategic games, WarBench, or ARMOR-inspired work, load `international-relations-conflict-security-simulation-design.md`.

## Pattern Selection Rule

Pick the simplest pattern that makes the research mechanism visible:

- Use `plain` when the first problem is measurement, not environment complexity.
- Use `social_network` when visibility and diffusion are the mechanism.
- Use `round_robin_conversation` when interaction protocol is the mechanism.
- Write a custom env only when FoVs, actions, hosted constraints, or records will be reused across steps or studies.
- For high-risk IR/security mechanisms, start with abstract or historical research scaffolds and keep the action surface coarse, typed, auditable, and non-operational.
- For education, legal, public-health, and consumer-market mechanisms, prefer small domain pilots with inspectable state and records before scaling; these domains need external validation before consequential claims.
