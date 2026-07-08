# Field Examples

Use these projects as orientation, not templates to copy wholesale.

## Generative Agents

Park et al., "Generative Agents: Interactive Simulacra of Human Behavior" introduced a Smallville-style sandbox where LLM agents perceive, remember, reflect, plan, and interact.

Link: https://arxiv.org/abs/2304.03442

Society0 mapping: memory + FoVs + `instruct` loops + step-level outputs.

## AI Town

AI Town is an open-source demo inspired by generative agents, showing AI characters in a virtual town.

Link: https://github.com/a16z-infra/ai-town

Society0 mapping: useful for onboarding imagination, but Society0 should stay research-first rather than game-first.

## Concordia

Concordia is a Google DeepMind library for generative social simulation.

Link: https://github.com/google-deepmind/concordia

Society0 mapping: compare scenes/components to environments/code steps.

## SOTOPIA

SOTOPIA evaluates social intelligence in language agents through social interaction tasks.

Link: https://arxiv.org/abs/2310.11667

Society0 mapping: use as inspiration for social goals, interaction protocols, and evaluation tables.

## WarAgent

WarAgent applies LLM multi-agent simulation to international conflict scenarios.

Link: https://arxiv.org/abs/2311.17227

Society0 mapping: model historical diplomacy as env-hosted country profiles, relationship boards, fixed actions, messages, treaty/war records, and graph metrics. High-stakes simulations require non-operational framing and limitation statements. Load `international-relations-conflict-security-simulation-design.md`.

## EscalAItion And Crisis Escalation

EscalAItion studies escalation risks from LLM agents in military and diplomatic decision-making with fixed nation actions, dynamic variables, world-model consequence summaries, action severity, and escalation scoring.

Links:

- https://dl.acm.org/doi/10.1145/3630106.3658942
- https://github.com/jprivera44/EscalAItion

Society0 mapping: use a custom crisis env with fixed action schemas, public/private communication, validated targets, severity labels, prompt/temperature/model sensitivity checks, and explicit deployment caution. Use for mechanism exploration and safety evaluation only.

## Strategic Games And Security Dilemmas

Recent LLM strategic-game work treats models as experimental subjects in repeated security dilemmas, varying polarity, finite horizons, and communication.

Link: https://arxiv.org/abs/2605.03604

Society0 mapping: start from a minimal game env with `attack`, `do_nothing`, optional public messages, terminal conflict, and repeated-seed robustness. Treat results as theory-probing artifacts, not state-behavior predictions.

## Historical Battle Emulation

BattleAgent uses a spatial sandbox, quantized time, map observations, dynamic agent structures, and historical-record comparison to emulate past battles.

Links:

- https://arxiv.org/abs/2404.15532
- https://github.com/agiresearch/BattleAgent

Society0 mapping: use only for historical emulation, pedagogy, and sandbox mechanism evidence. Keep public examples non-tactical and route modern conflict or safety-evaluation requests to the IR/security guide boundaries.

## Governance, Institutions, And Policy Practice

Governance-oriented LLM simulations model institutions such as legislatures, coalition negotiations, commons governance, social norms, moderation systems, elections, and policy labs. The useful lesson is to make permissions, aggregation rules, sanctions, appeals, and accountability records explicit instead of letting agents merely discuss politics.

Links:

- https://arxiv.org/abs/2406.18702
- https://arxiv.org/abs/2404.16698
- https://github.com/giorgiopiatti/GovSim
- https://github.com/sxswz213/CRSEC
- https://github.com/genglinliu/MOSAIC

Society0 mapping: implement the institution as env state, FoVs, typed actions, hosted constraints, and process/outcome records. Keep policy claims bounded by validation and participation requirements, and load `governance-institution-public-policy-simulation-design.md`.

## Education And Classroom Learning

AgentSchool models education as a state-transition simulation with student knowledge graphs, misconceptions, teacher scaffolding, classroom scenes, and informal peer dynamics.

Link: https://arxiv.org/abs/2605.30144

Society0 mapping: host curriculum, lesson scene, peer graph, assessment, student learning state, teacher actions, and misconception records in the environment. Load `education-learning-classroom-simulation-design.md`.

## Legal Society

Law in Silico simulates legal society with individual decisions and legal institutions for legislation, adjudication, and enforcement.

Link: https://aclanthology.org/2026.findings-acl.396/

Society0 mapping: use a closed-world law registry, typed legal/social actions, docket records, adjudication/enforcement steps, periodic law updates, legal-cost constraints, and non-operational boundaries. Load `law-justice-crime-simulation-design.md`.

## Public Health And Risk Behavior

VacSim studies vaccine hesitancy with demographic agents, social-network exposure, vaccine news, warmup, attitude modulation, and policy interventions. Recent heatwave work models vulnerability, warnings, protective behavior, psychosocial needs, and risk information diffusion.

Links:

- https://arxiv.org/abs/2503.09639
- https://github.com/abehou/VacSim
- https://arxiv.org/abs/2605.15918

Society0 mapping: host risk phase, warning/news exposure, social diffusion, intervention arm, vulnerability strata, protective actions, and validation records. Load `public-health-simulation-design.md`.

## Consumer Markets And Agentic Marketplaces

Consumer/marketing simulations test promotions, purchase behavior, word-of-mouth, and transaction records. Magentic Marketplace studies buyer/seller AI agents, search, messages, proposals, payments, welfare, manipulation, and market-design effects.

Links:

- https://arxiv.org/abs/2510.18155
- https://github.com/carolchu1208/LLM-Based-Generative-Agents-Simulating-Consumer-Decisions
- https://www.microsoft.com/en-us/research/publication/magentic-marketplace-an-open-source-environment-for-studying-agentic-markets/
- https://github.com/microsoft/multi-agent-marketplace

Society0 mapping: host catalogs, prices, discounts, budgets, search results, proposals, payments, transaction validation, welfare metrics, and manipulation arms. Load `consumer-marketing-marketplace-simulation-design.md`.

## OASIS

OASIS focuses on large-scale social media simulation with many LLM agents.

Links:

- https://arxiv.org/abs/2411.11581
- https://github.com/camel-ai/oasis

Society0 mapping: social feed exposure, recommender mechanisms, and scaling constraints. For paper-derived design rules, load `communication-social-media-simulation-design.md`.

## Communication-Oriented LLM Simulation

Use communication studies on LLM-agent news diffusion, rumor spread, echo chambers, and generative exaggeration as design inspiration. The useful lesson is not to copy a platform wholesale, but to isolate the mechanism: source credibility, network exposure, recommendation bias, social endorsement, memory, or rhetorical transformation.

Society0 mapping: implement the mechanism as FoVs, actions, hosted constraints, and metrics. Keep claims modest because LLM agents can amplify prompt/provider artifacts. For social media, news diffusion, rumor/fake-news, polarization, platform intervention, social movement, group-agent, or hybrid-scale designs, load `communication-social-media-simulation-design.md`.

## Generative Agent Simulations Of 1,000 People

The StanfordHCI generative-agent work grounds individual agents in self-reports and long qualitative interviews, then evaluates held-out survey, personality, economic-game, and scenario responses.

Links:

- https://arxiv.org/abs/2411.10109
- https://github.com/StanfordHCI/genagents

Society0 mapping: treat this as interview-grounded respondent simulation, not a generic agent architecture. Use `interview` for held-out measures, keep memory when grounding depends on it, and load `interview-survey-deliberation-simulation-design.md`.

## Silicon Samples And Turing Experiments

Out of One, Many uses socio-demographic backstories to simulate population and subgroup distributions, while Turing Experiments test whether LLM samples replicate known human-subject experiments or reveal model distortions.

Links:

- https://doi.org/10.1017/pan.2023.2
- https://doi.org/10.7910/DVN/JPV20K
- https://proceedings.mlr.press/v202/aher23a.html
- https://github.com/microsoft/turing-experiments

Society0 mapping: silicon samples are distributional, not individual twins. Turing Experiments need prompt validity checks before outcome testing. Load `interview-survey-deliberation-simulation-design.md`.

## Scenario Replication And Human-Replacement Boundaries

Large scenario/vignette replications can be useful for pilot work and method stress tests, but cautionary psychology papers argue against treating LLM outputs as human participant replacements without validation.

Links:

- https://arxiv.org/abs/2409.00128
- https://osf.io/j6wmn/
- https://arxiv.org/abs/2402.04470
- https://arxiv.org/abs/2508.06950

Society0 mapping: use scenario experiments only when the stimulus and measurement can be cleanly represented. Exclude or heavily qualify lived-experience, physical-behavior, longitudinal, and real team-interaction studies. Load `interview-survey-deliberation-simulation-design.md`.

## Deliberation Systems

Plurals structures simulated social ensembles with agents, deliberation structures, and moderators. The Habermas Machine separates opinion gathering, candidate statement generation, personalized ranking, social choice, critique, and revision.

Links:

- https://arxiv.org/abs/2409.17213
- https://github.com/josh-ashkinaze/plurals
- https://www.science.org/doi/10.1126/science.adq2852
- https://github.com/google-deepmind/habermas_machine

Society0 mapping: implement deliberation as hosted protocol state, visible FoVs, typed actions, ballots/rankings, critique records, and explicit aggregation rules. Load `interview-survey-deliberation-simulation-design.md`.

## AgentSociety

AgentSociety is an LLM-native social simulation platform aimed at social science research.

Links:

- https://arxiv.org/abs/2502.08691
- https://github.com/tsinghua-fib-lab/agentsociety/

Society0 mapping: compare intervention, survey, and large-population research workflows.

## Humanoid Agents

Humanoid Agents explores human-like generative agents with needs, emotions, and relationship state.

Link: https://arxiv.org/abs/2310.05418

Society0 mapping: encode needs/emotions as agent state only when they are part of the research design.
