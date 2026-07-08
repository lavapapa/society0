# Domain Distillation Coverage Audit

Use this audit as a short maintenance map for omitted discipline-specific LLM social simulation papers. It is not a citation list. Promote a paper into a guide only after checking the full paper and, where available, official code or configs.

Evidence labels:

- **Supported by paper**: full official paper/PDF/HTML was inspected.
- **Supported by official code/config**: official repository, README, configs, or scripts were checked.
- **Inference for Society0 mapping**: Society0 env/FoV/action translation, not necessarily the paper's implementation.
- **Unknown or unavailable**: source, code, appendix, data, or reproduction-critical detail was not available.

## Accepted New Domain Guides

| Cluster | Accepted sources | Rationale |
| --- | --- | --- |
| Education, classroom, and learning | AgentSchool: An LLM-Powered Multi-Agent Simulation for Education, arXiv:2605.30144, https://arxiv.org/abs/2605.30144 | Supported by paper. Domain-specific learning mechanisms are explicit: student knowledge graphs, misconceptions, teacher scaffolding, lesson/recess scenes, social networks, and longitudinal records. Official code URL appears in paper, but code details were not verified during this pass. |
| Law, justice, and legal society | Law in Silico: Simulating Legal Society with LLM-Based Agents, ACL Findings 2026, https://aclanthology.org/2026.findings-acl.396/ | Supported by paper. Domain-specific legal mechanisms are explicit: legislation, adjudication, enforcement, closed-world law, litigation costs, legal aid, rights-protection records, crime calibration, and micro-to-macro deployment. Official repo was found but states code is coming soon. |
| Public health, risk, and health behavior | VacSim, arXiv:2503.09639, https://arxiv.org/abs/2503.09639 and https://github.com/abehou/VacSim; Heatwave population health simulation, arXiv:2605.15918, https://arxiv.org/abs/2605.15918 | VacSim is supported by paper and official code/config README. Heatwave is supported by paper; paper points to example code under an AgentSociety repository, but code was not deeply inspected. Both are health-risk behavior simulations with public-health interventions, vulnerability strata, social exposure, and validation caveats. |
| Consumer, marketing, and agentic markets | LLM-Based Multi-Agent System for Simulating and Analyzing Marketing and Consumer Behavior, arXiv:2510.18155, https://arxiv.org/abs/2510.18155 and official repo; Magentic Marketplace, Microsoft Research PDF and https://github.com/microsoft/multi-agent-marketplace | Supported by paper and official code/config. The guide is justified as a market/consumer behavior surface, not as a generic marketplace framework. The marketing-town paper is a narrow discount case; Magentic Marketplace gives stronger reusable market-design mechanics. |

## Routed To Existing Guides

| Candidate | Route | Rationale |
| --- | --- | --- |
| POSIM: A Multi-Agent Simulation Framework for Social Media Public Opinion Evolution and Governance, arXiv:2603.23884 and https://github.com/DeepCogLab/posim/ | `communication-social-media-simulation-design.md`; governance guide only for intervention/governance layer | Supported by paper and official repository. It is social-media public opinion evolution with platform, recommendation, Hawkes activation, Social-BDI agents, and governance interventions, so it belongs in communication rather than a new public-opinion guide. |
| LLM-Agent-based Social Simulation for Attitude Diffusion, arXiv:2604.03898 | `communication-social-media-simulation-design.md` | Supported by paper. Domain target is immigration attitude diffusion after a real event, implemented through news retrieval, small-world exposure, generated posts, and attitude update; it extends existing opinion/attitude dynamics coverage. |
| Modeling U.S. Attitudes Toward China via an Event-Steered Multi-Agent Simulator, arXiv:2606.06971 | `communication-social-media-simulation-design.md`; IR/security guide only for high-risk boundary language if a user asks about geopolitical implications | Supported by paper. The mechanism is event/news-steered public attitude dynamics; keep it as opinion simulation, not geopolitical prediction or policy guidance. |
| ProSim / Investigating Prosocial Behavior Theory in LLM Agents Under Policy-Induced Inequities, arXiv:2505.15857 and https://github.com/halsayxi/ProSim/ | `governance-institution-public-policy-simulation-design.md` | Supported by paper and official code README. It studies prosocial norms under policy interventions and inequity; route to governance/public goods/norms rather than a new prosocial guide. |
| Cultural Evolution of Cooperation among LLM Agents, arXiv:2412.10270 and https://github.com/aronvallinder/llm-donor-game | `governance-institution-public-policy-simulation-design.md` | Supported by paper and official code README. It is a game-theoretic cooperation/norm probe; use as a compact commons/public-good pattern, not a standalone domain. |

## Omitted, Duplicate, Or Generic

| Candidate type | Decision | Rationale |
| --- | --- | --- |
| Generative Agents, Concordia, AgentSociety, YuLan-OneSim/S-Researcher-style platforms, generic agent frameworks | Omit as new distillation targets | Already covered or intentionally excluded by scope. They may remain orientation examples, but they should not drive new domain-specific guides. |
| Generic LLM-agent surveys and awesome lists | Omit | Useful for discovery only. Candidate lists are not evidence and do not expose enough domain mechanics for Society0 guide content. |
| Legal consultation, courtroom-only, or legal QA agents without society-level interaction | Omit from legal society guide unless later verified as social simulation | They may be legal-agent systems, but the current task is LLM social simulation with env-hosted institutions and interaction records. |
| Education review papers without classroom/social simulation mechanics | Omit | AgentSchool supplies the accepted domain pattern; broad education-agent reviews do not justify guide rules by themselves. |

## Evidence Gaps To Recheck Later

- AgentSchool: official code/config was not verified beyond the URL stated in the paper.
- Law in Silico: official GitHub page exists but code was not available during this pass.
- Heatwave population health: paper references open-source definitions under an AgentSociety example path; code/config was not inspected enough for exact reproduction claims.
- POSIM: official repo states full datasets and some system components will be released gradually after review and anonymization; exact reproduction should re-check data availability.
- Attitude diffusion and ES-MAS: paper mechanisms were inspected, but official package/dataset/code availability should be rechecked before exact reproduction.
