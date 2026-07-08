# Law, Justice, Crime, And Legal Society Simulation Design

Use this guide for Society0 simulations about legal society, crime propensity, legal deterrence, legislation, adjudication, enforcement, litigation, legal aid, rights protection, regulatory evasion, and justice-system access. Load it after `founder-experience.md`, `research-design.md`, `environment-design.md`, `agent-design.md`, and `step-dsl.md`; also load `simulation-paper-distillation.md` when adapting a paper.

This guide is for legal social simulation, not legal advice, legal QA, or courtroom role-play alone.

## Evidence Map

| Source | Evidence status | Society0 lesson |
| --- | --- | --- |
| Wang et al., *Law in Silico: Simulating Legal Society with LLM-Based Agents*, ACL Findings 2026, https://aclanthology.org/2026.findings-acl.396/ | Supported by paper. Official GitHub page was found, but code was not available during this pass. | Model law as environment-hosted institutions: closed-world legal corpus, agent actions, case construction, adjudication, enforcement, periodic legislation, legal costs, and welfare/crime records. |

## Boundary Stance

Legal simulations are research sandboxes. They can explore mechanisms such as access to justice, deterrence, enforcement transparency, regulatory loopholes, and rights-protection dynamics. They cannot provide legal advice, predict real crime, recommend sentencing, assess individual risk, or justify enforcement policy without independent empirical, legal, ethical, and community validation.

Use a closed-world legal boundary when studying legal mechanisms. Agents should see the statutes and institutions that the simulated world actually enforces. Do not let unstated real-world law leak into the agent's decision unless the study explicitly models legal knowledge as a belief.

## Target Taxonomy

- **Crime and deterrence calibration**: agents face scenario-specific choices under different perceived punishment or enforcement conditions; compare aggregate tendencies to external benchmarks only with caveats.
- **Legal evolution**: agents create disputes; courts rule under current law; legislators periodically amend rules when gaps appear.
- **Rights protection and access to justice**: vulnerable agents choose between survival, litigation, legal aid, protest, exit, or inaction under cost and trust constraints.
- **Regulatory evasion and enforcement**: firms or power-holding agents adapt to rules; institutions detect, adjudicate, enforce, or fail.
- **Legal-system design rehearsal**: transparency, litigation cost, legal aid, corruption, adjudication bias, or punishment severity are treatment arms.

## Society0 Construction Rules

Use this split:

| Component | Society0 location |
| --- | --- |
| Legal corpus, court docket, enforcement rules, legal aid eligibility, costs, punishments, institutional transparency, corruption/bias condition | env state, env config, rules, or hidden run params |
| Agent demographics, socioeconomic condition, role, legal perception, trust in institutions, resources, welfare, prior cases | agent state/properties |
| Current law, visible events, personal harms, legal options, litigation cost, public rulings, enforcement outcomes | FoV |
| Commit act, comply, sue, seek legal aid, protest, strike, negotiate, report, appeal, pollute safely/unsafely, upgrade safety, compensate | `instruct` with typed env actions |
| Judge ruling, legislator amendment, enforcement action | separate institutional agent actions or deterministic rules, depending on study target |
| Crime tendency, action distribution, welfare, lawsuits, rulings, law changes, enforcement events, unmet legal need | output tables and metrics |

## Paper-Derived Patterns

### Separate Agent Action From Legal Settlement

Supported by paper:
Law in Silico separates agent decisions from the legal system. The environment interprets actions and consequences; the judicial module determines violations under current law; enforcement applies consequences; the legislative module periodically creates, modifies, or removes laws.

Inference for Society0 mapping:
Use a step loop like:

```text
observe harms and current law
-> affected and power-holding agents choose actions
-> env validates and records actions
-> cases are constructed from events
-> court adjudicates under current law
-> enforcement applies consequences
-> legislature reviews case summaries on schedule
-> laws and welfare records update
```

Do not end a legal round when an illegal or invalid action is attempted. Record it, classify it, and let the environment decide whether it creates liability, protest, enforcement, or no consequence.

### Closed-World Law Prevents Prompt Leakage

Supported by paper:
The paper instructs judicial agents to use only the simulated law, with principles such as no punishment without law, and includes closed-world prompts in the appendix.

Society0 mapping:
Keep current law in an env legal registry and render only the relevant law in FoV. Store any treatment label, corruption condition, or benchmark country label outside visible state unless the agent is meant to know it.

### Access Costs Are Mechanisms, Not Metadata

Supported by paper:
Law in Silico experiments vary litigation costs, legal aid, transparency, corruption, and trust. High costs can suppress legal recourse even when formal laws exist.

Society0 mapping:
Represent legal cost and eligibility as hosted constraints. An action such as `file_lawsuit` should fail or route to `seek_legal_aid` when resources are insufficient. Record the difference between harmed agents, agents who could file, agents who filed, and agents whose cases were heard.

## Dirty-Work Triage

Can do now:
- Build a closed-world legal micro-scenario with one power-holder role, several affected agents, a legal corpus, typed actions, docket records, and monthly law review.
- Add rule baselines for compliance, litigation-cost sensitivity, and enforcement severity.

Need user input:
- Whether the study is theoretical, historical, fictional, or tied to a jurisdiction; which harms and rights are in scope; and what legal expertise validates rule wording.

Optional external pipeline:
- Public crime statistics, victimization surveys, statutes, court data, or expert-coded case outcomes. Treat these as calibration/validation inputs, not hidden prompt truth.

Society0 scaffold impact:
- Use `plain` only for a small vignette. Use a custom env once law registry, docket, litigation costs, or enforcement state are reused across steps.

## Validation And Boundaries

- Always report underreporting, benchmark mismatch, and model bias risks for crime or compliance outputs.
- Use repeated seeds, model comparisons, prompt sensitivity, and rule-only baselines.
- Validate law text, case construction, and outcome interpretation with domain experts.
- Keep outputs non-operational: no real-person risk scoring, policing recommendations, evasion guidance, targeting, or legal advice.
