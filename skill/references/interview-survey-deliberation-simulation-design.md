# Interview, Survey, Deliberation, And Social Psychology Simulation Design

Use this guide for Society0 simulations about LLM respondents, survey experiments,
social psychology experiment replication, vignette/scenario experiments, deliberation,
group discussion, focus groups, mini-publics, citizens' juries, Habermas Machine-style
consensus systems, silicon samples, interview-grounded agents, and generative agent
simulations of people. Load it after `research-design.md`, `environment-design.md`,
`agent-design.md`, and `step-dsl.md`; also load `simulation-paper-distillation.md`
when adapting a paper.

This is not a generic generative-agent or AgentSociety architecture guide. It distills
respondent simulation and deliberative protocols into Society0's env-first scaffold:
sample, stimulus, FoVs, actions, hosted protocol, records, measurements, validation,
and interpretation boundaries.

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

Model this domain as **measurement and protocol-hosted simulation**:

```text
sample / persona source -> bounded stimulus or discussion FoV
-> LLM response or deliberative action -> env/protocol update
-> audit rows -> human benchmark, replication, or robustness check
```

The environment owns the research design: sample frame, quotas, persona data,
survey instrument, treatment assignment, scenario text, hidden labels, randomization,
conversation structure, moderator rules, ranking or voting rules, candidate generation,
social-choice aggregation, critique rounds, output records, and benchmark tables. LLM
agents own situated response: answering, reasoning, narrating, critiquing, ranking,
voting, revising, and expressing stance inside the bounded information they are given.

Use `plain` for surveys, vignettes, and action-free measurement. Use
`round_robin_conversation` for paired or rotating discussion when the protocol is simple.
Use a custom env or explicit code steps when the study requires a deliberation protocol,
jury, social choice rule, group statement workflow, evidence ledger, or stance-update
state.

Use `interview` for measurement: survey answers, manipulation checks, post-hoc judgments,
qualitative explanations, final private opinions, and structured study outcomes. Use
`instruct` with environment actions for behavior that changes the simulated world:
speaking in a discussion, submitting a critique, ranking candidate statements, voting,
changing a ballot, proposing a group statement, or making a final public commitment.
Do not replace action-bearing deliberation with direct JSON output just to reduce cost.

LLM respondents are not human evidence. Treat them as pilot, exploratory,
design-refinement, or calibrated simulation instruments unless a target population,
task, benchmark, and validation regime have already established domain-specific fidelity.
Distinguish:

- **Interview-grounded individual simulation**: tries to predict the same participant's
  responses from self-reports or interviews.
- **Silicon samples**: try to reproduce population or subgroup distributions, not
  individual truth.
- **Scenario replication**: tests whether LLM samples reproduce effects from existing
  vignette experiments.
- **Deliberation systems**: host a protocol for generating, exchanging, ranking,
  critiquing, or revising positions.
- **Stance dynamics**: explicitly model how evidence or prior anchoring changes stated
  position over time.

Keep hidden treatment labels and benchmark answers in `properties`, run config, or
output tables, not in FoVs. Audit leakage aggressively. Keep memory when the modeled
respondent or discussion history depends on it; ablate memory only as a named
experimental condition.

## Evidence Map

Read `simulation-paper-distillation.md` before using this guide for paper reproduction.
The following sources were checked through official papers, official PDFs/HTML where
available, official repositories, official data repositories, or author-listed artifacts.
Candidate lists are not evidence.

| Paper or source | Evidence status | Main design target |
| --- | --- | --- |
| "LLM Agents Grounded in Self-Reports Enable General-Purpose Simulation of Individuals" / Generative Agent Simulations of 1,000 People, arXiv 2411.10109, https://arxiv.org/abs/2411.10109 and https://github.com/StanfordHCI/genagents | Full arXiv PDF inspected; official repository checked. Full interview agent bank is not public; code provides sample agent, GSS demographic agents, response APIs, memory, and prompt templates. | Interview- and survey-grounded individual respondent simulation. |
| "Using Large Language Models to Simulate Multiple Humans and Replicate Human Subject Studies", PMLR/ICML 2023, https://proceedings.mlr.press/v202/aher23a.html and https://github.com/microsoft/turing-experiments | Full PMLR PDF inspected; official repository checked with prompt templates, scripts, result data, and notebooks. | Turing Experiments for behavioral economics, psycholinguistics, social psychology, and distortion detection. |
| "Out of One, Many: Using Language Models to Simulate Human Samples", Political Analysis, https://doi.org/10.1017/pan.2023.2 and Harvard Dataverse https://doi.org/10.7910/DVN/JPV20K | Full paper PDF inspected; official replication data/code metadata checked in Harvard Dataverse. | Silicon samples and algorithmic fidelity for U.S. public opinion distributions. |
| "Can Large Language Models Replace Human Subjects? A Large-Scale Replication of Scenario-Based Experiments in Psychology and Management", arXiv 2409.00128 and OSF https://osf.io/j6wmn/ | Full arXiv PDF inspected; official OSF file listing checked with README, LLM API calls, study-level analysis, replication analysis, and temperature folders. | Large-scale scenario/vignette replication and replacement boundary testing. |
| "Position: LLM Social Simulations Are a Promising Research Method", arXiv 2504.02234 | Full arXiv PDF inspected. | Method stance: pilot/exploratory use, diversity, bias, sycophancy, alienness, and generalization challenges. |
| "Plurals: A System for Guiding LLMs Via Simulated Social Ensembles", CHI 2025/arXiv 2409.17213 and https://github.com/josh-ashkinaze/plurals | Full arXiv PDF inspected; official repository and `plurals/instructions.yaml` checked. | Simulated focus groups, ANES personas, deliberation structures, moderators, and templates. |
| "AI can help humans find common ground in democratic deliberation", Science 2024, DOI 10.1126/science.adq2852 and https://github.com/google-deepmind/habermas_machine | Official Science page was Cloudflare-blocked in this pass. Official DeepMind repository, dataset README, source code, and Google Cloud data links checked. | Habermas Machine-style common-ground generation, personalized ranking, social choice, and critique rounds. |
| "Belief Engine: Configurable and Inspectable Stance Dynamics in Multi-Agent LLM Deliberation", arXiv 2605.15343 | Full arXiv PDF inspected. Paper lists an anonymous reproducibility artifact; no stable public official repository was confirmed. | Inspectable evidence uptake, prior anchoring, structured memory, and stance dynamics in deliberation. |
| "Six Fallacies in Substituting Large Language Models for Human Participants", DOI 10.1177/25152459251357566 / arXiv 2402.04470 | Full arXiv PDF inspected. | Boundary and language discipline against human-participant replacement claims. |
| "Large Language Models Do Not Simulate Human Psychology", arXiv 2508.06950 | Full arXiv PDF inspected. | Cautionary evidence about prompt sensitivity, model variance, semantic perturbations, and replacement limits. |
| "Towards Deliberating Agents" | Candidate only. Downloaded artifact was HTML rather than a readable PDF; no stable official full text was used as evidence in this pass. | Unknown or unavailable. Do not cite for design rules until official full text is retrieved. |

If a user needs paper-specific reproduction, re-check the latest paper version, appendix,
repository state, data availability, and licensing before claiming exact fidelity.

## Target Taxonomy

Choose the target by the research object being simulated:

- **LLM respondents and surveys**: agents answer fixed survey items, qualitative prompts,
  manipulation checks, or interviews. Use `interview` by default.
- **Silicon samples**: a population distribution or subgroup pattern is simulated from
  data-grounded backstories or personas. The unit of validation is distributional
  correspondence, not individual accuracy.
- **Interview-grounded individual agents**: a participant's interview or self-report
  record grounds later responses on held-out measures or scenarios. The unit of validation
  is test-retest, held-out survey, or same-person benchmark.
- **Scenario or vignette experiment replication**: LLM respondents receive
  treatment/control scenarios and measures copied or adapted from human-subject studies.
- **Turing Experiments**: LLM samples are used to test whether a model replicates known
  experimental patterns or reveals systematic distortions.
- **Deliberation and group discussion**: agents exchange statements, reasons, critiques,
  or proposals under a hosted structure such as pair discussion, chain, debate, focus group,
  jury, or mini-public.
- **Common-ground or consensus systems**: a mediator generates candidate statements,
  predicts participant preferences, aggregates rankings, gathers critiques, and revises
  statements.
- **Stance or belief dynamics**: the study targets how evidence, memory, identity, or
  anchoring changes position over turns.
- **Boundary and replacement audits**: the study evaluates when simulated participants
  are inappropriate, unstable, biased, or unvalidated.

Use the communication guide when feeds, platforms, diffusion, recommendations, or
social-media affordances are the core mechanism. Use economics/finance guides when the
target is macro expectations, forecasters, markets, or bank behavior.

## Loading Order

For interview, survey, and deliberation work, load only the files needed for the target:

1. `research-design.md`
2. `environment-design.md`
3. `agent-design.md`
4. `step-dsl.md`
5. `simulation-paper-distillation.md` when adapting a paper
6. This guide
7. `run-monitor-analyze.md` when planning repeated runs, output tables, validation, or qualitative review
8. Domain-specific guides when the content domain dominates the respondent method
9. Source files only when implementing or debugging a concrete env: start from `src/society0/env/`, `src/society0/environment.py`, `src/society0/schedule.py`, and `src/society0/agent/core.py`

Load this guide when the user mentions LLM respondents, AI respondents, simulated
participants, human subjects replacement, survey experiments, vignette experiments,
scenario experiments, social psychology experiment replication, silicon samples,
generative agent simulations of people, interview-grounded agents, self-report agents,
Turing Experiments, Out of One Many, Can LLMs Replace Human Subjects, Plurals, Habermas
Machine, Belief Engine, focus groups, deliberation, group discussion, citizens' juries,
mini-publics, common ground, stance update, or "do not simulate human psychology."

## General Construction Rules

Use this env-first split in every respondent or deliberation simulation:

| Component | Society0 location |
| --- | --- |
| Sample frame, quotas, weights, persona source, interview transcripts, survey batteries, treatment arms, scenario text, randomization, benchmark answers | env config, run config, agent `properties`, or output tables |
| Persona text, visible role, current belief, public stance, interview summary, prior conversation history | agent `state` when visible; `properties` when hidden |
| Hidden treatment label, true condition, held-out answer, original human effect, leakage flags, sampling cell, validation split | hidden `properties`, step params, or output tables, not FoV |
| Survey item, scenario, vignette, partner message, prior round summary, candidate statements, ranking ballot, critique prompt | FoV |
| Answer survey item, give qualitative explanation, report private final opinion, complete manipulation check | `interview` |
| Speak, ask, critique, rank, vote, propose, revise, submit final group statement | `instruct` with typed env actions and semantic terminal actions |
| Treatment delivery, candidate generation, statement ranking, social-choice aggregation, evidence ledger, stance update, moderator summary | env rules, behaviors, or code steps |
| Metrics, raw responses, parsed outputs, invalid-output rows, ballots, critique rows, stance trajectories, benchmark comparisons | `ctx.result(metrics=..., tables=...)` plus analysis scripts |

Design rules:

- Define the empirical claim before the prompt: pilot, distributional approximation,
  same-person prediction, treatment replication, deliberation protocol test, or interface
  design tool.
- Keep sampling and respondent construction auditable. Record the population source,
  variables used, excluded variables, missing-data handling, weights, and whether profiles
  are real, synthetic, or generated.
- Separate stimulus from measurement. FoV shows only what a human participant would see;
  treatment labels and benchmark outcomes stay hidden.
- Preserve raw output and parsed values. Store parser errors, retries, model, temperature,
  prompt version, item ID, treatment, seed, and sample cell.
- Freeze prompt adaptations before testing outcomes when reproducing experiments. If
  adaptation is needed, record why and mark it approximate.
- Treat invalid output as data: refusals, noncompliance, impossible JSON, answer-category
  mismatches, and overlong answers.
- Do not treat demographic prompts as identity. Use real survey variables or user-approved
  distributions when the population claim matters; validate between-group and within-group
  diversity.
- For group discussion, make the protocol explicit: who speaks, what they see, when
  critiques happen, how summaries are produced, how votes/rankings are counted, and when
  the round ends.
- For stance change, maintain explicit state or evidence records. A transcript alone cannot
  explain whether movement came from evidence uptake, anchoring, sycophancy, or prompt drift.
- Use memory as a modeled mechanism. In interview-grounded or multi-round deliberation
  settings, `memory=True` is usually part of the design.

## Paper-Derived Patterns

### Measurement Mode Is Not Action Mode

Supported by paper:
  Generative Agent Simulations of 1,000 People, Turing Experiments, and scenario
  replication studies elicit responses to fixed stimuli and compare outcomes against human
  benchmarks.

Supported by official code/config:
  The StanfordHCI `genagents` repository exposes `categorical_resp`, `numerical_resp`, and
  `utterance`. The Microsoft Turing repository provides prompt templates, notebooks, result
  files, and Milgram procedure code. The OSF project lists LLM API calls and analysis folders.

Inference for Society0 mapping:
  Map fixed survey and post-hoc measurement to `interview`. Map deliberative speech,
  rankings, critiques, votes, and final submissions to `instruct` actions.

Unknown or unavailable:
  Some paper-specific prompts, private data, and full agent banks may be restricted.

### Interview Grounding Is A Different Claim From Thin Persona Prompting

Supported by paper:
  The 1,000-person generative-agent paper builds agents from a stratified U.S. sample of
  1,052 participants using two-hour American Voices Project interviews, structured GSS and
  Big Five surveys, or both. It reports held-out GSS performance near participants' own
  test-retest consistency and evaluates Big Five, economic games, and scenario replications.

Supported by official code/config:
  The `genagents` repository provides a sample interview-grounded agent, GSS demographic
  agent bank, memory stream, retrieval, reflection, categorical/numerical response, and
  open-ended utterance APIs. The README states the full interview agent bank is not public.

Inference for Society0 mapping:
  Use interview transcripts or user-provided self-reports as agent `properties` or private
  memory source. Use FoVs for held-out tasks and `interview` for measurement. Record source:

```text
agent_source:
  demographic_only | interview_only | survey_only | survey_plus_interview
grounding_data:
  fields_used:
  held_out_fields:
  leakage_check:
```

Unknown or unavailable:
  Exact reproduction of the full 1,052 interview-agent bank is unsupported without restricted
  data and appendix/code version checks.

### Silicon Samples Are Distributional, Not Individual Twins

Supported by paper:
  Out of One, Many conditions GPT-3 on socio-demographic backstories from ANES and
  Pigeonholing Partisans data and defines algorithmic fidelity through social science Turing
  tests, backward continuity, forward continuity, and pattern correspondence.

Supported by official code/config:
  Harvard Dataverse replication metadata provides data and code files, including study data,
  R/Python scripts, tables, temperature variants, and `GPT3_OtherModels_DataGenerationCode.pdf`.

Inference for Society0 mapping:
  Use silicon samples when the claim is subgroup or population distribution. Store weights and
  cell variables; report marginal distributions, subgroup estimates, and correlation/pattern
  correspondence. Do not interpret a simulated row as the real respondent behind a backstory.

Unknown or unavailable:
  Fidelity outside U.S. public opinion, the paper's time window, and checked survey domains
  must be revalidated.

### Turing Experiments Need Prompt Validation Before Outcome Testing

Supported by paper:
  Turing Experiments require simulating a representative sample rather than one arbitrary
  person. The method validates prompts by maximizing validity rate before examining outcomes
  to avoid p-hacking. It covers Ultimatum Game, Garden Path, Milgram-like destructive
  obedience, and Wisdom of Crowds, including hyper-accuracy distortion.

Supported by official code/config:
  The Microsoft repository provides prompt templates, notebooks, result data, scripts,
  surnames, and Milgram procedure code.

Inference for Society0 mapping:
  Add a pre-outcome prompt-validation step:

```text
prompt_validation:
  validity_criteria:
  pilot_n:
  frozen_prompt_version:
  outcome_blinded: true
```

  Store validity failures separately from treatment effects. Use Turing Experiments to detect
  model distortions, not only successes.

Unknown or unavailable:
  Deprecated model endpoints and partial Milgram data limit exact reruns.

### Scenario Replication Requires Feasibility Filters And Adaptation Logs

Supported by paper:
  The large-scale psychology/management replication study selects 156 scenario-based
  experiments from 2015-2024 and excludes self-reported lived experience, priming of
  motivation/emotion/cognition, physiological or behavioral observation, longitudinal
  designs, and team/group interactions. It reports stronger main-effect than interaction
  replication, larger LLM effect sizes, null human effects becoming significant, and lower
  performance on socially sensitive race/gender/ethics topics.

Supported by official code/config:
  The official OSF project lists README, `01 LLM API Calls`, `02 Study-level analysis`,
  `03 LLM replication analysis`, and `temperature` folders.

Inference for Society0 mapping:
  Before coding, classify each study:

```text
replication_feasibility:
  scenario_text_available:
  outcome_measure_available:
  requires_lived_experience:
  requires_physical_behavior:
  requires_longitudinal_process:
  requires_team_interaction:
  prompt_adaptation_needed:
  adaptation_reason:
```

  Treat adapted prompts as approximate. Report main effects and interactions separately.

Unknown or unavailable:
  Scenario adaptations and study-level files must be inspected before exact reproduction.

### Deliberation Requires Hosted Protocols, Not Just "Talk"

Supported by paper:
  Plurals separates Agents, Structures, and Moderators. Structures define information sharing;
  combination instructions define how agents use prior responses. The paper frames deliberation
  as inspired by human mini-publics, not a substitute for human deliberation.

Supported by official code/config:
  The Plurals repository provides `instructions.yaml` with ANES, first-wave, second-wave,
  debate, jury, critique-revise, minimal-discussion, voting, and moderator templates. The
  README documents ensembles, chains, graphs, debates, moderators, ANES personas, and an
  interview class for story-based personas.

Inference for Society0 mapping:
  Represent discussion structure in env state or step order:

```text
discussion_protocol:
  structure: ensemble | chain | graph | debate | jury | focus_group
  visibility:
  speaker_order:
  cycles:
  moderator_role:
  final_product:
```

  Use `round_robin_conversation` only when a simple turn structure is enough. Use a custom
  env when visibility, moderation, or final products are core.

Unknown or unavailable:
  Plurals efficacy results do not prove human-focus-group replacement.

### Habermas-Style Common Ground Separates Generation, Ranking, Social Choice, And Critique

Supported by paper:
  The Science paper itself was not readable from the official page in this pass, so paper-specific
  claims are not used beyond the official repository's citation and README description.

Supported by official code/config:
  The Google DeepMind repository describes a dataset of candidate comparisons, final preference
  rankings, position-statement ratings, and round survey responses. `machine.py` implements a
  mediator that gathers opinions, generates 16 candidate statements by default, predicts each
  participant's ranking, aggregates rankings with a social choice method, gathers critiques of
  the winner, generates revised statements, and repeats if configured. The README states the
  prompted version differs from the paper's fine-tuned version and uses AI Studio/Gemini by default.

Inference for Society0 mapping:
  Implement common-ground systems as hosted steps:

```text
opinion_round -> generate_candidates -> private_rankings
-> social_choice -> critique_round -> revised_candidates
-> final_rankings / final_statement
```

  Store candidate rows, per-agent ranking rows, social-choice inputs, tied/untied rankings,
  critique rows, and final preference shifts.

Unknown or unavailable:
  Exact Science-paper reproduction is unsupported here because the full official article text
  was blocked and the public prompted repository differs from the fine-tuned paper system.

### Stance Change Needs Inspectable Update State

Supported by paper:
  Belief Engine separates argument extraction, evidence judgment, active/archive memory,
  log-odds belief updating, stance computation, and response generation. It exposes evidence
  uptake and prior anchoring controls and evaluates parameter sweeps plus DEBATE human replay.

Supported by official code/config:
  The paper lists an anonymous reproducibility artifact with source code, configs, prompt
  strings, compact result artifacts, and validation metadata. No stable public official
  repository was confirmed in this pass.

Inference for Society0 mapping:
  When stance change is the target, add explicit state and records:

```text
stance_state:
  proposition:
  stance_score:
  prior_anchor:
  evidence_uptake:
evidence_record:
  claim:
  polarity:
  strength:
  active:
  source_agent_id:
  turn:
```

  Use rules or behaviors for the update equation and `instruct` for generated deliberative text.

Unknown or unavailable:
  Exact implementation requires the artifact and dataset access. A single scalar stance is a
  modeling choice, not a general psychology model.

### Replacement-Boundary Papers Are Part Of The Method

Supported by paper:
  Six Fallacies warns against token prediction as intelligence, average-human assumptions,
  alignment-as-explanation, anthropomorphism, identity essentialization, and substitution.
  Large Language Models Do Not Simulate Human Psychology argues that prompt/model sensitivity,
  semantic rewording, bias, diversity gaps, hallucination, and token-similarity generalization
  undermine replacement claims. The Promising Research Method position paper supports cautious
  pilot and exploratory use while naming diversity, bias, sycophancy, alienness, and generalization.

Supported by official code/config:
  No official runtime code was needed for these boundary sources in this pass.

Inference for Society0 mapping:
  Every respondent-simulation design should include an interpretation field:

```text
claim_level:
  pilot | exploratory | calibrated_distribution | same_person_prediction | replacement_claim
human_validation:
  benchmark:
  recency:
  population_match:
  failure_boundary:
```

  Default claim level should be pilot or exploratory unless validation supports more.

Unknown or unavailable:
  Cautionary papers do not provide a universal numeric threshold for "valid enough."

## Dirty-Work Triage

Respondent and deliberation simulations fail when sample construction, treatment design,
and validation are hidden. Triage the hard work explicitly:

| Relationship to Society0 | Examples in this domain | Agent behavior |
| --- | --- | --- |
| Society0-native modeling | survey FoVs, treatment assignment, hidden labels, interview/action split, action schemas, discussion structure, ranking/voting, social choice, evidence ledger, stance state, output tables | Build into env state, rules, actions, steps, and records. |
| Agent can prepare | extract paper prompts, draft survey schemas, normalize public replication metadata, create persona tables from user-approved distributions, write analysis scripts, code parsing/validity checks | Do the work when sources and permissions are available; state assumptions and source labels. |
| User input required | target population, construct definitions, private survey/interview data, consent/IRB limits, acceptable prompt adaptations, validation benchmark, claim threshold | Ask for the decision or data; label placeholders. |
| External pipeline | restricted interview banks, OSF/Dataverse downloads, provider credentials, large model batches, human benchmark collection, qualitative coding panels | Scaffold or run only with access and permission. Keep public docs provider-neutral. |
| Methodological/statistical | sample weights, post-stratification, repeated seeds, bootstrap uncertainty, effect-size comparison, interaction tests, prompt validation before outcomes, leakage audit | Implement or specify the analysis; ask the user to confirm construct validity and benchmark relevance. |
| Scale/cost operations | thousands of respondents, full scenario batteries, multi-provider comparisons, deliberation rounds, candidate/ranking explosion | Start with a pilot; scale only after traces, parsers, and validation logic are sound. |

Minimum dirty-work questions:

- What human population or participant frame is being simulated?
- Is the claim individual prediction, distributional patterning, effect replication, group process, or pilot design?
- Which data ground the personas or interviews?
- Which variables are hidden labels rather than participant-visible context?
- What exactly does the participant see at response time?
- Which actions change shared state, and which are measurements?
- What benchmark or human data will validate the output?
- Which prompt or scenario adaptations were made before outcome testing?
- What failure would make the simulation invalid for the research claim?

## Society0 Scaffold Checklist

Before coding an interview, survey, deliberation, or social psychology simulation, write this scaffold:

```text
Research target:
  respondent survey / silicon sample / interview-grounded individual / scenario replication / Turing Experiment / deliberation / common ground / stance dynamics

Claim level:
  pilot / exploratory / calibrated distribution / same-person prediction / replication probe / unsupported replacement claim

Construct and hypothesis:
  survey response, attitude, belief, preference, treatment effect, consensus quality, evidence uptake, stance change, or group product.

Setting:
  survey instrument, scenario, discussion protocol, jury, focus group, or common-ground mediation; tick or round meaning.

Sample:
  population, source data, quotas, weights, persona variables, excluded variables, missing data, hidden cells.

Agents:
  roles, profiles, self-report grounding, interview transcript, memory policy, visible state, hidden properties, LLM vs rule boundary.

FoVs:
  exact stimulus, survey item, scenario, partner message, prior round summary, candidate statements, critique request, ballot.

Actions:
  none for pure measurement; or speak, ask, critique, rank, vote, propose, revise, submit final statement with typed arguments.

Hosted constraints:
  randomization, treatment delivery, response categories, parser rules, turn order, moderator rules, candidate count, social choice, stance update.

Dirty-work triage:
  society0_native:
  agent_can_do:
  user_input_needed:
  external_pipeline:
  methodological:
  scale_cost:

Run loop:
  sample -> assign treatment -> render FoV -> interview or instruct -> validate parse/action -> update protocol -> record -> measure.

Records:
  raw outputs, parsed responses, invalid outputs, stimulus IDs, prompt version, treatment, sample cell, ballots, critiques, candidate statements, stance/evidence rows.

Measurements:
  item distributions, subgroup correspondence, test-retest match, replication direction, effect size, p-value pattern, ranking agreement, consensus preference, stance trajectory.

Baselines:
  human benchmark, demographics-only, no persona, survey-only, interview-only, random response, majority rule, no memory, no discussion, zero-shot, alternate model/provider.

Validation:
  held-out human data, same-person test-retest, distributional fidelity, prompt validity rate, repeated seeds, leakage audit, effect-size inflation check, qualitative review.

Fidelity:
  exact / approximate / extension / unsupported by paper element.
```

Do not proceed to a large run until the scaffold makes visible information, hidden labels,
and validation targets explicit.

## Baselines/Ablations/Validation

Baseline menu:

- Human benchmark or original study statistics.
- Random response, uniform response, category-frequency response, or majority-class baseline.
- Demographics-only persona, thin persona, survey-only grounding, interview-only grounding, survey-plus-interview grounding.
- No persona, explicit demographic persona, implicit demographics such as names, data-grounded backstories.
- Zero-shot single model, chain-of-thought zero-shot, single-agent answer, ensemble answer, moderated synthesis.
- No memory, short memory, interview memory, conversation memory, evidence ledger, explicit stance state.
- No discussion, pair discussion, chain, graph, debate, jury, focus group, moderated ensemble.
- Majority vote, Borda, Condorcet/Schulze, random tie-break, moderator-only synthesis.
- No prompt adaptation, adapted prompt, frozen prompt, alternate wording, semantic perturbation.
- Alternate model/provider, temperature, top-p, parser, retry policy, and prompt version.

Validation targets:

| Target | Validation |
| --- | --- |
| Survey respondents | item distributions, subgroup distributions, response validity, nonresponse/refusal rate, repeated seeds, human benchmark |
| Silicon samples | marginal fit, subgroup fit, backward/forward continuity, pattern correspondence, Cramer's V/correlation, post-stratified estimates |
| Interview-grounded agents | held-out item accuracy, participant test-retest denominator, Big Five/economic-game correlation, leakage audit, privacy boundary |
| Scenario replication | effect direction, main vs interaction effects, p-value distribution, effect-size inflation, null-effect false positives, socially sensitive topics |
| Turing Experiments | validity rate before outcome testing, comparison to known human study, distortion detection, alternate stimulus, model/provider sensitivity |
| Deliberation/focus group | diversity of generated responses, adherence to protocol, minority voice visibility, audience preference only when measured, qualitative audit |
| Common-ground systems | candidate quality, participant ranking fit, social-choice trace, critique incorporation, final preference improvement, tie handling |
| Stance dynamics | evidence record quality, update trace, prior anchoring sensitivity, stance trajectory, human pre/post benchmark, unexplained movement |

For every target:

- Run a small pilot and inspect raw traces.
- Repeat seeds before interpreting patterns.
- Preserve rows needed to recompute metrics.
- Report model/provider, prompt version, FoV version, sample source, treatment, temperature, seed, and parser.
- Separate simulated output from empirical evidence in writeups.
- State what claim the simulation cannot support.

## Reproduction Boundaries

Use these fidelity labels:

- **exact**: the paper/code/data specify the mechanism and Society0 can implement it directly with the same inputs.
- **approximate**: the mechanism is clear, but Society0 differs in env interface, model, provider, prompts, data, scale, or infrastructure.
- **extension**: the design is inspired by the paper but intentionally adds Society0-specific structure.
- **unsupported**: source material is missing or insufficient to reproduce the element.

Paper-specific cautions:

- Generative Agent Simulations of 1,000 People: strong evidence for interview/self-report grounding and memory-backed response APIs; exact reproduction of the full interview-agent bank is unsupported without restricted data and appendix/version checks.
- Turing Experiments: strong evidence for prompt templates, validity-rate workflow, and result analysis; exact reruns may be blocked by deprecated models, LFS data availability, and cost.
- Out of One, Many: strong evidence for distributional silicon samples in U.S. public opinion; do not use it as support for individual digital twins or other domains without validation.
- Can LLMs Replace Human Subjects: useful for feasibility filtering, prompt adaptation logs, and effect-size warnings; it argues against replacement even when many main effects replicate.
- Promising Research Method: use as cautious method stance, not as proof that a specific Society0 simulation is valid.
- Plurals: useful for structures, moderators, ANES personas, and deliberation templates; not evidence that AI focus groups replace human focus groups.
- Habermas Machine: official code strongly supports the protocol decomposition; exact Science-paper reproduction is unsupported here because the full official article text was blocked and the public prompted implementation differs from the fine-tuned paper system.
- Belief Engine: useful for inspectable stance-update design; exact implementation is unsupported without retrieving and checking the anonymous artifact.
- Six Fallacies and Do Not Simulate Human Psychology: use as boundary constraints. They should lower overclaiming, not prevent clearly labeled pilot simulations.
- Towards Deliberating Agents: candidate not used as evidence in this pass because the retrieved file was HTML, not a readable official paper.

Do not present a respondent simulation as one-to-one reproduced unless all reproduction-critical
pieces are specified: sample construction, persona data, hidden labels, prompt/stimulus text,
treatment assignment, output schema, parser, invalid-output handling, memory policy, model
settings, prompt versions, validation split, baselines, ablations, and metrics.

## Failure Modes

- **Human replacement claim**: simulated respondents are presented as direct substitutes for human participants.
- **Prompt-only research design**: sample, treatment, measurement, and validation live only in prose prompts.
- **Interview/action confusion**: an action-bearing discussion is replaced with `interview`, or a survey measurement mutates shared state.
- **Hidden-label leakage**: treatment arm, true hypothesis, benchmark answer, or expected direction appears in FoV.
- **Persona essentialization**: demographic labels are treated as fixed psychology or homogeneous identity.
- **Thin-persona overclaiming**: a short persona is described as an interview-grounded individual simulation.
- **Distribution/individual confusion**: silicon samples are interpreted as individual predictions.
- **Adaptation laundering**: prompts are modified after seeing outcomes, or adaptations are not logged.
- **Effect-size inflation blindness**: direction replication is reported while inflated magnitudes and false positives are ignored.
- **Interaction collapse**: main-effect success is generalized to interaction effects without separate validation.
- **Scenario infeasibility**: studies requiring lived experience, physical behavior, longitudinal process, or real team interaction are simulated as if ordinary vignette tasks.
- **Protocol-free deliberation**: agents "discuss" without explicit turn order, visibility, final product, ranking/voting, or moderator rules.
- **Moderator laundering**: a moderator summary hides disagreement, minority positions, or social-choice choices.
- **Stance transcript illusion**: text appears to change stance, but no evidence ledger or state update explains why.
- **Memory shortcut**: memory is disabled for speed even though interviews or conversation history are part of the modeled mechanism.
- **Metric-only logging**: final averages are stored without raw responses, parsed rows, stimulus IDs, and prompt versions.
- **No human benchmark**: simulated patterns are interpreted without a relevant human comparison or stated lack of one.
- **Provider drift opacity**: model/provider/version changes are not recorded, making reruns incomparable.
- **Domain guide confusion**: social-media diffusion, feed algorithms, macro expectations, or financial markets are routed here when their domain-specific guide is the better first load.
