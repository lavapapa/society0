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

Society0 mapping: high-stakes simulations require careful framing and limitation statements.

## Governance, Institutions, And Policy Practice

Governance-oriented LLM simulations model institutions such as legislatures, coalition negotiations, commons governance, social norms, moderation systems, elections, and policy labs. The useful lesson is to make permissions, aggregation rules, sanctions, appeals, and accountability records explicit instead of letting agents merely discuss politics.

Links:

- https://arxiv.org/abs/2406.18702
- https://arxiv.org/abs/2404.16698
- https://github.com/giorgiopiatti/GovSim
- https://github.com/sxswz213/CRSEC
- https://github.com/genglinliu/MOSAIC

Society0 mapping: implement the institution as env state, FoVs, typed actions, hosted constraints, and process/outcome records. Keep policy claims bounded by validation and participation requirements, and load `governance-institution-public-policy-simulation-design.md`.

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
