# Public Health, Risk, And Health Behavior Simulation Design

Use this guide for Society0 simulations about public-health behavior, vaccine hesitancy, risk communication, heatwaves, climate-health stressors, vulnerability, protective behavior, social support, and community information diffusion. Load it after `founder-experience.md`, `research-design.md`, `environment-design.md`, `agent-design.md`, and `step-dsl.md`; also load `simulation-paper-distillation.md` when adapting a paper.

This guide supports research design and mechanism exploration. It does not provide medical advice, public-health policy recommendations, diagnosis, treatment, or emergency guidance.

## Evidence Map

| Source | Evidence status | Society0 lesson |
| --- | --- | --- |
| Hou et al., *Can A Society of Generative Agents Simulate Human Behavior and Inform Public Health Policy? A Case Study on Vaccine Hesitancy*, arXiv:2503.09639, https://arxiv.org/abs/2503.09639 and https://github.com/abehou/VacSim | Supported by paper and official code/config README. | Public-health attitude simulation needs demographic grounding, social network exposure, news/policy interventions, warmup, attitude modulation, model suitability checks, and expert/human benchmark caveats. |
| Liu et al., *The Impact of Heatwaves on Population Health: A Large Language Model-Enhanced Agent-Based Simulation*, arXiv:2605.15918, https://arxiv.org/abs/2605.15918 | Supported by paper. Paper references example code path, but code/config was not deeply inspected. | Risk simulations should separate vulnerability strata, public warnings, daily stressor phases, protective actions, needs/emotional traces, communication networks, and external validation plans. |

## Domain Stance

Model public-health simulations as **risk environment and intervention systems**:

```text
health-risk environment -> public and local FoVs
-> LLM interpretation, concern, trust, protective choice, sharing, or support
-> hosted exposure, resource, attitude, need, and behavior updates
-> auditable health-behavior traces -> cautious mechanism interpretation
```

The environment owns risk phase, policy arm, warnings, news pool, social network, resource availability, vulnerability strata, intervention timing, and measurement schedule. LLM agents own situated interpretation: trust, fear, ambiguity, competing information, social influence, perceived efficacy, perceived barriers, and reasons for action or inaction.

## Target Taxonomy

- **Vaccine hesitancy and attitude change**: agents receive disease/vaccine news, peer messages, risk signals, and policy interventions; measure attitude distributions and hesitancy reduction.
- **Risk communication**: warnings, official notices, ambassadors, credible news, misinformation, or community reinforcement are varied as treatments.
- **Heatwave or climate-health stress**: environmental hazard phases affect safety, social needs, behavior, and protective routines by vulnerability group.
- **Community resilience**: social ties and cohesive networks affect information uptake, support seeking, and unmet need.

## Society0 Construction Rules

Use this split:

| Component | Society0 location |
| --- | --- |
| Risk phase, policy arm, news stance mix, warning schedule, disease or hazard intensity, resources, vulnerability index, social graph | env state, env config, rules, hidden properties |
| Demographics, risk perception, trust, attitude, needs, prior beliefs, resources, vulnerability group | agent state/properties |
| Official warning, local hazard status, peer messages, news items, policy offer, social support, prior personal experience | FoV |
| Read, discuss, share, seek help, help neighbor, adopt protective action, refuse, comply, delay, buy supplies, travel, stay home | `instruct` with typed env actions |
| Attitude rating, perceived risk, trust, reason, mental-health or need survey | `interview` or analysis step |
| Exposure delivery, attitude modulation, warmup gate, resource depletion, vulnerability effect, network metrics | rules or code steps |

## Paper-Derived Patterns

### Warmup And Model Suitability Are Part Of Public-Health Validity

Supported by paper and official code/config:
VacSim runs a warmup before applying policy, evaluates model suitability, tunes attitude modulation, compares policy strength, alters news stance, and uses qualitative checks for memory, conversations, and attitude consistency. The official README exposes warmup/run-day and evaluation modes.

Inference for Society0 mapping:
Use a separate baseline/warmup phase before treatment:

```text
initialize demographics and initial attitudes
-> warmup news/social exposure
-> check simulated baseline against benchmark
-> apply policy intervention
-> record attitude distribution and reasons each tick
```

Do not interpret policy effects if the model cannot reproduce baseline hesitancy or local consistency checks.

### Vulnerability Should Shape Exposure And Adaptive Capacity

Supported by paper:
The heatwave simulation assigns agents a Heat Vulnerability Index from demographic and social risk factors, runs baseline, heatwave, and recovery phases, and tracks needs, emotions, behaviors, and communication.

Society0 mapping:
Represent vulnerability as hidden or visible only as appropriate. It can affect FoV, resources, mobility, baseline needs, and action feasibility. Measure whether protective behavior is possible, not only whether the agent says it is concerned.

### Risk Diffusion Needs Network Records

Supported by paper:
VacSim models peer tweets and social-network exposure. The heatwave paper tests whether heatwave discussion adoption is predicted by friends' prior discussion and clustering, finding complex-contagion patterns in the simulated network.

Society0 mapping:
Record every risk message exposure:

```text
risk_exposure:
  agent_id
  tick
  source: official | news | peer | local_observation
  message_id
  stance_or_risk_frame
  adopted_discussion
```

Aggregate by vulnerability group, exposure source, and network position.

## Dirty-Work Triage

Can do now:
- Build a small public-health pilot with 20 or fewer agents, a warning schedule, peer messages, protective actions, and attitude/behavior tables.
- Add simple rule baselines for no social influence, no warning, uniform vulnerability, and no memory.

Need user input:
- Target population, health construct, acceptable intervention wording, benchmark dataset, IRB/ethics constraints, and whether sensitive group labels may be modeled.

Optional external pipeline:
- Public survey data, social media data, weather or mobility data, health outcomes, expert-coded news, and community validation.

Society0 scaffold impact:
- Use `plain` for the first risk message or attitude pilot. Use a custom env when hazard phase, resources, vulnerability, and network diffusion are central.

## Validation And Boundaries

- Treat simulations as mechanism probes, not public-health recommendations.
- Include baseline alignment, repeated seeds, model/provider comparison, policy/no-policy controls, and news-stance ablations.
- Validate with human, expert, survey, epidemiological, mobility, or social-media data before making external claims.
- Do not commit private health data, sensitive endpoint details, or run artifacts into public docs.
