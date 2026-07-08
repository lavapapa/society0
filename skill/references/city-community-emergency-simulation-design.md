# City, Community, Emergency, Mobility, And Traffic Simulation Design

Use this guide for Society0 simulations about cities, neighborhoods,
communities, emergency preparedness, evacuation, mobility, participatory urban
planning, traffic signal control, and urban-task evaluation. Load it after
`research-design.md`, `environment-design.md`, `agent-design.md`, and
`step-dsl.md`; also load `simulation-paper-distillation.md` when adapting a
paper.

This guide is not an operational emergency plan, traffic-control directive, or
city-policy recommendation. It distills design moves from LLM-based urban and
emergency simulation papers into Society0's env-first scaffold: spatial setting,
FoVs, actions, hosted constraints, records, measurements, baselines, ablations,
and interpretation boundaries.

Contents:

- Domain stance
- Evidence map
- Target taxonomy
- Loading order
- Society0 construction rules
- Paper-derived patterns
- Dirty-work triage
- Scaffold checklist
- Baselines, ablations, and validation
- Reproduction boundaries
- Failure modes

## Domain Stance

Model city and emergency simulations as **place-hosted decision systems**:

```text
spatial environment / policy scenario / infrastructure state
-> bounded local FoV -> LLM interpretation or route/control decision
-> env movement, resource, traffic, or plan update
-> auditable traces -> rehearsal, mechanism, or benchmark interpretation
```

The environment owns the city: maps, locations, routes, road networks, building
or venue layout, land-use parcels, exits, hazards, traffic, accessibility
constraints, resources, warnings, official communications, social ties,
simulation time, and metric records. LLM agents own situated interpretation:
risk perception, trust in warning messages, mobility routines, route reasoning,
local satisfaction, rationale generation, social communication, and constrained
choice under partial observation.

Use `plain` only for the first mechanism sketch when geography is not yet
essential. Use a custom env when maps, FoVs, movement, traffic, accessibility,
resource availability, hazards, or location-indexed records define the research
claim. Keep city-specific assumptions in the env or experiment, not in
`Society0`, `CodeSchedule`, `World`, or generic agent APIs.

High-stakes boundary: city, traffic, and emergency simulations can support
scenario rehearsal, stakeholder sensemaking, mechanism exploration, training
discussion, and research design. They cannot by themselves authorize emergency
orders, evacuation routes, traffic timings, infrastructure investment, security
operations, or public-policy decisions. Require domain expert review, empirical
calibration, participation from affected groups, and institutional
accountability before any consequential use.

## Evidence Map

Read `simulation-paper-distillation.md` before using this guide for paper
reproduction. Candidate lists are not evidence.

| Paper or source | Evidence status | Main design target |
| --- | --- | --- |
| Li, Das, and Shirado, *What Makes LLM Agent Simulations Useful for Policy Practice? An Iterative Design Study in Emergency Preparedness*, arXiv:2509.21868, https://arxiv.org/abs/2509.21868 | Full arXiv PDF and appendix inspected. Public official code was not confirmed in this pass. | Emergency-preparedness policy practice, stakeholder co-design, large venue evacuation, message interpretation, crowd movement, and communication. |
| Li et al., *WhatIf: Interactive Exploration of LLM-Powered Social Simulations for Policy Reasoning*, arXiv:2604.17615, https://arxiv.org/abs/2604.17615 | Full arXiv PDF and appendices inspected. Public official code was not confirmed in this pass. | Interactive branching, steering, inspection, agent interviews, and cross-run comparison for evacuation policy reasoning. |
| Waldburger and Ghafarollahi, *Hierarchical LLM Agents for Cognitive and Social Modeling of Disaster Evacuation Decisions*, arXiv:2606.14989, https://arxiv.org/abs/2606.14989 | Full arXiv PDF inspected. Paper lists code at `github.com/lucaswaldburger/hierarchical LLM agents`; a normalized public repository URL was not verified in this pass. | Grid-based disaster evacuation with persona-conditioned cognition, PADM grounding, dynamic hazards, route choice, and social cues. |
| Wang et al., *Large Language Models as Urban Residents: An LLM Agent Framework for Personal Mobility Generation*, arXiv:2402.14744 / NeurIPS 2024, https://arxiv.org/abs/2402.14744 and https://github.com/Wangjw6/LLMob | Full arXiv PDF and official repository checked. | Personal mobility generation from historical activity trajectories, patterns, motivations, and external-event prompts. |
| Wang et al., *Where Would I Go Next? Large Language Models as Human Mobility Predictors*, arXiv:2308.15197, https://arxiv.org/abs/2308.15197 and https://github.com/xlwang233/LLM-Mob | Full arXiv PDF and official repository checked. | Next-location prediction with historical stays, context stays, time-aware prompts, and interpretable reasons. |
| Feng et al., *AgentMove: A Large Language Model based Agentic Framework for Zero-shot Next Location Prediction*, arXiv:2408.13986 / NAACL 2025, https://arxiv.org/abs/2408.13986 and https://github.com/tsinghua-fib-lab/AgentMove | Full arXiv PDF and official repository checked. | Mobility prediction with spatial-temporal memory, world knowledge, collective pattern extraction, and final reasoning. |
| Ni et al., *LiPUP-MA: A Residential Experience-centric Multi-Agent Framework for Living-in-the-loop Participatory Urban Planning*, arXiv:2412.20505, https://arxiv.org/abs/2412.20505 | Full arXiv PDF and appendices inspected. Paper says LiPUP-MA artifacts may be released; own public repo was not confirmed in this pass. | Living-in-the-loop participatory urban planning with resident simulation, experience bank, planner tools, and static/dynamic metrics. |
| Lai et al., *LLMLight: Large Language Models as Traffic Signal Control Agents*, arXiv:2312.16044 / KDD 2025, https://arxiv.org/abs/2312.16044 and https://github.com/usail-hkust/LLMTSCS | Full arXiv PDF and official repository checked. | Single-intersection traffic signal control with verbalized lane observations, bounded signal actions, rationales, and LightGPT training. |
| Yuan, Lai, and Liu, *CoLLMLight: Cooperative Large Language Model Agents for Network-Wide Traffic Signal Control*, arXiv:2503.11739, https://arxiv.org/abs/2503.11739 and https://github.com/usail-hkust/CoLLMLight | Full arXiv PDF and official repository checked. | Network-wide traffic control with neighboring-intersection observations, complexity-aware reasoning, and simulation feedback. |
| Feng et al., *CityBench: Evaluating the Capabilities of Large Language Models for Urban Tasks*, arXiv:2406.13945 / KDD 2025, https://arxiv.org/abs/2406.13945 and https://github.com/tsinghua-fib-lab/CityBench | Full arXiv PDF and official repository checked. | Urban benchmark with CityData, CitySimu, geospatial/visual tasks, mobility prediction, urban exploration, navigation, and traffic control. |

If a user needs exact reproduction, re-check the latest paper version,
appendices, repository state, data licenses, and prompt/config files.

## Target Taxonomy

Choose the target by the city mechanism being simulated:

- **Emergency-preparedness and evacuation rehearsal**: warnings, hazards,
  exits, accessibility, family/group behavior, officials, crowd flow, and
  after-action learning. Treat as scenario rehearsal, not route advice.
- **Interactive policy-practice reasoning**: experts steer a live or forked
  scenario, inspect agents, compare branches, and surface planning assumptions.
- **Community resource and preparedness behavior**: residents receive public
  signals, request help, share information, shelter, travel, or support others.
- **Personal mobility generation**: agents generate daily activity trajectories
  from history, routines, motivations, and scenario prompts.
- **Next-location or mobility prediction**: agents predict the next POI or stay
  from historical/context stays, spatial knowledge, and collective patterns.
- **Participatory urban planning**: resident agents live through a plan,
  generate area/transition experience, and planner agents propose constrained
  land-use revisions.
- **Traffic signal control**: intersection agents choose one signal phase from
  lane observations; a network variant includes neighboring intersections and
  congestion propagation.
- **Urban task benchmarking**: LLM/VLM capabilities are tested on geospatial,
  visual, mobility, navigation, exploration, and traffic tasks across cities.

Use the governance guide when the core mechanism is formal policy authority,
public accountability, or participation design. Use the communication guide when
social-media diffusion is the mechanism. Use economics/finance guides when city
behavior is mainly a household, market, or macroeconomic process.

## Loading Order

For city, community, mobility, emergency, and traffic work, load only the files
needed for the target:

1. `research-design.md`
2. `environment-design.md`
3. `agent-design.md`
4. `step-dsl.md`
5. `simulation-paper-distillation.md` when adapting a paper
6. This guide
7. `governance-institution-public-policy-simulation-design.md` when the use
   case involves institutional authority, participation, or policy-practice
   preconditions
8. `communication-social-media-simulation-design.md` when warnings, rumors, or
   platform communication are central
9. `run-monitor-analyze.md` when planning repeated runs, output tables,
   validation, replay, or qualitative review
10. Source files only when implementing or debugging a concrete env: start from
   `src/society0/env/`, `src/society0/environment.py`,
   `src/society0/schedule.py`, and `src/society0/agent/core.py`

Load this guide when the user mentions city, neighborhood, community,
emergency, disaster, evacuation, preparedness, warnings, mobility, urban
residents, trajectory, next-location prediction, POI, traffic, traffic signal
control, urban planning, participatory planning, residential satisfaction,
CityBench, LLMob, LLM-Mob, AgentMove, LLMLight, CoLLMLight, LiPUP, WhatIf, or
policy-practice rehearsal in a built environment.

## Society0 Construction Rules

Use this env-first split:

| Component | Society0 location |
| --- | --- |
| Map, parcels, roads, lanes, intersections, venue layout, exits, homes, workplaces, parks, safe zones, accessibility, hazards, smoke, fire, traffic, resources, official announcements, social-group graph | env state, env config, rules, or step params |
| Persona, routine, role, mobility limitation, risk perception, trust in alerts, route familiarity, household/family ties, current goal, current location | agent `state` when visible; agent `properties` when hidden |
| True hazard, treatment arm, benchmark answer, human route choice, real census cell, protected sampling label, validation split | hidden `properties`, run config, or output tables, not FoV |
| Local spatial observation, visible hazards, nearby agents, road/exit options, queue lengths, warning text, group messages, current plan, recent mobility history | FoV |
| Move, shelter, evacuate, choose destination, send message, request help, assist, choose POI, select traffic phase, propose plan edit, accept/reject revision | `instruct` with typed env actions |
| Survey, trust rating, satisfaction score, reason explanation, manipulation check, post-hoc agent interview | `interview` |
| Movement physics, pathfinding, congestion, traffic simulator, hazard spread, resource depletion, signal phase timing, land-use metric computation, branch/fork, replay | env rules, behaviors, or code steps |
| Records, metrics, replay, audit logs | `ctx.result(metrics=..., tables=...)` plus analysis scripts |

Design rules:

- Make space concrete before personas. Define locations, routes, distances,
  visibility, accessibility, travel time, and action bounds.
- Give agents local FoVs. Do not expose global hazard state, true route quality,
  future congestion, hidden treatment labels, or benchmark answers unless the
  scenario says they can see them.
- Keep movement and traffic mechanics deterministic or simulator-backed where
  possible. Use LLMs for interpretation and choice, not for maintaining maps,
  clearing traffic, spreading fire, or calculating congestion.
- Type every action. Validate location IDs, route IDs, phase IDs, one-step
  movement bounds, safe-zone eligibility, signal phase choices, and whether a
  message/action is allowed for the current role.
- Record raw output, parsed action, validation failure, fallback, FoV version,
  prompt version, model, seed, treatment, map version, and timestamp.
- Treat time as part of the model. One tick might be a traffic phase, minute,
  evacuation round, hour, day, or planning cycle.
- Keep memory when the scenario depends on route familiarity, prior warnings,
  group discussion, or accumulated experience. Ablate memory only as an
  explicit experimental condition.
- Separate operational outputs from research interpretation. A faster simulated
  exit or shorter queue is a scenario result, not a field instruction.

## Paper-Derived Patterns

### Policy-Practice Simulations Need Stakeholder-Grounded Iteration

Supported by paper:
  The emergency-preparedness design study began with policymaker interviews,
  used prototypes as diagnostic artifacts, moved from communication-only
  simulation to verifiable commencement evacuation, scaled from hundreds to
  thousands of agents, and treated simulation value as co-evolving with policy
  implementation. The paper explicitly frames usefulness as training,
  reflection, feasibility assessment, and infrastructure/planning support rather
  than predictive optimization.

Inference for Society0 mapping:
  Start with a small venue/env that stakeholders can verify. Add realism only
  when a stakeholder can say why it matters: seating sections, exits,
  accessibility, family/group ties, official announcements, coordinators,
  bottlenecks, or after-action records. Keep a `policy_question`,
  `stakeholder_feedback`, and `assumption_change` table.

Unknown or unavailable:
  Public official code for the design-study simulator was not confirmed in this
  pass.

Fidelity:
  Approximate unless venue geometry, population construction, warning text,
  stakeholder workflow, and validation records are reproduced.

### Interactive What-If Tools Are Shared Reasoning Environments

Supported by paper:
  WhatIf supports direct manipulation and natural-language steering, branching,
  rewinding/replay, multi-user sessions, individual inspection, in-character
  agent interviews, and cross-run outcome reports. Its users iterated through
  intervene -> simulate -> inspect -> fork loops and used unexpected agent
  behavior to surface planning assumptions and vulnerabilities.

Inference for Society0 mapping:
  Represent each branch as a run condition or checkpoint. Store interventions as
  typed rows:

```text
intervention:
  branch_id:
  tick:
  actor:
  command_type: announcement | coordinator_move | hazard_insert | route_block | resource_change
  target_scope:
  before_state_ref:
  after_state_ref:
```

Use checkpoints for replay and branch comparison. Use `interview` only for
agent-inspection questions; do not expose ordinary movement/control actions in
that measurement path.

Safety boundary:
  Treat scenario steering as expert sensemaking. Do not turn the "best" branch
  into an operational instruction without field validation and accountable
  review.

### Emergency Evacuation Needs Dynamic Hazards, Local FoVs, And Hierarchical Decisions

Supported by paper:
  The hierarchical evacuation paper models a discrete-time 2D grid with homes,
  roads, parks, safe zones, fire, smoke, traffic, alarms, official messages, and
  social warnings. Agents have persona attributes, risk perception, social
  influence, route familiarity, finite FoV, memory, urgency assessment, and a
  high/mid/low hierarchy: intent, route, primitive movement.

Inference for Society0 mapping:
  Use a custom env with:

```text
state:
  map_cells:
  hazards:
  warnings:
  traffic_density:
  agent_locations:
  social_ties:
actions:
  set_goal(destination_type)
  choose_route(route_id)
  move(direction)
  interact(agent_id, message)
  assist(agent_id)
  stay()
```

Make low-level navigation a rule or env action validator. Invoke LLM reasoning
when context changes: warning received, smoke/hazard visible, route blocked,
group message received, dependent missing, or coordinator instruction appears.

Validation:
  Compare against empirical route-choice distributions, departure-time curves,
  congestion signatures, and qualitative expert review. Mark agent-level route
  realism as limited when individual ground truth is unavailable.

### Mobility Generation Is Routine, Motivation, And Scenario Conditioning

Supported by paper and official code/config:
  LLMob identifies personalized activity patterns from historical trajectories,
  uses self-consistency scoring against target and non-target trajectories,
  retrieves or evolves motivations, and generates daily activity trajectories.
  Its repository exposes agent, persona-identification, trajectory-generation,
  retrieval helper, prompt templates, and evaluation scripts.

Society0 mapping:
  Treat trajectory history and pattern summaries as env/user data, not global
  knowledge. Store `activity_pattern`, `motivation_summary`, `trajectory_day`,
  and `scenario_prompt` in hidden properties or run tables. Use `instruct` with
  bounded movement/visit actions when the generated trajectory updates state;
  use `interview` only for explanations or satisfaction.

Limit:
  The paper models individual daily activity without social interaction. Do not
  infer friend/family contagion or network effects unless the Society0 study
  adds and labels that extension.

### Mobility Prediction Needs Data Formatting, Memory, And Bias Checks

Supported by paper and official code/config:
  LLM-Mob formats human mobility data into historical stays and context stays,
  uses time-aware prompts, asks for prediction plus reason, and evaluates with
  top-k and ranking metrics. It reports efficiency, hallucination, proprietary
  model drift, and prompt sensitivity as limitations. AgentMove decomposes
  prediction into spatial-temporal memory, world knowledge over urban
  structure, collective knowledge from transition graphs, and a final reasoning
  step; its repository exposes preprocessing, memory/world modules, prompts,
  baselines, and evaluation scripts.

Society0 mapping:
  Use mobility prediction as either an action-free benchmark or a bounded
  decision module. Keep venue IDs/POI IDs validated. Store reasons separately
  from predictions, and log hallucinated/unrecognized location IDs as parser
  failures rather than silently correcting them.

Validation:
  Report city-by-city error, top-k accuracy, NDCG/MRR-style ranking metrics,
  prompt/model sensitivity, and geospatial bias. Do not claim individual
  itinerary truth from sparse trajectory data.

### Living-In-The-Loop Urban Planning Requires Spatially Grounded Experience

Supported by paper:
  LiPUP-MA alternates living simulation and planning. Resident agents simulate
  daily life under the current plan, then urban-grounded reflections are stored
  in a Plan-centric Graph-based Experience Bank with area and transition
  records. A skill-augmented planner uses locate-ground-execute steps, visual
  and geospatial evidence, constraint checks, and metric feedback. The paper
  evaluates service, ecology, static satisfaction, travel distance, and living
  satisfaction, and explicitly frames deployment as decision support requiring
  human validation.

Inference for Society0 mapping:
  Represent plans as env state and experience as output tables:

```text
area_experience:
  cycle:
  resident_id:
  area_id:
  activity:
  satisfaction_signal:
  complaint_or_need:
  evidence_text:
transition_experience:
  cycle:
  origin_area_id:
  destination_area_id:
  travel_burden:
  friction:
  evidence_text:
plan_revision:
  cycle:
  area_id:
  old_use:
  new_use:
  constraint_check:
  metric_delta:
```

Do not let a planner agent freely rewrite geometry. Validate immutable parcels,
allowed land-use codes, zoning-like constraints, and metric effects in env
rules.

Unknown or unavailable:
  The paper says LiPUP-MA code artifacts may be released; a public official
  LiPUP-MA repo was not confirmed in this pass.

### Traffic Signal Control Is A Bounded Control Action, Not Open-Ended Advice

Supported by paper and official code/config:
  LLMLight verbalizes lane-level traffic observations, provides a traffic-task
  description, commonsense control knowledge, and a bounded action space of
  signal phases. The environment executes the selected phase with fixed green,
  yellow, and all-red timing. The repository includes prompts, baselines,
  CityFlow-based simulation, LightGPT training, and datasets. CoLLMLight adds
  neighboring-intersection observations, spatial relation graphs, historical
  interactions, complexity-aware reasoning, and simulation feedback for
  network-wide coordination.

Society0 mapping:
  Use typed signal actions only:

```text
choose_signal_phase(intersection_id, phase_id)
```

The env must own phase timing, queue updates, spillback, throughput, and reward
metrics. If cooperative control matters, provide neighboring lanes and recent
history as FoV, but keep the road network graph and future rollout calculations
in env rules.

Validation:
  Compare against FixedTime, MaxPressure, RL baselines when available, and a
  no-cooperation variant. Report average travel time, queue length, waiting
  time, throughput, invalid phase outputs, runtime, and performance under
  out-of-distribution traffic.

Safety boundary:
  Never present a simulated phase plan as a deployable signal timing plan. It
  needs traffic-engineering review, hardware/infrastructure constraints,
  pedestrian/cyclist modeling, legal approval, and field testing.

### Urban Benchmarks Are Capability Tests, Not City Models

Supported by paper and official code/config:
  CityBench integrates CityData and CitySimu, then evaluates 8 urban tasks in 13
  cities across perception/understanding and interactive decision-making. It
  finds LLM/VLM strengths on some commonsense and semantic tasks and failures on
  professional/numerical tasks such as geospatial prediction and traffic
  control. The official repository includes evaluation code, simulator code,
  data preparation instructions, task metrics, and result records.

Society0 mapping:
  Use CityBench-style tasks as validation fixtures for urban FoVs/actions:
  mobility prediction, navigation, exploration, and traffic control. Do not
  treat benchmark performance as evidence that a new Society0 city simulation is
  valid for a different city, population, or operational context.

## Dirty-Work Triage

Can do now:

- Sketch the custom env: map units, FoVs, actions, hazard/traffic/resource
  rules, time step, output tables, and pilot metrics.
- Convert public map, route, parcel, or trajectory examples into provider-neutral
  schemas when licensing permits.
- Draft warning texts, intervention arms, persona/profile tables, action
  validators, and analysis scripts.
- Build small pilots with 5-30 agents, two neighborhoods or intersections, one
  event, and inspectable traces.

Need user/domain input:

- Study site, population, affected groups, emergency or planning scenario,
  construct definitions, acceptable warning/policy language, validation targets,
  and whether synthetic placeholders are acceptable.
- Private route, sensor, incident, after-action, survey, or stakeholder data.
- Ethical review, participation requirements, public communication constraints,
  and institutional approval for high-stakes studies.

Optional external pipeline:

- GIS/OSM preprocessing, geocoding, routing, traffic simulators, hazard models,
  street-view/satellite imagery, mobility datasets, census aggregates, and
  large-scale repeated runs.

Society0 scaffold impact:

- City studies usually need a custom env once location, mobility, traffic,
  hazards, resources, or area-indexed experience become reusable mechanisms.

## Scaffold Checklist

Before running:

- Define the claim: rehearsal, mechanism exploration, benchmark, pilot,
  calibration, or stakeholder discussion.
- Define map units, time units, actions, FoVs, hosted constraints, and hidden
  labels.
- Define who sees warnings, hazards, resources, routes, congestion, group
  messages, and official instructions.
- Define movement/traffic/hazard rules outside the LLM.
- Define output tables that can recompute metrics: locations, actions, warnings,
  messages, route choices, signal phases, queues, experience rows, revisions,
  validation labels, and branch IDs.
- Define invalid-output handling and fallback behavior.
- Define expert/human validation and interpretation boundaries before scaling.

## Baselines, Ablations, And Validation

Use the smallest credible validation set for the target:

- Emergency: no-warning vs warning; coordinator/no-coordinator; accessibility
  present/absent; social groups on/off; compare evacuation-time distributions,
  bottlenecks, exit usage, agent-level rationales, and expert review.
- Mobility: Markov/deep-learning/persistence baseline where available; no
  memory/pattern/motivation ablations; top-k, NDCG/MRR, spatial distance,
  temporal interval, routine distribution, and city-by-city error.
- Planning: initial plan, random feasible edits, static-preference planner,
  no-living, no-experience-bank, no-constraint-harness; service, ecology,
  travel distance, living satisfaction, constraint violations, and human
  evaluation of satisfaction judgments.
- Traffic: FixedTime, MaxPressure, no-neighbor, no-history, no-complexity
  routing, rule/RL baselines where available; average travel time, queue length,
  waiting time, throughput, invalid phase, runtime, and OOD traffic.
- Benchmark: task-specific metrics, city-level breakdowns, prompt/model
  sensitivity, refusal/misformat rate, and geographic bias.

Run repeated seeds and report model/provider versions. For high-stakes
emergency, traffic, or planning work, include expert review and a deployment
non-use statement unless a separate accountable process exists.

## Reproduction Boundaries

Use these fidelity labels:

- **Exact**: paper/code specifies map, data, prompts, action schema, schedule,
  deterministic rules, model settings, output parsing, baselines, and metrics.
- **Approximate**: mechanism is clear, but Society0 differs in env interface,
  model/provider, data, scale, or prompts.
- **Extension**: design move is intentionally adapted to a new Society0 study.
- **Unsupported**: source material is missing or not specific enough.

Keep public docs provider-neutral. Do not include private endpoints, keys, local
maintainer paths, or deployment-specific infrastructure in a public guide.

## Failure Modes

Avoid:

- turning emergency rehearsal into operational advice;
- hiding maps, routes, traffic, hazards, or constraints inside persona prompts;
- exposing global hazard truth or future congestion through FoV leakage;
- using `interview` for world-changing movement, traffic, or planning actions;
- accepting hallucinated POI/route/phase IDs instead of logging parser failures;
- evaluating only aggregate evacuation time or average queue without traces;
- treating LLM satisfaction or risk perception as affected-community evidence;
- over-scaling before a stakeholder can verify a small pilot;
- ignoring accessibility, vulnerable groups, pedestrians, cyclists, or resource
  constraints when they are part of the question;
- claiming validity for a different city, venue, population, or incident type
  without new calibration and review.
