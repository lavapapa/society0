# International Relations, Conflict, And Security Simulation Design

Use this guide for Society0 simulations about international relations, crisis
escalation, conflict/security mechanisms, strategic games, diplomacy/security
decision-making, or historical conflict and battle emulation as research
mechanisms.

Load it after `research-design.md`, `environment-design.md`,
`agent-design.md`, and `step-dsl.md`; also load
`simulation-paper-distillation.md` when adapting a paper. Use the governance
guide when formal institutions, public accountability, lawmaking, moderation, or
policy participation are the main mechanism. Use city/emergency, economics, or
communication guides when those domains host the actual behavior.

Contents:

- High-risk boundary
- Evidence map
- Target taxonomy
- Loading order
- Society0 construction rules
- Pattern notes
- Minimal scaffold checklist
- Baselines, ablations, and validation
- Reproduction boundaries
- Failure modes

## High-Risk Boundary

IR, crisis, conflict, and security simulations are high-risk research tools.
They can support scenario rehearsal, mechanism exploration, robustness checks,
historical interpretation, pedagogy, benchmark construction, and research
planning. They must not be used as operational military advice, conflict
prediction, targeting support, evasion guidance, force employment planning, or
policy recommendation.

Keep every Society0 design provider-neutral and research-bounded:

- anonymize or fictionalize contemporary high-risk scenarios unless the user
  supplies an approved research protocol;
- use coarse, bounded, typed actions rather than operational action detail;
- keep all lethal, targeting, evasion, and tactical optimization content out of
  public docs and examples;
- require expert review, legal/ethical review, human oversight, and independent
  validation before any consequential use;
- report model/provider, prompt, temperature, seed, action schema, scenario
  assumptions, and validation gaps for every run.

## Evidence Map

Read `simulation-paper-distillation.md` before using this guide for paper
reproduction. Candidate lists are not evidence.

| Source | Evidence status | Society0 lesson |
| --- | --- | --- |
| Hua et al., *War and Peace (WarAgent): Large Language Model-based Multi-Agent Simulation of World Wars*, arXiv:2311.17227, and official `agiresearch/WarAgent` README/code | Supported by paper and official code/config | Model historical IR as country agents in a hosted world with country profiles, action space, relationship board, internal record/stick, secretary checks, run logs, and graph-style historical metrics. Treat as historical mechanism exploration, not prediction or policy authority. |
| Rivera et al., *Escalation Risks from Language Models in Military and Diplomatic Decision-Making* / EscalAItion, FAccT 2024, and official `jprivera44/EscalAItion` README/code | Supported by paper and official code/config | Use fixed action sets, nation/world models, day-level concurrent turns, dynamic variables, action severity, escalation scores, prompt/model/temperature sensitivity checks, and explicit deployment caution. |
| Elbaum and Panter, *Managing Escalation in Off-the-Shelf Large Language Models*, arXiv:2508.01056 | Supported by paper | Treat prompt wording and temperature as experimental treatments. Lower temperature and reflection/de-escalation prompts changed escalation outputs in their partial replication; do not infer a universal safety guarantee. |
| Douglass et al., *What is Escalation?*, arXiv:2402.03340 | Supported by paper | Measure escalation as a process over crisis-time steps. Distinguish correlation, explanation, prediction, and prescription; track actions, speech, actors, targets, timing, and time-to-crisis-end style outcomes. |
| Fan et al., *BattleAgent: Multi-modal Dynamic Emulation on Historical Battles*, arXiv:2404.15532, and official `agiresearch/BattleAgent` README/code | Supported by paper and official code/config, but historical emulation/sandbox evidence only | Use only for historical emulation design: spatial sandbox, quantized time, map FoVs, typed action space, dynamic agent structure, observer-estimated casualties, and comparison with historical records. Do not transfer it into operational tactical planning. |
| Chupilkin, *Multi-Agent Strategic Games with LLMs*, arXiv:2605.03604 | Supported by paper; public code availability not verified in this pass | Use simple repeated security games to probe strategic mechanisms such as polarity, finite horizons, and communication. Treat LLMs as experimental subjects, not state actors. |
| Li et al., *WarBench*, arXiv:2603.21280 | Supported by paper as safety/evaluation boundary evidence only | Use as evidence that tactical/security LLM evaluation requires legal/ethical constraints, fog-of-war stress tests, deployment-condition stress tests, and explicit non-operational disclaimers. Do not use as an operational template. |
| *ARMOR 2025: A Military-Aligned Benchmark for LLM Safety* | Supported by paper as safety/evaluation boundary evidence only | Use as evidence that safety evaluation must be doctrine/constrained-judgment specific and that strong benchmark performance is not autonomous readiness. Do not use as an operational template. |
| RAND/MDPI PDFs referenced during evidence collection | Unknown or unavailable | They were blocked or unavailable in this pass; omit or mark unavailable rather than citing as inspected. |

## Target Taxonomy

### Historical International Conflict Mechanism Exploration

Use when the study asks how alliances, treaties, mobilization, public morale,
historical grievances, triggers, or communication patterns shape a historical
conflict trajectory.

Supported by paper and official code/config:
- WarAgent represents countries with profiles and actions, uses a board for
  international relationships, uses a stick/internal record, and includes a
  secretary-agent check for action appropriateness and consistency.
- WarAgent evaluates alliance grouping with mutual information and war
  declaration/mobilization edge overlap with Jaccard similarity.
- WarAgent reports prompt sensitivity: aggressive framing increases early war
  declarations, while conservative framing yields alliances, non-intervention
  treaties, and peace agreements in the checked experiments.
- WarAgent lists limitations around communication timing, espionage, publicity
  levels, mobilization delays, synchronous rounds, and stopping criteria.

Inference for Society0 mapping:
- Environment: historical diplomacy env with countries, relationship graph,
  public/private message records, treaty records, mobilization state, triggers,
  and optional domestic record.
- FoVs: own profile, visible relationships, public messages, private messages
  addressed to the country, prior actions, trigger event, and relevant historical
  constraints.
- Actions: `send_message`, `propose_alliance`, `accept_alliance`,
  `propose_non_intervention`, `sign_peace`, `mobilize`, `declare_war`,
  `wait`, `request_mediation`.
- Hosted constraints: one or more actions per round, message visibility,
  treaty eligibility, mobilization delay, valid target, alliance graph update,
  and stopping rule.
- Measures: alliance partition similarity, declaration/mobilization edge
  overlap, first-conflict timing, treaty counts, connected-board stability,
  prompt sensitivity, and qualitative event traces.

Do not frame a historical trajectory as inevitable. Use repeated runs,
counterfactual treatments, and labeled interpretation boundaries.

### Crisis Escalation And Diplomatic-Security Decision Simulation

Use when the research target is whether models, prompts, action schemas, or
scenario conditions produce escalation, de-escalation, arms racing, signaling,
or crisis termination patterns.

Supported by paper and official code/config:
- EscalAItion runs eight nation agents over 14 simulated days, with an LLM
  nation model and an LLM world model.
- Its fixed action configuration includes peaceful, temperate, provoking,
  extreme, and nuclear-severity actions, plus dynamic effects on variables such
  as military capacity, GDP, trade, resources, political stability, soft power,
  cybersecurity, territory, population, and nuclear capability.
- Its world validates action names and target nations, records daily action
  history, updates dynamic variables, and stores world-model consequence
  summaries.
- Its scripts expose `temperature`, prompt ablations, nation/action config
  files, day-zero scenario, model choices, and mock-model runs.
- The FAccT paper recommends further examination and cautious consideration
  before deploying autonomous language-model agents for strategic military or
  diplomatic decision-making.

Supported by paper:
- The escalation-risk paper found escalation patterns across studied
  off-the-shelf models, including arms-race dynamics and rare nuclear actions.
- The managing-escalation paper reports that lower temperature and
  reflection/de-escalation prompts reduced average escalation scores in its
  partial replication, while emphasizing model/context dependence.

Inference for Society0 mapping:
- Environment: crisis env with nation profiles, dynamic state variables,
  action schema, day counter, scenario history, and consequence records.
- FoVs: own goals and state, known other-nation profiles, prior day actions,
  world-model consequence summary, and allowed actions.
- Actions: fixed typed actions with severity labels and validated targets.
  Keep public examples coarse and non-operational.
- Hosted constraints: maximum actions per nation/day, valid targets, conflict
  status requirements, action visibility, severity labels, dynamic-variable
  updates, and end-of-day consequence summarization.
- Measures: escalation score over time, violent/nuclear action ratio, arms-race
  indicators, de-escalatory action counts, variance across seeds, prompt
  treatment effects, temperature effects, and qualitative justifications.

Unknown or unavailable:
- Escalation scores are design-specific; do not present them as universal
  conflict risk measures.
- The RAND source used by the managing-escalation authors was not inspected in
  this pass.

### Escalation As A Process Measurement Problem

Use when the study asks how to define, code, or compare escalation over a
sequence rather than merely classify a final outcome.

Supported by paper:
- *What is Escalation?* uses ICB crisis narratives and ICBe/ICBeLLM event coding
  to create crisis-time steps.
- Its time step is a narrative sentence with coded events; each time step can
  include multiple actor/action events.
- It measures escalation/de-escalation through predicted time remaining until
  crisis end and treats behavior contributions as correlational, not causal.
- It warns that scale and time-step definition do much of the conceptual work
  and that measurement, explanation, prediction, and prescription are often
  confused.

Inference for Society0 mapping:
- Record every crisis event as `{step, actor, target, action_type, speech_type,
  visibility, severity, consequence, source_prompt, parsed_action}`.
- Keep separate fields for behavior labels, observed outcome, estimated
  contribution, and researcher interpretation.
- Build metrics that can be recomputed from event tables: time to first
  extreme action, time since last threat, de-escalatory action density, crisis
  duration, and transition to stable state.
- Use `interview` only for post-hoc measurement, such as perceived risk or
  rationale coding; use `instruct` with actions for world-changing behavior.

Do not convert correlational escalation scores into prescriptions.

### Strategic Games And Security Dilemma Experiments

Use when the goal is controlled theory testing: polarity, communication, finite
horizons, preemption, reciprocity, signaling, or cooperation under uncertainty.

Supported by paper:
- The strategic-games paper uses repeated security dilemmas where agents choose
  `attack` or `do_nothing`; attack terminates the game.
- It varies two versus three actors, finite known horizon, and public
  communication.
- It reports treatment patterns consistent with canonical mechanisms and uses
  private reasoning/public messages to connect choices to strategic logics.
- It explicitly states that LLM agents are not human decision-makers or states
  and that the method complements, not replaces, historical analysis, formal
  theory, human experiments, or expert surveys.

Inference for Society0 mapping:
- Environment: minimal repeated game with actor count, horizon condition,
  payoff ranking, communication channel, and termination state.
- FoVs: current period, prior choices, public messages, payoff ordering, and
  horizon information only if assigned to that treatment.
- Actions: `attack`, `do_nothing`, and optionally `send_public_message`.
- Measures: conflict incidence, period of first attack, unilateral versus
  simultaneous attack, message categories, private-reasoning categories,
  model fixed effects, and leave-one-model-out robustness.

This is the safest pattern for new IR theory work because the action surface is
small, auditable, and non-operational.

### Historical Battle Emulation And Sandbox Reconstruction

Use only for historical interpretation, pedagogy, and research sandboxing around
documented past battles. Do not use it for modern operational planning.

Supported by paper and official code/config:
- BattleAgent uses historical maps, textual and visual observations, quantized
  15-minute intervals, dynamic agent structure, and a 51-action space.
- Its action categories include reposition, preparation, attack, defense,
  observation, and retreat.
- Agents can move by coordinates, interact with landscape features, fork/merge
  or prune sub-agents, and interact with other agents.
- An LLM observer estimates casualties using agent state, action details,
  locations/distances, landscape information, and weapon parameters.
- Its evaluation compares final casualties with historical data and uses human
  analysis of movement and action reasonableness; the paper says quantitative
  evaluation is mostly limited to casualty counts.

Inference for Society0 mapping:
- Environment: historical spatial sandbox with map, coordinates, terrain,
  troop counts, unit hierarchy, time step, visibility range, and casualty
  records.
- FoVs: local map description, nearby allies/enemies, current mission, action
  history, visible terrain, and own unit state.
- Actions: coarse historical-emulation actions such as `move_to`,
  `observe_area`, `prepare_position`, `split_unit`, `merge_unit`, `retreat`,
  `signal_ally`, and non-operational generic interaction actions.
- Hosted constraints: coordinate bounds, movement speed, unit count,
  visibility radius, terrain effects, hierarchy, and stopping criteria.
- Measures: historical casualty distance, movement plausibility, action
  plausibility, convergence/stability, and qualitative diary/traces.

Keep public Society0 examples non-tactical. If a user asks for modern battle
planning, targeting, evasion, or force-employment optimization, refuse that use
and offer a safety/evaluation or historical-pedagogy variant.

### Safety And Evaluation Boundary Studies

Use when the question is whether LLM systems are safe, robust, or aligned enough
for high-risk defense/security workflows.

Supported by paper:
- WarBench includes a content warning and says the framework is strictly for
  academic research and adversarial AI safety evaluation, not operational
  military deployment, tactical planning, or kinetic action.
- WarBench emphasizes legal/ethical constraints, fog-of-war stress tests, edge
  or time constraints, explicit reasoning, and real-source grounding as
  evaluation dimensions.
- ARMOR 2025 is scoped to doctrine-constrained multiple-choice decisions and
  states that strong benchmark performance does not imply readiness for
  autonomous use.
- ARMOR 2025 identifies failures around hallucinated rules, refusal of lawful
  scoped requests, and difficult normative reasoning under uncertainty.

Inference for Society0 mapping:
- Use benchmark-style simulations to test refusal, hallucinated constraints,
  uncertainty handling, legal/ethical rule recognition, and human-escalation
  triggers.
- Keep scenarios anonymized, desensitized, and non-operational.
- Use action outputs such as `flag_risk`, `request_human_review`,
  `identify_missing_information`, `refuse_unsafe_request`, and
  `classify_constraint`, not operational courses of action.

## Loading Order

For IR, conflict, crisis, and security work, load only the files needed for the
target:

1. `research-design.md`
2. `environment-design.md`
3. `agent-design.md`
4. `step-dsl.md`
5. `simulation-paper-distillation.md` when adapting a paper
6. This guide
7. `governance-institution-public-policy-simulation-design.md` when formal
   authority, policy practice, lawmaking, participation, accountability, or
   institution design dominates
8. `communication-social-media-simulation-design.md` when public messaging,
   propaganda, rumor, media, or platform diffusion dominates
9. `city-community-emergency-simulation-design.md` when place, mobility,
   evacuation, preparedness, or infrastructure hosts the mechanism
10. `run-monitor-analyze.md` for repeated runs, score tables, trace review, and
    validation

Load this guide when the user mentions international relations, IR, diplomacy,
security dilemma, strategic game, crisis escalation, de-escalation, wargame,
wargaming, conflict simulation, war simulation, WarAgent, EscalAItion,
escalation risks, military/diplomatic decision-making, BattleAgent, historical
battle emulation, WarBench, ARMOR, arms race, deterrence, alliance formation,
finite horizon, multipolarity, public signaling, or crisis stability.

## Society0 Construction Rules

Use this env-first split:

| Component | Society0 location |
| --- | --- |
| Scenario assumptions, actor set, relationship graph, crisis history, action schema, severity labels, dynamic variables, map, turn clock, stopping rule, benchmark labels | env config, env state, rules, step params, or hidden `properties` |
| Country or actor profile, goals, doctrine-like abstract preferences, public morale, resources, memory, current state | agent `state` if visible; agent `properties` if hidden |
| True treatment arm, benchmark answer, red-team label, hidden scenario source, evaluation key | hidden `properties`, run config, or output tables, not FoV |
| Prior actions, public/private messages, local observations, allowed actions, current dynamic state, consequence summary | FoV |
| World-changing diplomatic/security moves, game choices, messages, treaty actions, movement in historical sandbox | `instruct` with typed env actions |
| Post-hoc risk rating, rationale coding, perceived intent, trust, explanation, qualitative reflection | `interview` |
| Relationship updates, action validation, severity scoring, dynamic-variable transitions, game termination, casualty/emulation update, safety guardrail checks | env rules, behaviors, or code steps |
| Action trace, prompt/FoV version, model/provider, seed, parsed action, validation failure, score table, qualitative trace | `ctx.result(metrics=..., tables=...)` plus run artifacts |

Design rules:

- Define the scenario boundary before agents. State whether it is historical,
  fictional, anonymized, abstract game, or safety benchmark.
- Keep actions coarse, bounded, and typed. Public docs should never provide
  tactical, targeting, evasion, or force-employment detail.
- Let the environment own consequences. LLM agents may reason and choose, but
  the env validates targets, updates relationships/state, applies scoring, and
  records outcomes.
- Separate public, private, and hidden information. Do not show agents benchmark
  answers, hidden treatment labels, true intent, or future events.
- Treat prompt wording, temperature, model, and action schema as experimental
  conditions. Record them and run sensitivity checks.
- Use memory only when prior interactions, diplomacy, or historical experience
  are part of the mechanism. Disable or simplify memory only as an explicit
  ablation.
- Use `terminal_actions` only for semantic endpoints such as final vote,
  finalized treaty, attack/no-attack game decision, or benchmark submission.
- Make escalation recomputable from tables. Do not bury scores only in summary
  prose.
- Preserve human oversight boundaries in the study design: risk flags, expert
  review notes, and "not for operational use" metadata belong in outputs.

## Pattern Notes

### Fixed Action Sets Are Safety And Method Controls

Supported by paper and official code/config:
  EscalAItion and the strategic-games paper both constrain decisions to fixed
  action sets. WarAgent and BattleAgent also expose explicit action spaces.

Inference for Society0 mapping:
  Use action schemas to preserve construct validity and reduce operational
  drift. A narrow strategic game might have only `attack`, `do_nothing`, and
  `send_public_message`. A crisis study might use coarse diplomatic/security
  actions with severity labels. A safety benchmark should use
  `flag_risk`/`request_human_review` style actions rather than operational
  recommendations.

### World Models Are Experimental Conditions

Supported by official code/config:
  EscalAItion uses a separate world model to summarize daily consequences,
  while env code applies dynamic-variable updates from action config.

Inference for Society0 mapping:
  Treat a consequence summarizer as a model component that can introduce bias or
  variance. Record its prompt/model and ablate it against deterministic
  consequence rules when possible.

### Prompt And Temperature Sensitivity Are Core Results

Supported by paper:
  WarAgent, EscalAItion, and the managing-escalation paper all show that prompt
  framing, system instructions, or temperature materially affect conflict or
  escalation outcomes.

Inference for Society0 mapping:
  Every IR/security pilot should include at least one prompt or temperature
  sensitivity check before any interpretive claim.

## Minimal Scaffold Checklist

Before writing code, specify:

- research question and allowed interpretation;
- high-risk boundary and explicit non-operational use statement;
- scenario type: abstract, fictional, historical, anonymized, benchmark, or
  supplied research protocol;
- actor roles and whether they represent states, delegates, commanders,
  benchmark respondents, or abstract game agents;
- FoVs and hidden fields;
- fixed action schema, target validation, severity labels, and terminal actions;
- turn order and concurrency assumptions;
- environment consequence rules or world-model summarization plan;
- metrics and event tables that make scores recomputable;
- baselines, ablations, repeated seeds, and model/provider comparisons;
- validation source and remaining evidence gaps.

## Baselines, Ablations, And Validation

Minimum baselines:

- rule agent or fixed strategy baseline;
- no-communication or communication-disabled condition when messaging matters;
- action-free measurement baseline when only perceptions are measured;
- deterministic consequence-rule baseline when a world model is optional.

Minimum ablations:

- prompt framing;
- temperature;
- model/provider;
- memory on/off only when memory is theoretically relevant;
- action schema severity labels or allowed-action set;
- public versus private communication where communication publicity matters.

Validation options:

- historical event trace comparison for historical IR mechanisms;
- graph similarity for alliances, declarations, mobilization, or treaty edges;
- escalation score trajectory and first-extreme-action timing;
- crisis-time-step process metrics;
- expert qualitative review of plausibility, not policy endorsement;
- robustness across seeds and leave-one-model-out checks.

## Reproduction Boundaries

Use these fidelity labels:

- **exact**: paper/code specifies actors, FoVs, action schema, schedule,
  prompts/config, scoring, baselines, and validation, and Society0 implements
  them directly.
- **approximate**: the mechanism is clear, but Society0 differs in interface,
  provider, model, consequence rules, or unavailable prompt/config details.
- **extension**: inspired by paper evidence but intentionally changes the
  scenario, action schema, or measurements.
- **unsupported**: missing paper/code/data or operational details would be
  required to claim reproduction.

For this evidence pass:

- WarAgent: approximate-to-exact for high-level historical diplomacy mechanisms
  and graph metrics when using the official code/README; exact prompt fidelity
  requires reproducing source prompts and scenario data.
- EscalAItion: approximate-to-exact for fixed action config, nation config,
  world updates, and escalation score design; exact claims require matching run
  config, model versions, prompts, and analysis scripts.
- BattleAgent: approximate historical-emulation evidence only; do not use for
  modern tactical simulation.
- Strategic-games paper: paper-supported method; code was not verified here.
- WarBench and ARMOR: safety/evaluation boundary evidence only.
- RAND/MDPI: unavailable in this pass.

## Failure Modes

- Prompt-only geopolitics: agents discuss conflict without env-owned actions,
  records, or consequences.
- Operational drift: a research sandbox quietly becomes tactical advice,
  targeting, evasion, or policy recommendation.
- Hidden global knowledge: agents see true scenario labels, future events,
  benchmark answers, or target states they should not know.
- Universal escalation score: one paper's severity score is treated as a
  general measure of real-world conflict risk.
- Unlogged sensitivity: model, temperature, prompt, world model, or action
  schema changes without being recorded as a treatment.
- Outcome-only analysis: final war/no-war or casualty counts are reported
  without process traces.
- False historical inevitability: a few runs are interpreted as showing that a
  conflict was inevitable.
- Safety benchmark misuse: WarBench/ARMOR-style materials are treated as
  operational templates rather than evaluation boundaries.
- No human-review path: high-risk outputs lack expert review, accountability,
  and a clear "not for operational use" statement.
