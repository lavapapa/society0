# Communication And Social Media Simulation Design

Use this guide for Society0 simulations about social media, news diffusion, rumor or fake-news spread, information diffusion, polarization, echo chambers, platform interventions, social movements, group agents, and hybrid-scale communication systems. Load it after `research-design.md`, `environment-design.md`, `agent-design.md`, and `step-dsl.md`; also load `simulation-paper-distillation.md` when adapting a paper.

This guide is not a literature review. It distills design moves from social-media and communication simulation papers into Society0's env-first scaffold: setting, FoVs, actions, hosted constraints, records, measurements, baselines, ablations, and interpretation boundaries.

Contents:

- Domain stance
- Evidence map
- Target taxonomy
- Loading order
- General construction rules
- Paper-derived patterns
- Dirty-work triage
- Society0 scaffold checklist
- Baselines/ablations/validation
- Reproduction boundaries
- Failure modes

## Domain Stance

Model communication simulations as **platform-hosted information systems**:

```text
platform / movement / event -> bounded exposure -> LLM interpretation
-> constrained social action -> env update -> auditable traces
-> diffusion, discourse, attitude, or governance metric
```

The environment owns the platform and institutions: user graph, follow graph, timelines, recommendation algorithms, search exposure, social exposure, post/comment tables, engagement counts, moderation or intervention rules, time, online schedules, event injections, ground-truth labels, and output records. LLM agents own situated interpretation: attention, relevance, trust, belief, emotion, stance, rhetoric, memory, reasoning, and constrained communication choices.

Use `social_network` when the study can be represented by feeds, posts, replies, recommendations, endorsements, and diffusion. Use a custom env only when the paper requires a platform affordance, hybrid agent layer, or deterministic dynamics not expressible with the built-in env. Keep social-network-specific assumptions inside the env or experiment logic, not in `Society0`, `CodeSchedule`, `World`, or generic agent APIs.

Use `instruct` with environment actions when behavior changes the simulated world: post, repost, quote, comment, reply, like, dislike, follow, unfollow, search, refresh, report, or ignore. Use `interview` for measurement: perceived credibility, belief, trust, intention, stance, explanation, manipulation check, or post-hoc survey. Do not expose ordinary social actions during interviews unless the interview is explicitly an action-mode extension.

Treat memory, recommendation, network structure, and platform statistics as experimental mechanisms. Do not disable memory or action loops to make a run faster if those mechanisms are part of the semantics. Scale by changing the design deliberately: group agents, hybrid LLM/rule layers, topology-aware batching, or rule baselines, each labeled as an approximation or extension.

## Evidence Map

Read `simulation-paper-distillation.md` before using this guide for paper reproduction. The following sources were checked through official paper pages, official PDFs or HTML, and official code or supplement links where available. Candidate lists are not evidence.

| Paper or source | Evidence status | Main design target |
| --- | --- | --- |
| S3: Social-network Simulation System with Large Language Model-Empowered Agents, arXiv 2307.14984, https://arxiv.org/abs/2307.14984 | Full arXiv PDF inspected. No stable official code repository was confirmed during this pass. | Social-network environment with emotion, attitude, and interaction behavior. |
| OASIS: Open Agent Social Interaction Simulations with One Million Agents, arXiv 2411.11581, https://arxiv.org/abs/2411.11581 and https://github.com/camel-ai/oasis | Full arXiv PDF and official project repository checked. Appendix and database/action/recommender details are in the paper. | General social-media simulator with dynamic network, recommender, action space, time engine, and scale. |
| Simulating Social Media Using Large Language Models to Evaluate Alternative News Feed Algorithms, arXiv 2310.05984, https://arxiv.org/abs/2310.05984 | Full arXiv PDF and appendix prompt details inspected. The paper says code would be released, but no stable official code URL was confirmed during this pass. | Feed algorithm intervention, bridging algorithm, ANES personas, toxicity and cross-partisan engagement. |
| From Skepticism to Acceptance: Simulating the Attitude Dynamics Toward Fake News, IJCAI 2024, https://www.ijcai.org/proceedings/2024/0873 and https://github.com/LiuYuHan31/FPS | Full IJCAI PDF inspected; paper-provided code and appendix repository checked. | Fake-news belief dynamics, dual memory, reasoning, official intervention agent. |
| Large Language Model-driven Multi-Agent Simulation for News Diffusion Under Different Network Structures, arXiv 2410.13909, https://arxiv.org/abs/2410.13909 | Full arXiv PDF inspected. It uses FakeNewsNet; no stable official code repository was confirmed during this pass. | Network topology, personality effects, misinformation countermeasures. |
| Can We Fix Social Media? Testing Prosocial Interventions using Generative Social Simulation, arXiv 2508.03385, https://arxiv.org/abs/2508.03385 and https://github.com/cssmodels/prosocialinterventions | Full arXiv PDF inspected; code availability statement and official repository checked. | Minimal social-media platform, echo chambers, influence inequality, prosocial platform interventions. |
| MIDSim: Simulating Multi-Channel Information Diffusion in Social Media with LLM-Powered Multi-Agent System, arXiv 2606.13140, https://arxiv.org/abs/2606.13140 | Full arXiv PDF inspected. The paper lists an anonymous implementation link; no stable public official repository was confirmed during this pass. | Multi-channel diffusion with social exposure plus algorithmic exposure. |
| Unveiling the Truth and Facilitating Change: Towards Agent-based Large-scale Social Movement Simulation, ACL Findings 2024, https://aclanthology.org/2024.findings-acl.285/ and https://github.com/xymou/social_simulation | Full ACL PDF and official code/data repository checked. | Social movement simulation with LLM core users and rule-based ordinary users. |
| GA-S3: Comprehensive Social Network Simulation with Group Agents, ACL Findings 2025, https://aclanthology.org/2025.findings-acl.468/ and https://github.com/AI4SS/GAS-3 | Full ACL PDF and official repository checked. | Group agents for large-scale event traffic prediction. |
| Social opinions prediction utilizes fusing dynamics equation with LLM-based agents, Scientific Reports 2025, https://www.nature.com/articles/s41598-025-99704-3 | Full Nature HTML/PDF inspected; article says data are in the article and supplementary information. No stable official code repository was confirmed during this pass. | LLM opinion leaders plus CA/SIR followers for opinion trajectories. |
| An LLM-enhanced Agent-based Simulation Tool for Information Propagation, IJCAI 2024, https://www.ijcai.org/proceedings/2024/1007 and https://github.com/shaunahu/LAIDSim | Full IJCAI PDF and official tool repository checked. | Independent-cascade style diffusion with LLM message alteration. |
| Topology-Aware LLM-Driven Social Simulation, arXiv 2604.18011, https://arxiv.org/abs/2604.18011 and https://github.com/D2I-CUHKSZ/MicroWorld | Full arXiv PDF inspected; paper-provided artifact repository checked. | Topology-aware exposure materialization and update coordination for scalable LLM social simulation. |

If a user needs paper-specific reproduction, re-check the latest paper version, appendix, repository state, and data availability before claiming exact fidelity.

## Target Taxonomy

Choose the target by the communication object being simulated:

- **Social-media platform simulation**: users interact through feeds, posts, replies, likes, reposts, follows, recommendations, and platform-level visibility rules. Use this guide for OASIS-style, news-feed, and prosocial-platform designs.
- **News or information diffusion**: a message, event, article, rumor, or content item spreads through a network or platform. Track reach, scale, depth, breadth, exposure channel, and text mutation.
- **Rumor or fake-news belief dynamics**: agents form, update, defend, or abandon belief in a claim. Track susceptible/infected/recovered states, belief, reasons, corrections, and official interventions.
- **Opinion, attitude, emotion, and polarization**: agents' stances or emotional states evolve through exposure and interaction. Track average attitudes, extreme opinions, disagreement, echo chambers, E-I index, and content quality.
- **Platform intervention and governance**: feed ranking, chronological timelines, bridging algorithms, hiding engagement counts, accuracy labels, influencer blocking, official declarations, or moderation actions are treatments.
- **Social movement or event-response simulation**: public reactions to trigger events unfold across timelines; core users generate discourse while ordinary users update attitude through ABM or dynamics rules.
- **Group-agent or hybrid-scale simulation**: one agent represents a behaviorally similar group, or LLM agents model semantic leaders while rules model followers. Use when scale is part of the method, not just a performance wish.
- **Topology-aware execution or network-sensitive scheduling**: network structure shapes exposure, intervention effects, and possibly batched updates. Use as an approximation layer only when fidelity effects are measured.

## Loading Order

For communication and social-media work, load only the files needed for the target:

1. `research-design.md`
2. `environment-design.md`
3. `agent-design.md`
4. `step-dsl.md`
5. `simulation-paper-distillation.md` when adapting a paper
6. This guide
7. `run-monitor-analyze.md` when planning metrics, tables, repeated runs, or qualitative review
8. Source files only if implementing or debugging a concrete env: start from `src/society0/env/`, `src/society0/environment.py`, `src/society0/schedule.py`, and `src/society0/agent/core.py`

Load this guide when the user mentions social media, communication, feeds, posting, reposting, commenting, likes, follows, recommendation algorithms, news diffusion, information diffusion, rumor, fake news, misinformation, disinformation, belief spread, attitude dynamics, emotion propagation, opinion dynamics, polarization, echo chambers, bridging algorithms, platform interventions, social movements, online events, group agents, hybrid scale, OASIS, S3, FPS, HiSim, GA-S3, MIDSim, LAID, FDE-LLM, or TopoSim.

## General Construction Rules

Use this env-first split in every communication simulation:

| Component | Society0 location |
| --- | --- |
| Platform, graph, follow relations, post/comment stores, engagement counts, recommender, search, moderation, time, online schedule, event injection, ground truth | env state, env config, rules, step params, or output tables |
| Persona, ideology, interests, history summary, personality traits, prior belief, stance, emotion, activity level, current attention budget | agent `state` if visible; agent `properties` if hidden |
| Treatment arm, seed, network topology label, true/false label, source condition, data-sampling cell, held-out benchmark labels | hidden `properties`, run config, or output tables, not visible FoV |
| Timeline, feed, search results, comments, engagement counts, official notice, local neighbor messages, current event | FoV |
| Posting, reposting, replying, liking, following, searching, refreshing, reporting, deciding not to act | `instruct` with env actions |
| Credibility rating, belief, perceived toxicity, manipulation check, survey answer, explanation, stance coding | `interview` or analysis model outside the action loop |
| Recommendation, ranking, propagation, graph updates, CA/SIR/IC/LT dynamics, online sampling, decay, intervention delivery | env rules or code steps |
| Metrics and audit records | `ctx.result(metrics=..., tables=...)` plus analysis scripts |

Design rules:

- Treat the feed as an experimental treatment. Record the candidate pool, ranking algorithm, in-network/out-of-network mix, social-vs-algorithmic exposure, displayed post count, truncation rules, and hidden labels.
- Keep platform affordances as typed actions. Do not ask the LLM to "act like Twitter" in prose while the environment accepts arbitrary text.
- Bound every action. Validate post IDs, comment IDs, user IDs, one-action-per-round rules, text length, allowed recipients, follow constraints, and whether a user has already seen or acted on content.
- Record raw LLM output, parsed action, validated action, validation failure, fallback, FoV version, prompt version, model, seed, treatment, and timestamp.
- Use `completion_action_tags=["social_write"]` when a social browsing round should end after one real write interaction. Use `terminal_actions` only when a successful action is the semantic endpoint of the round.
- Represent "do nothing" or "no action" explicitly. Non-action is often part of diffusion dynamics.
- Keep hidden truth and treatment labels out of FoV. A fake-news experiment may tell an agent about an official correction only when the intervention schedule delivers it.
- Make time meaningful: one tick might be a minute, 3 minutes, an hour, a day, or an event stage. Record the mapping and whether agents are synchronously or asynchronously activated.
- Keep memory as a modeled mechanism. For ongoing social interaction, `memory=True` is usually appropriate; ablate memory only to test its effect. Do not turn it off for speed in a memory-bearing design.
- Separate language generation from metric coding. An LLM may generate a post; a separate rule, classifier, human coder, or analysis model may code stance, toxicity, or bridging quality.
- Pilot with a small network and visible traces before scaling. Large agent counts do not rescue weak FoVs, action schemas, or validation design.

## Paper-Derived Patterns

### Platform As Hosted Environment

Supported by paper:
  OASIS separates an environment server, recommender, agent module, time engine, and scalable inference. The environment stores users, posts, comments, relations, traces, and recommendations. The news-feed algorithm paper and the prosocial-intervention paper make feed ranking the treatment.

Supported by official code/config:
  OASIS and the prosocial-intervention paper provide official repositories. OASIS also documents action and database details in appendices.

Society0 mapping:
  Implement platform mechanics as env state, FoVs, actions, and rules. The FoV is the rendered timeline; the action set is platform-specific; the recommender is an env rule or step, not a persona prompt. Store trace rows for every recommendation and action.

Unknown or unavailable:
  The news-feed algorithm paper's stable official code URL was not confirmed during this pass.

Fidelity:
  Approximate unless the exact population construction, feed algorithm, prompt wording, model settings, action schedule, and code are reproduced.

### Feed Ranking As Treatment

Supported by paper:
  The news-feed paper compares followed-user engagement ranking, all-user engagement ranking, and a bridging algorithm. The prosocial paper tests chronological ordering, downplaying dominant content, boosting out-partisan content, bridging attributes, hiding social statistics, and hiding biography. OASIS models in-network and out-of-network recommendations and Reddit-style hot ranking.

Society0 mapping:
  Treat each feed rule as a named treatment in env config:

```text
feed_treatment:
  candidate_pool:
  ranking:
  diversity_or_bridging_signal:
  in_network_count:
  out_network_count:
  visible_statistics:
  hidden_labels:
```

Record per-agent recommendation rows: `agent_id`, `tick`, `candidate_post_id`, `rank`, `score_components`, `shown`, `treatment`, and `reason_if_filtered`.

Inference for Society0 mapping:
  Bridging attributes may be implemented with any provider-neutral classifier or user-supplied score. Do not hard-code a proprietary scoring API into the public guide.

### Exposure Channels Must Be Separate

Supported by paper:
  MIDSim explicitly separates social streams, such as follow feeds and notifications, from algorithmic streams, such as recommendation and search. Its ablation compares social-only, algorithmic-only, and full multi-channel exposure.

Society0 mapping:
  Store exposure channel on every shown item:

```text
exposure:
  channel: social_feed | notification | recommendation | search | official_notice
  source_user_id:
  post_id:
  rank:
  trigger:
  attention_slot:
```

Metrics should report diffusion by channel, not only total reach.

Unknown or unavailable:
  MIDSim's paper lists an anonymous implementation link; no stable public repository was confirmed during this pass.

### Belief Dynamics Need State, Memory, And Correction Schedules

Supported by paper:
  FPS models fake-news propagation with personas, short-term memory, long-term memory, reasoning, daily interactions, susceptible/infected/recovered-style states, and an official agent that issues interventions. The news-diffusion network paper studies countermeasures such as blocking influential nodes and announcing accuracy after spread thresholds.

Supported by official code/config:
  FPS provides a paper-linked code and appendix repository.

Society0 mapping:
  Store belief and exposure separately:

```text
state:
  belief: susceptible | infected | recovered | skeptical | unknown
  belief_score:
  latest_reason:
  last_correction_tick:
properties:
  true_label:
  treatment_arm:
```

The correction is an env-delivered FoV event or an official-agent post. It is not global knowledge unless the platform rule makes it visible.

Fidelity:
  Exact only if the daily schedule, memory update, official intervention cadence, topic prompts, personality construction, and metrics are reproduced.

### Network Topology Changes Both Diffusion And Intervention Effects

Supported by paper:
  The news-diffusion network paper compares random, scale-free, and high-brokerage networks and finds countermeasure effectiveness depends on topology. S3 and OASIS use real or reconstructed social-network data. TopoSim treats topology as an exposure and execution signal.

Society0 mapping:
  Make topology an explicit run condition: graph type, source node rule, degree distribution, brokerage/community structure, edge direction, and whether edges are static or dynamic. Record graph snapshots when follow/unfollow actions can change the network.

Validation:
  Compare results across topology variants. Report when an intervention only works under one topology family.

### Attitude, Emotion, And Interaction Are Distinct Layers

Supported by paper:
  S3 models emotion, attitude, and interaction behavior separately, using social data to evaluate individual-level and population-level outcomes. FDE-LLM distinguishes opinion leaders and followers and constrains opinion change with CA and SIR-inspired dynamics.

Society0 mapping:
  Do not collapse "saw content", "felt emotion", "believed claim", "posted text", and "shifted attitude" into one variable. Keep them as separate state fields or tables:

```text
exposure -> emotion/stance/belief update -> action -> env propagation -> measurement
```

Use rules for deterministic decay, CA/SIR transitions, or aggregate opinion equations; use LLM agents for semantic interpretation and communicative action.

### Message Mutation Is A First-Class Mechanism

Supported by paper:
  LAID/LAIDSim extends influence diffusion by letting agents alter information content according to their profiles before forwarding it.

Supported by official code/config:
  LAIDSim has an official repository linked in the IJCAI demonstration paper.

Society0 mapping:
  Store message lineage:

```text
message_id:
parent_message_id:
source_agent_id:
text:
alteration_degree:
semantic_similarity_to_parent:
stance_or_topic_shift:
```

If the study concerns rumor distortion, the env should preserve parent-child text links so semantic drift can be audited.

### Social Movements Benefit From Hybrid Actors

Supported by paper:
  HiSim categorizes users into LLM-driven core users and ABM-driven ordinary users for social movement events, with Twitter-like timelines, post/retweet/reply/like actions, stance/content/behavior alignment, and macro attitude trajectories.

Supported by official code/config:
  The ACL paper links an official code and data repository.

Society0 mapping:
  Use LLM agents for core users whose language, stance, and behavior need semantic fidelity. Use rules for ordinary users when they only update attitude or state in response to leader messages. Keep the boundary visible:

```text
agent_type: core_llm | ordinary_rule
leader_influence_table:
ordinary_update_rule:
calibration_split:
validation_split:
```

Inference for Society0 mapping:
  In Society0, ordinary users can be represented as rule-based agents, group rows, or env-level aggregate state depending on the research target. Label this as approximate unless it matches the paper.

### Group Agents Are Not Just Cheap Individuals

Supported by paper:
  GA-S3 designs group agents that represent collections with similar behavior. It uses hierarchical generation, group attributes, memory/state, decision reasoning, and actions such as view, like, comment, share, and predict.

Supported by official code/config:
  The ACL paper links an official GA-S3 repository.

Society0 mapping:
  Use group agents only when the group is the modeled actor:

```text
group_agent:
  represented_population:
  grouping_basis:
  country_or_domain:
  characteristics:
  attitude:
  emotion:
  activity_weight:
  action_to_count_mapping:
```

Actions by group agents should map to counts or rates, not pretend that one post equals one individual unless that is the declared abstraction.

### Dynamics Equations Can Constrain LLM Overreaction

Supported by paper:
  FDE-LLM combines LLM opinion leaders with CA constraints and CA/SIR followers to model reversal news, opinion decay, and recovery. Its ablation reports worse performance without CA constraints.

Society0 mapping:
  Use deterministic rules for opinion decay, recovery, and follower state updates; use LLMs for leader action text and semantic stance. Record the fusion coefficient, decay rate, recovery rate, threshold, and grid or graph structure.

Unknown or unavailable:
  No stable official code repository was confirmed during this pass. The Nature article states that data are included in the article and supplementary information.

### Topology-Aware Scale Is An Approximation Layer

Supported by paper:
  TopoSim frames graph-coupled LLM social simulation as gather-update-scatter and uses receiver-dependent influence materialization plus topology-aware update coordination to reduce redundant LLM calls while measuring fidelity loss.

Supported by official code/config:
  The arXiv paper links an artifact repository under MicroWorld.

Society0 mapping:
  This is not a reason to skip individual LLM loops silently. If used, topology-aware grouping or shared updates must be an explicit scale approximation with its own records:

```text
coordination_cell_id:
representative_agent_id:
members:
signature_features:
shared_update:
fidelity_check_sample:
```

Validation:
  Compare coordinated execution against full-agent execution on a small graph before using it for scale.

## Dirty-Work Triage

Social-media simulations fail when the hard preparatory work is erased. Triage it explicitly:

| Relationship to Society0 | Communication examples | Agent behavior |
| --- | --- | --- |
| Society0-native modeling | FoVs, feed ranking, action schemas, post/comment stores, graph updates, online schedules, interventions, hidden labels, trace tables, action validation | Build into env state, rules, actions, steps, and records. |
| Agent can prepare | extract paper prompts, normalize public datasets, generate synthetic persona tables from user-approved distributions, draft treatment variants, write analysis scripts, build coding rubrics | Do the work when sources and permissions are available; state assumptions. |
| User input required | target population, construct definitions, acceptable intervention wording, private data, platform terms, ethics/IRB constraints, whether synthetic placeholders are acceptable | Ask for the decision or dataset; label placeholders. |
| External pipeline | social-media crawling, API access, large RAG corpora, engagement-statistic classifiers, toxicity/bridging scoring services, large batch model jobs | Scaffold or run only with available credentials and allowed data use. Keep public docs provider-neutral. |
| Methodological/statistical | sampling weights, annotation quality, human coder validation, calibration split, validation split, repeated seeds, uncertainty, manipulation checks, prompt version control | Implement where possible; ask the user to confirm construct validity and benchmark relevance. |
| Scale/cost operations | one-million-agent runs, group-agent design, topology-aware coordination, queueing, caching, storage, rate limits | Start with a pilot; scale only after trace and metric validity are established. |

Minimum dirty-work questions:

- What is the platform or communication setting?
- What can users actually see?
- What can they actually do?
- What changes when an action succeeds?
- Which labels are hidden ground truth?
- What data or theory grounds the personas, graph, and feed?
- What empirical or human benchmark will validate the output?
- Which scale shortcut is being introduced, and how will it be checked?

## Society0 Scaffold Checklist

Before coding a communication simulation, write this scaffold:

```text
Research target:
  social media / news diffusion / fake news / polarization / social movement / group agent / hybrid scale

Construct and hypothesis:
  belief, trust, stance, emotion, attention, toxicity, echo chamber, reach, exposure, intervention effect, or content mutation.

Setting:
  platform or event; tick meaning; public/private data source; provider-neutral runtime assumptions.

Agents:
  roles, profiles, visible state, hidden properties, LLM vs rule vs group-agent boundary.

Network:
  graph source, topology, edge direction, dynamic follow/unfollow rules, source node selection.

FoVs:
  timeline, social exposure, algorithmic exposure, search, notifications, engagement counts, official notices, local memory.

Actions:
  names, typed arguments, one-action rules, terminal/completion semantics, validation, fallback.

Hosted constraints:
  recommendation, search, post store, comment store, engagement counts, moderation, correction schedule, online sampling, decay/dynamics.

Dirty-work triage:
  society0_native:
  agent_can_do:
  user_input_needed:
  external_pipeline:
  methodological:
  scale_cost:

Run loop:
  event injection -> online sampling -> feed construction -> LLM action -> validation -> env update -> metrics -> memory/update rules.

Interventions:
  feed treatment, official correction, accuracy label, blocked node, hidden metric, bridging score, social-channel removal, algorithmic-channel removal.

Records:
  shown items, raw outputs, parsed actions, validated actions, message lineage, graph snapshots, state tables, metric inputs.

Measurements:
  diffusion, belief, attitude, toxicity, cross-partisan interaction, content diversity, trajectory fit, qualitative traces.

Baselines:
  rule, IC/LT/SIR/CA, no profile, no memory, no recommender, no social channel, no algorithmic channel, no intervention, alternate topology, alternate model/provider.

Validation:
  real trajectory, human coding, known stylized fact, held-out event, repeated seeds, ablation, leakage audit.

Fidelity:
  exact / approximate / extension / unsupported by paper element.
```

Do not proceed to a large run until the scaffold makes visible versus hidden information explicit.

## Baselines, Ablations, And Validation

Baseline menu:

- Independent Cascade, Linear Threshold, SIR/SIS, CA, bounded confidence, HK, relative agreement, social judgement, Lorenz, or other rule dynamics.
- Real-data replay without LLM decision.
- Random, chronological, engagement, interest, hot-score, or bridging feed.
- No recommender, no social exposure, no algorithmic exposure, no search, no notifications.
- No profile, demographics only, profile only, no historical posts, no memory, no long-term memory, no short-term memory, no reasoning, no reflection.
- No official intervention, delayed intervention, single declaration, repeated declaration, accuracy label, influencer block, comment requirement.
- Random, scale-free, high-brokerage, empirical, or dynamic follow graph.
- Individual LLM agents versus group agents versus hybrid LLM/rule design.
- Full-agent execution versus topology-aware or group-coordinate approximation.
- Alternate model/provider, temperature, prompt wording, and parser variants.

Validation targets:

| Target | Validation |
| --- | --- |
| Information diffusion | scale, depth, max breadth, reach curve, MAPE, MRSE/RMSE, normalized RMSE, confidence intervals, held-out events |
| Fake news belief | infection/recovery/peak/half rates, belief average and variance, correction response, individual reasoning audit |
| News-feed intervention | E-I index, cross-partisan likes/comments, toxicity/civility, bridging score, feed exposure audit |
| Polarization and echo chambers | E-I index, modularity/community alignment, attitude variance, extreme-opinion share, disagreement, Gini of visibility |
| Social movement | stance/content/behavior alignment, average attitude trajectory, DTW, Pearson correlation, micro alignment samples |
| Message mutation | lineage depth, alteration degree, semantic similarity, stance/topic drift, qualitative examples |
| Group or hybrid scale | group-to-count mapping, population weights, DTW/MAPE, small full-agent comparison, approximation error |
| Topology-aware execution | token/call savings, trajectory MAE, correlation with full-agent baseline, sampled full-execution audit |

For every target:

- Run a small pilot first and inspect traces.
- Repeat seeds before interpreting emergent behavior.
- Preserve enough rows to recompute metrics.
- Separate platform mechanics from LLM decision effects.
- Record model/provider, prompt version, FoV version, temperature, seed, and treatment.
- Keep qualitative examples for human audit, especially when metrics use automated scoring.

## Reproduction Boundaries

Use these fidelity labels:

- **exact**: the paper/code specifies the mechanism and Society0 can implement it directly.
- **approximate**: the mechanism is clear, but Society0 differs in env interface, model, provider, prompt, data, scale, or infrastructure.
- **extension**: the design is inspired by the paper but intentionally adds Society0-specific structure.
- **unsupported**: source material is missing or insufficient to reproduce the element.

Do not present a study as one-to-one reproduced unless all reproduction-critical pieces are specified: population construction, graph construction, FoV, action schema, feed ranking, intervention schedule, run loop, memory policy, model settings, prompts, output parser, invalid-output handling, data split, baselines, ablations, and metrics.

Paper-specific cautions:

- S3: useful for separating emotion, attitude, and interaction; exact reproduction is unsupported without code and data details beyond the paper.
- OASIS: strong design evidence for env/recommender/action/time structure; exact reproduction requires the official code, database schema, action setup, datasets, and model choices.
- News-feed algorithm paper: appendix prompts and ANES persona method are useful; exact reproduction is unsupported unless stable code and data transformations are available.
- FPS: stronger reproduction support because paper and code/appendix repository are available; still check repository version and prompt files before exact claims.
- News-diffusion network paper: useful topology and countermeasure design; exact reproduction is unsupported without stable official code.
- Prosocial-intervention paper: repository exists; exact reproduction still requires checking supplement, data construction, Perspective/bridging score implementation, and run settings.
- MIDSim: useful multi-channel exposure design; exact reproduction is unsupported until a stable public implementation and dataset schema are available.
- HiSim: strong hybrid-design evidence with official code/data; exact reproduction requires dataset processing and calibration details.
- GA-S3: useful for group-agent design; exact reproduction requires official code, benchmark details, and group-generation rules.
- FDE-LLM: useful for dynamics constraints; exact reproduction is limited by code availability and supplementary-data handling.
- LAIDSim: useful for message-alteration diffusion; small-tool reproduction is more feasible but still requires code version and parameter settings.
- TopoSim: useful for scale approximation; do not use it as semantic fidelity evidence unless compared against full-agent execution.

## Failure Modes

- **Prompt-only platform**: feeds, recommendations, graph, and actions exist only as prose.
- **Candidate-list evidence**: a reading list is treated as proof instead of official papers/code.
- **Hidden-label leakage**: treatment, truth label, topology condition, or future correction appears in FoV.
- **Feed-treatment opacity**: the candidate pool and ranking rule are not recorded, so exposure cannot be audited.
- **Metric-only logging**: final reach, toxicity, or DTW is stored without rows to recompute it.
- **Action collapse**: "engage" mixes like, repost, reply, follow, and quote into one ambiguous action.
- **Interview/action confusion**: an interview about credibility is treated as an actual platform share, or an action round is replaced with action-free JSON output.
- **Memory shortcut**: memory is disabled for speed even though memory is part of belief, interaction, or movement dynamics.
- **Scale illusion**: large agent counts hide weak persona grounding, weak FoV, unvalidated feed ranking, or missing baselines.
- **Overhomogeneous agents**: agents agree too much, fail to generate dissent, or reflect provider/prompt bias more than population structure.
- **Unmarked group approximation**: group agents are described as if they were individual users.
- **Topology blindness**: an intervention is claimed to work generally after testing one graph.
- **Provider-specific public docs**: public guide text includes private endpoints, keys, private file paths, or platform-specific infrastructure assumptions.
- **Overclaiming**: simulated discourse is presented as empirical evidence about real users without calibration, validation, or limitation statements.
