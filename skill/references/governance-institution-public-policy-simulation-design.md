# Governance, Institution, And Public Policy Simulation Design

Use this guide when a Society0 design is about institutions that make, enforce,
contest, or justify collective decisions: legislatures, committees, coalitions,
public policy labs, commons governance, norm formation, moderation systems,
elections, roll-call voting, public-good cooperation, sanctions, accountability,
or policy-practice simulations.

Load it after `research-design.md`, `environment-design.md`, `agent-design.md`,
and `step-dsl.md`; also load `simulation-paper-distillation.md` when adapting a
paper.

Do not use this guide as a generic multi-agent template. If the mechanism is a
social feed, news diffusion, misinformation spread, or platform recommendation,
start with `communication-social-media-simulation-design.md` and return here only
for the governance layer. If the mechanism is interview, survey response,
focus-group deliberation, or human-subject replication, start with
`interview-survey-deliberation-simulation-design.md`. If the mechanism is macro
or financial behavior, start with the economics/finance guides.

Contents:

- Boundary stance
- Evidence map
- Target taxonomy
- Loading order
- Society0 construction rules
- Minimal scaffold checklist
- Baselines and ablations
- Interpretation boundaries
- Common failure modes

## Boundary Stance

Governance simulations are evidence machines, not policy authorities. They can
make institutional mechanisms inspectable by forcing the designer to specify who
can see what, who can act, when rules bind, how decisions aggregate, and what
records survive. They do not make LLM outputs into public preferences, legal
advice, coalition forecasts, election forecasts, or official policy analysis.

Use this guide when the research question depends on at least one of these
institutional mechanisms:

- agenda setting, proposal drafting, amendment, committee debate, or roll-call voting;
- coalition bargaining, manifesto-policy alignment, negotiated program text, or government formation;
- common-pool resource use, public-good contribution, sanctions, or collapse;
- social norms, rule creation, compliance, monitoring, and norm diffusion;
- content governance, flagging, fact-checking, appeal, enforcement, or moderation strategy;
- public-policy scenario rehearsal, policy lab reasoning, accountability, or stakeholder interpretation;
- election or representative voting simulation with explicit population/persona construction and validation.

Do not use this guide when:

- the desired output is a real-world policy recommendation without external validation and accountable human interpretation;
- affected groups are modeled as a substitute for participation;
- the study needs current law, agency procedure, or legal compliance advice;
- the institution is only a decorative setting and no rule, permission, sanction, vote, allocation, or accountability mechanism changes the outcome;
- the simulation would operationalize sensitive-population predictions without participation, validation, and a deployment/accountability report.

## Evidence Map

| Source | Domain use | Evidence status | Society0 lesson |
| --- | --- | --- | --- |
| Baker and Azher, *Simulating The U.S. Senate* (arXiv:2406.18702) | Legislative committee discussion and bipartisanship perturbations | Supported by paper; no official code found in checked paper text | Use named institutional roles, memory-backed debate records, expert believability ratings, and explicit perturbations; do not generalize committee demos to policy forecasting. |
| Moghimifar et al., *Modelling Political Coalition Negotiations Using LLM-based Agents* (arXiv:2402.11712) | Parliamentary coalition bargaining and policy-agreement prediction | Supported by paper; POLCA dataset is described as not publicly available; no official repo found | Model bargaining as agenda items and policy statements with inclusion/partial-inclusion outcomes; log each offer, concession, and termination rule. |
| Piatti et al., *Cooperate or Collapse* / GovSim (arXiv:2404.16698) | Commons governance, cooperation, sustainability, public-good failure | Supported by paper and official GitHub `giorgiopiatti/GovSim` | Treat common resources as hosted state, decisions as resource extraction/contribution actions, and collapse/survival as objective metrics; language communication is an ablation target. |
| Dai et al., *Artificial Leviathan* (arXiv:2406.14373) | Social-contract and institution emergence experiments | Supported by paper; no official code found in checked paper text | Use as a conceptual institution-formation case only; mark implementation details as unavailable unless code is later verified. |
| Ren et al., *Emergence of Social Norms in Generative Agent Societies* / CRSEC (arXiv:2403.08251; IJCAI 2024) | Norm creation, representation, spreading, evaluation, and compliance | Supported by paper and official GitHub `sxswz213/CRSEC` | Separate norm lifecycle modules: create/represent, spread/retrieve, evaluate, and comply; keep norm records inspectable rather than burying them in persona text. |
| Liu et al., *MOSAIC* (arXiv:2504.07830) | Content governance, fact-checking, community moderation, hybrid moderation | Supported by paper and official GitHub `genglinliu/MOSAIC` | Put moderation policy in the environment: no fact-checking, third-party fact-checking, community fact-checking, and hybrid fact-checking are treatment arms with database-backed interaction records. |
| Zhang et al., *ElectionSim* (arXiv:2410.20746) | Large-population election simulation and poll benchmark | Supported by paper and project page; official GitHub not found in checked paper text | Population construction and benchmark choice dominate claims; treat election simulation as calibrated survey/population modeling, not a prediction oracle. |
| Li et al., *Political Actor Agent* (arXiv:2412.07144) | U.S. House roll-call vote prediction through role-playing legislators | Supported by paper; no official code found in checked paper text | Separate profile construction, planning, influence mechanism, and vote action; record what prior votes and bill text are visible. |
| Kreutner et al., *Persona-driven Simulation of Voting Behavior in the European Parliament* (arXiv:2506.11798; EACL Findings 2026) | MEP persona prompting and roll-call simulation | Supported by paper and official GitHub `dess-mannheim/european_parliament_simulation` | Use explicit persona attributes, proposal text, roll-call visibility, counter-speech options, and group/national-party ablations; report coalition-strategy limitations. |
| Hao and Xie, *A Multi-LLM-Agent-Based Framework for Economic and Public Policy Analysis* (arXiv:2502.16879) | Economic/public-policy analysis with heterogeneous LLM agents | Supported by paper; no official code found in checked paper text | Use only for policy-analysis framing where agent heterogeneity is specified; keep economics-heavy mechanics routed to economics/finance guides. |
| Luo and Arora, *We Need Strong Preconditions For Using Simulations In Policy* (arXiv:2604.07838) | Policy use, dual-use, participation, validation, and accountability boundaries | Supported by paper; boundary source rather than implementation source | Require participation, independent validation, accountability, and deployment/reporting language before simulations affect consequential policy. |
| Zhou et al., *Investigating Prosocial Behavior Theory in LLM Agents Under Policy-Induced Inequities* / ProSim (arXiv:2505.15857; AAAI 2026) | Prosocial behavior, policy interventions, inequity, fairness perception, and social contagion | Supported by paper and official GitHub `halsayxi/ProSim` README | Treat prosociality as a norm/policy environment with scenarios, intervention arms, network observation, perceived unfairness, and human benchmark checks. |
| Vallinder and Hughes, *Cultural Evolution of Cooperation among LLM Agents* (arXiv:2412.10270) | Indirect reciprocity, cooperation, reputation, punishment, and cultural transmission | Supported by paper and official GitHub `aronvallinder/llm-donor-game` README | Use as a compact cooperation/norm probe: hosted donor-game payoffs, reputation traces, punishment affordance, generation boundary, and repeated-seed/model comparison. |

Candidate names such as GPLab or policy lab papers are not enough by themselves.
Cite only the paper, official page, official repository, or official data that
was actually checked.

## Target Taxonomy

### Legislative and Committee Simulation

Use when the question is about debate, proposal quality, bipartisanship,
committee dynamics, hearings, amendments, or roll-call decisions.

Supported by paper:
- The Senate simulation creates senator agents, stores policy biographies and memories, places them in structured committee discussion, runs repeated simulations, and uses expert believability ratings.
- PAA uses legislator profiles, bill text, historical records, planning, and an influence mechanism to predict roll-call votes.
- European Parliament voting uses MEP personas and proposal information, then compares predicted FOR/AGAINST/ABSTENTION decisions against real roll calls.

Inference for Society0 mapping:
- Environment: a committee chamber or roll-call environment with agenda state, speaking queue, proposal/amendment records, and vote records.
- FoVs: agent role, party/group, constituency or persona facts, bill text, previous speeches, visible coalition/group lines, and prior votes only when part of the design.
- Actions: `speak`, `question`, `propose_amendment`, `support`, `oppose`, `abstain`, `vote`, `summarize_position`, `request_information`.
- Hosted constraints: speaking order, time limit, committee membership, vote eligibility, public/private vote status, group-line visibility, amendment deadlines.
- Measures: vote accuracy, stance consistency, bipartisan language, amendment uptake, expert believability, agreement text similarity, and minority-position preservation.

Do not evaluate only the final vote. Store the transcript, bill/proposal ID, role
prompt, visible information, vote, reasoning, and any post-hoc reflection.

### Coalition Bargaining and Government Formation

Use when parties negotiate over manifestos, policy planks, office benefits,
coalition agreements, or shared program text.

Supported by paper:
- POLCA frames coalition negotiation as a task over party manifestos and final coalition agreement inclusion.
- The paper states the POLCA dataset is not publicly available.

Inference for Society0 mapping:
- Environment: bargaining table with parties, agenda items, party positions, proposed clauses, concessions, vetoes, and draft agreement versions.
- FoVs: party manifesto snippets, current draft, partner proposals, negotiation history, payoff/priority summaries if modeled.
- Actions: `offer_clause`, `accept_clause`, `reject_clause`, `counter_offer`, `concede`, `request_package`, `finalize_agreement`, `walk_away`.
- Hosted constraints: minimum coalition size, party compatibility, issue salience, unanimity or majority requirement, maximum rounds, and allowed package deals.
- Measures: clause inclusion, partial inclusion, agreement similarity, number of rounds, concession balance, stability, and unresolved conflict.

Unknown or unavailable: do not promise direct POLCA reproduction unless the
dataset/license/access path is available.

### Commons, Public Goods, and Sanctions

Use when agents must share a resource, decide whether to cooperate, enforce norms,
or prevent collapse.

Supported by paper and official code/config:
- GovSim defines fishery, pasture, and pollution experiments through Hydra configurations, including default, universalization, no-language ablation, and greedy-newcomer perturbation experiments.
- GovSim's official README exposes experiment IDs, multi-LLM configuration, and supported analysis utilities.

Inference for Society0 mapping:
- Environment: resource ledger with regeneration/depletion equations, group communication phase, action phase, observation phase, and termination condition.
- FoVs: current common resource, prior group actions, communication transcript, personal payoff, public collapse risk, and rule/sanction state.
- Actions: `extract`, `contribute`, `limit_use`, `warn`, `negotiate_rule`, `sanction`, `forgive`, `defect`, `explain_hypothesis`.
- Hosted constraints: resource capacity, replenishment rate, extraction cap, sanction cost, newcomer entry, communication availability, universalization prompt availability.
- Measures: survival/collapse, resource trajectory, inequality, cooperation rate, sanction rate, rule adoption, hypothesis quality, and communication ablations.

Required ablations: no language / no communication; rule-only agents or fixed
strategies; resource dynamics held constant while LLM reasoning varies; repeated
seeds and model/provider versions logged.

### Norm Creation, Evaluation, and Compliance

Use when the question is about institutional culture, emergent rules, informal
sanctions, etiquette, or compliance with social norms.

Supported by paper and official code/config:
- CRSEC separates norm Creation & Representation, Spreading, Evaluation, and Compliance modules.
- The official repository maps these modules to prompt folders and Python files under `reverie/backend_server/norm`.

Inference for Society0 mapping:
- Environment: social setting with event records and a norm registry.
- FoVs: local event, observed violation, candidate norm, retrieved norms, compliance expectation, and peer reactions.
- Actions: `create_norm`, `cite_norm`, `spread_norm`, `evaluate_norm`, `comply`, `violate`, `sanction`, `revise_norm`.
- Hosted constraints: who can propose norms, how norms become active, decay or consolidation rules, sanction visibility, and conflict resolution between norms.
- Measures: conflict count, norm adoption, norm retrieval accuracy, compliance rate, sanction frequency, human-evaluation agreement, and drift across time.

Do not hide the norm in a persona paragraph. Keep it as an environment record so
runs can explain which norm existed, when it spread, and why an agent complied.

### Prosocial Norms, Fairness, and Cooperation Probes

Use when the question is about helping, donating, volunteering, recycling,
cooperating, indirect reciprocity, reputation, punishment, fairness perception,
or erosion of prosocial norms under policy conditions.

Supported by paper and official code/config:
- ProSim initializes agents with demographic and psychological traits, evaluates
  six prosocial scenarios, embeds agents in a Watts-Strogatz network, tests
  cognitive/behavioral and voluntary/imposed policy interventions, introduces
  reward or burden asymmetry, and tracks perceived unfairness and prosocial
  contagion against human benchmark tasks.
- Cultural Evolution of Cooperation uses an iterated Donor Game across
  generations. Agents observe recent behavior, decide donation amounts, may have
  costly punishment affordances, carry resources/reputation traces, and transmit
  surviving strategies to the next generation.

Inference for Society0 mapping:
- Environment: prosocial scenario board or donor-game env with resources,
  partner assignment, policy arm, asymmetry condition, reputation trace, network
  activation, and generation boundary.
- FoVs: scenario description, own traits/state, policy framing, neighbor
  observations, recipient resources/reputation, prior round traces, and current
  fairness condition.
- Actions: `help`, `donate`, `volunteer`, `cooperate`, `share_information`,
  `recycle`, `donate_amount`, `punish`, `abstain`, `rate_unfairness`,
  `revise_strategy`.
- Hosted constraints: payoff equations, donation bounds, punishment cost,
  recognition/benefit asymmetry, burden asymmetry, active network edge sampling,
  selection/survival rule, and strategy inheritance.
- Measures: prosocial intention or action, resources, cooperation rate,
  punishment rate, perceived unfairness, norm erosion, contagion by network
  distance, generation-level strategy change, and human/model alignment.

Keep interpretation narrow. These designs probe norm mechanisms and model
behavior under controlled conditions; they do not prove real communities will
cooperate or that a policy is fair.

### Content Governance and Moderation

Use when the institution is a platform rule system: flagging, fact-checking,
moderation, appeals, Community Notes-like mechanisms, or policy interventions.

Supported by paper and official code/config:
- MOSAIC models liking, sharing, flagging, fact-checking, following, posts, and comments in a directed social graph.
- Its README lists experiment arms: no fact checking, third-party fact checking, community-based fact checking, and hybrid fact checking.
- Its official repository includes configuration, personas, social feed records, human-study data, SQLite outputs, and post-simulation analysis scripts.

Inference for Society0 mapping:
- Environment: platform governance env with posts, feed visibility, flags, fact-check decisions, user interactions, enforcement outcomes, and appeal state.
- FoVs: post content, source/account info, fact-check labels, peer engagement, moderation history, and visible rule text.
- Actions: `post`, `like`, `share`, `comment`, `flag`, `fact_check`, `apply_label`, `remove`, `appeal`, `restore`, `ignore`.
- Hosted constraints: moderation policy arm, flag thresholds, reviewer role, label visibility, appeal window, recommender exposure, and enforcement state.
- Measures: non-factual-content spread, engagement, flag precision/recall, moderation burden, appeal outcomes, trust, and explanation/behavior alignment.

If the main mechanism is feed diffusion or misinformation spread, use
`communication-social-media-simulation-design.md` first. Return here for the
governance-specific policy arm and enforcement logic.

### Election, Representative Voting, and Public-Opinion Policy Simulation

Use when the study asks how voters or representatives choose under explicit
population construction, persona prompts, party/group constraints, and validation
against polls or roll-call data.

Supported by paper:
- ElectionSim builds a large voter pool, population distributions, and a poll-based presidential election benchmark.
- PAA models legislative roll-call votes using political actor profiles and bill/voting records.
- European Parliament voting releases personas and simulation code, and studies persona attributes, reasoning, public roll-call prompts, speeches, and counterfactual speeches.

Inference for Society0 mapping:
- Environment: ballot or roll-call env, not open-ended debate by default.
- FoVs: voter/representative profile, proposal or candidate information, party/group/national-party signals, visible speeches, and poll/benchmark split.
- Actions: `vote_for`, `vote_against`, `abstain`, `explain_vote`, `revise_after_speech`, `answer_poll`.
- Hosted constraints: eligibility, public/private vote, party whip/group line, district/state/group distribution, abstention option, and benchmark holdout.
- Measures: micro/macro F1, state/group accuracy, calibration, subgroup error, baseline prompt comparison, and sensitivity to persona attributes.

Election simulation should be presented as method testing, calibration, or
sensitivity analysis unless independently validated for the exact population and
election context.

### Policy-Lab and Public-Policy Analysis Simulations

Use when policy analysts want scenario exploration, stakeholder rehearsal, or
mechanism testing rather than reproducing one specific institution.

Supported by paper:
- MLAB maps heterogeneous LLMs to educational/income groups for an economic and public policy case study on interest-income taxation.
- The policy-preconditions paper argues that societal-scale LLM simulations face dual-use and validation risks and proposes participation and accountability preconditions.

Inference for Society0 mapping:
- Environment: policy sandbox with explicit policy lever, stakeholder groups, resource or welfare metric, scenario assumptions, and interpretation record.
- FoVs: policy text, baseline condition, personal/group situation, public explanation, prior round outputs, and uncertainty caveats.
- Actions: `choose_response`, `allocate`, `comply`, `object`, `comment`, `revise_assumption`, `report_risk`.
- Measures: distributional impact, sensitivity to assumptions, stakeholder disagreement, welfare metrics, explanation categories, and validation gaps.

These simulations can support exploration and rehearsal. They cannot replace
affected-community participation, expert review, legal analysis, or official
policy evaluation.

## Loading Order

For governance, institution, and public-policy work, load only the files needed
for the target:

1. `research-design.md`
2. `environment-design.md`
3. `agent-design.md`
4. `step-dsl.md`
5. `simulation-paper-distillation.md` when adapting a paper
6. This guide
7. `communication-social-media-simulation-design.md` when feeds, diffusion,
   recommendations, or misinformation are the main mechanism
8. `interview-survey-deliberation-simulation-design.md` when surveys,
   respondent simulation, focus groups, or deliberation protocols dominate
9. Economics/finance guides when macro, market, banking, or expectations
   mechanics dominate
10. `run-monitor-analyze.md` when planning metrics, repeated runs, qualitative
   process review, or validation

Load this guide when the user mentions governance, institutions, public policy,
policy simulation, legislatures, committees, coalitions, government formation,
manifestos, roll-call voting, elections, representative voting, commons,
public goods, sanctions, norm emergence, rule compliance, legitimacy,
accountability, content governance, moderation policy, fact-checking arms,
appeals, Community Notes-like mechanisms, GovSim, CRSEC, MOSAIC, ElectionSim,
Political Actor Agent, European Parliament voting, Artificial Leviathan, or
policy-use preconditions, ProSim, prosocial behavior, policy-induced inequity,
fairness contagion, donor game, indirect reciprocity, or cultural evolution of
cooperation.

## Society0 Construction Rules

### 1. Start With The Institution, Not The Agents

Define roles, permissions, lifecycle, state, and termination before personas:

- roles: voter, senator, committee chair, party negotiator, moderator, fact checker, citizen, administrator, judge, observer;
- permissions: who may speak, propose, amend, vote, flag, sanction, allocate, or appeal;
- lifecycle: agenda -> proposal -> discussion -> amendment -> decision -> enforcement -> appeal -> record;
- state: rules, resources, draft text, ballots, sanctions, public signals, active norms, moderation labels;
- termination: vote threshold, agreement, collapse, rounds exhausted, appeal resolved, resource exhausted, policy period ended.

A governance simulation without explicit permissions and records is just a
conversation simulation with political vocabulary.

### 2. Use Typed Actions For Institutional Moves

Use free-text only inside typed institutional actions. Prefer:

```python
ctx.group(parties).instruct(
    "Submit one negotiation move.",
    output_schema={
        "action": "offer_clause|accept_clause|reject_clause|counter_offer|walk_away",
        "issue": "string",
        "clause": "string",
        "reason": "string",
        "concession": "string"
    },
)
```

Do not log only a transcript. Convert each move into an environment event with
actor, target, issue, round, visibility, and downstream effect.

### 3. Separate Process Evidence From Outcome Evidence

Governance simulations need both process evidence and outcome evidence.

- Process evidence: who saw what, who spoke, what rule applied, what action was allowed, what sanction or label was imposed.
- Outcome evidence: vote totals, agreement clauses, resource survival, content spread, compliance, legitimacy, accuracy, or welfare.

A final majority, agreement, or resource curve is not interpretable without the
process record.

### 4. Prefer Rule Agents For Non-Language Institutions

Use deterministic code or rule agents for vote counting, resource regeneration,
quorum checks, eligibility checks, flag thresholds, sanction application,
roll-call aggregation, social-choice execution, and budget/accounting updates.
Use LLM agents where interpretation, bargaining, persuasion, norm reasoning, or
explanation is the mechanism under study.

### 5. Validate Against The Right Benchmark

Choose the benchmark before running the simulation:

- roll-call voting: historical vote records and group/subgroup error;
- committee debate: expert believability plus transcript/process checks;
- coalition bargaining: public coalition agreements, manifesto alignment, and clause-level inclusion metrics;
- commons: survival/collapse, resource trajectory, and no-language/rule baselines;
- moderation: human-study reactions, fact-check labels, spread metrics, and appeal/enforcement records;
- policy lab: external data, expert review, affected-community participation, and stated non-use boundaries.

Never report simulated policy preferences as public opinion without a human or
official benchmark and a scope statement.

## Minimal Scaffold Checklist

```text
Institution:
Decision or rule under study:
Roles and permissions:
Proposal / content / resource / ballot objects:
Visibility / FoV rules:
Typed actions:
Hosted constraints:
Aggregation rule:
Enforcement / sanction / appeal rule:
Baseline condition:
Treatment condition:
Validation benchmark:
Human participation or expert review plan:
Records to save:
Boundary statement:
```

MVP patterns:

- Committee: 5-8 role agents, one agenda item, one chair/rule agent, two debate rounds, one vote, expert/process review.
- Coalition: 2-4 parties, three policy issues, maximum five rounds, clause-level agreement record.
- Commons: one resource, one communication treatment, one no-language baseline, 20 repeated seeds.
- Norms: one social setting, one candidate norm, explicit creation/spread/compliance records.
- Moderation: 50-200 users, fixed news/content set, one moderation arm versus no moderation, stored flags/labels/interactions.
- Voting: one proposal set, fixed persona/profile fields, held-out roll-call or poll benchmark, persona-ablation runs.

## Baselines And Ablations

Use at least two of these for any governance claim:

- no-institution baseline: agents answer individually without the institutional protocol;
- rule-only baseline: deterministic actors or fixed strategies;
- no-language baseline: institutional mechanics without negotiation/discussion;
- no-memory baseline: current FoV only;
- no-group-line baseline: remove party/coalition cues;
- no-sanction baseline: compliance without enforcement;
- public/private vote ablation;
- moderation-arm comparison;
- participation/validation ablation: show what cannot be claimed without it;
- model/provider/temperature repeated-seed robustness.

## Interpretation Boundaries

Use these labels in outputs and docs:

- `Supported by paper`: the mechanism or result is described in the checked paper text.
- `Supported by official code/config`: the mechanism is present in the checked repository README, config, or source tree.
- `Inference for Society0 mapping`: a design translation from the evidence into Society0 primitives.
- `Unknown or unavailable`: code, data, full text, or implementation details were not found or not accessible.

For consequential policy work, also include who commissioned the simulation, who
validated it, what affected communities participated, what populations or
decisions are explicitly out of scope, whether output may enter a policy record,
and what independent evidence could falsify the result.

## Common Failure Modes

- **Institution-free debate**: agents talk about politics, but there is no agenda, permission, vote, sanction, or decision rule.
- **Outcome-only logging**: final vote or agreement is saved without process evidence.
- **Policy oracle framing**: simulated outcomes are described as what government should do.
- **Participation laundering**: simulated marginalized or affected populations are treated as community participation.
- **Data opacity**: public policy or voting claims depend on inaccessible data, private datasets, or undocumented preprocessing.
- **Coalition overclaiming**: agreement text similarity is treated as successful political forecasting.
- **Election overclaiming**: calibrated simulation is presented as an election forecast without exact-context validation.
- **Moderator laundering**: an LLM moderator summarizes away disagreement, minority positions, appeals, or rule violations.
- **Sanction ambiguity**: punishments or enforcement outcomes are generated in prose but not applied to environment state.
- **Rule drift**: institutional rules change during the run without a recorded amendment event.
- **Prompt-history leakage**: historical vote records or policy outcomes leak into test decisions.
- **No accountability path**: outputs could influence decisions but no owner, validator, participation plan, or caveat is recorded.
- **Cross-guide confusion**: feed diffusion is routed here instead of the communication guide, or survey/respondent simulation is routed here instead of the interview guide.
