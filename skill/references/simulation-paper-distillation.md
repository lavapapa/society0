# Simulation Paper Distillation

Use this reference when the user asks to learn from papers, design a Society0 simulation from a research idea, reproduce or adapt a paper, or expand Society0 domain guides from LLM-based multi-agent social simulation research. The output should teach design method, not produce a loose literature summary or a paper-by-paper reproduction checklist.

Contents:

- Purpose
- Source evidence gate
- Design-transfer stance
- What to extract
- Dirty work triage
- What not to extract
- Society0 mapping
- Guide consolidation
- Output checklist
- Failure modes

## Purpose

Distill papers into operational knowledge for building Society0 simulations:

```text
paper evidence -> design method -> Society0 simulation scaffold -> domain guide
```

The target is not to summarize everything in a paper, and it is not mainly to write reproduction code. The target is to learn how researchers turn a research question, research hunch, or intuitive idea into a bottom-up LLM social simulation: world, agents, information, actions, rules, run loop, interventions, measurements, baselines, analysis, and interpretation. Preserve the parts needed to help a future Society0 user design their own simulation world.

## Source Evidence Gate

Do not create or update a definitive domain guide from abstracts, blog posts, snippets, citation lists, or partial reading.

Before distilling, gather and read the strongest available sources:

- Full paper text, preferably official PDF or official HTML.
- Appendix, supplementary material, prompt examples, tables, and figures when available.
- Official code, configuration files, data schemas, run scripts, prompts, and analysis scripts when available.
- Author project pages or official repositories only as supporting evidence.

Use search, abstracts, and keyword hits only to locate and navigate sources. They are not substitutes for reading the full paper. Read the entire paper, including limitations and appendix, before writing the guide. Use `pdftotext`, HTML text, or local copies when helpful, but still inspect the whole document rather than only `rg` hits.

If the full paper or crucial appendix/code is unavailable:

- Say exactly what is missing.
- Distill only the supported pieces.
- Mark missing or inferred items explicitly.
- Do not present a one-to-one reproduction blueprint as complete.

While reading, keep lightweight notes on where important mechanisms come from. Do not create a long source registry or source matrix unless the user asks for it. A paper can be identified by title, URL, DOI, or another stable citation. The guide only needs provenance when it affects design fidelity or prevents inference from being mistaken for paper content.

Use this compact note shape during reading:

```text
Supported by paper:
  ...
Supported by official code/config:
  ...
Supported by appendix/supplement:
  ...
Inference for Society0 mapping:
  ...
Unknown or unavailable:
  ...
```

Only promote an item into a guide when its source is clear or the text labels it as an inference.

## Design-Transfer Stance

Read `founder-experience.md` before promoting paper notes into a domain guide. Use it as the cross-domain guardrail for evidence boundaries, subject-layer selection, environment-hosted consequences, and avoiding schema-first or prompt-only worldbuilding.

Read a paper as evidence about how to construct a simulation paradigm, not as a fixed artifact to copy. Ask:

- What research question would make this simulation method necessary rather than a survey, archival regression, lab experiment, or traditional ABM?
- What part of the phenomenon is produced bottom-up by LLM agents, and what part is hosted by the environment?
- What did the authors make explicit that a naive designer would leave implicit?
- Which steps are "dirty work" that are not the headline contribution but are necessary for credible simulation, and who can actually do them?
- Which mechanisms transfer to a user's new Society0 study, and which are paper-specific?
- Where does Society0 need a custom env, FoV, action schema, or analysis table to preserve the method?

Distinguish these approaches:

- **Traditional ABM**: behavior is primarily hand-coded rules; language is usually not the medium of perception, meaning, or decision.
- **LLM-based ABM**: ABM structure with LLMs used for agent decisions; still risks treating prompts as drop-in rule replacements.
- **LLM multi-agent social simulation**: environment-hosted institutions and bounded FoVs make agents situated; LLMs interpret language, form beliefs, communicate, reason, and choose constrained actions; records make the simulation auditable.
- **Other empirical methods**: surveys, experiments, RCTs, and observational designs measure or identify effects directly; LLM simulations often use them as calibration, validation, treatment design, or scale-up anchors.

When helping a user, translate their idea into a Society0 scaffold. Do not merely say "paper X did Y." Say what design move the paper teaches and how that move should shape the user's setting, FoV, actions, hosted constraints, measurements, and validation.

## What To Extract

Extract the study as a simulation system, not as prose.

### Research Target

Record:

- discipline and subfield.
- simulation target, such as macro household economy, rumor diffusion, deliberation, institutional compliance, historical counterfactual, or financial market stress.
- research questions and claims the simulation is meant to support.
- target scale, population, time unit, and run length.
- empirical data, historical setting, scenario, or benchmark used for grounding.
- why the authors used LLM agents instead of a conventional model, survey, or archival study.
- what claim the simulation can support and what claim it cannot support.

### Environment

Identify the world the agents inhabit:

- institutions, platforms, markets, organizations, places, or historical settings.
- global state variables.
- hosted constraints: prices, budgets, inventory, rules, permissions, tax schedules, recommendation algorithms, geography, network topology, resource limits, law, norms, or event chronology.
- deterministic state transitions and stochastic processes.
- what belongs to the world rather than to the agent.

Ask: if the LLM agents were replaced with rule agents, what environment mechanics would still remain?

### Agents

Extract agent construction:

- roles and types.
- personas, demographic profiles, occupations, affiliations, ideology, preferences, expertise, or historical identities.
- visible mutable state.
- hidden researcher-only labels.
- memory/reflection/planning mechanisms.
- model/provider, prompt style, temperature, token limits, and output parser when given.
- how heterogeneity is introduced and whether it is sampled, data-grounded, prompt-authored, or rule-derived.

Separate actual paper details from a Society0 implementation choice. For example, "the paper uses bounded dialogue plus quarterly reflection" is different from "in Society0 also keep `memory=True` unless the study defines a no-memory condition."

### Perception And FoV

Extract what each agent can see at decision time:

- local observations.
- public signals.
- social context, feed items, partner messages, market prices, policy announcements, historical records, or prior outcomes.
- whether the agent sees aggregate indicators or only local information.
- whether the paper includes prompt examples.

Convert this into potential Society0 FoVs. Do not give agents global experiment labels unless the paper says they see them.

### Actions

Extract what agents can do:

- action names and arguments.
- whether action outputs are free text, structured JSON, tool calls, votes, trades, messages, movements, consumption choices, work choices, or policy choices.
- action bounds, grids, validation rules, fallback behavior, and invalid-output handling.
- whether an action is a semantic endpoint of a decision round.

In Society0, prefer environment actions for behavior. Use `interview` only for measurement.

### Run Loop

Extract the schedule:

- what one tick means.
- event order inside a tick.
- when agents observe, decide, interact, remember, reflect, or update.
- when the environment applies rules.
- when interventions occur.
- when outputs are logged.

Represent this as a Society0 `step(ctx)` loop or custom env lifecycle. Note exact order when the paper/code makes it important.

### Records And Analysis

Extract outputs and validation logic:

- raw interaction traces.
- state tables.
- macro, network, discourse, historical, or financial metrics.
- qualitative traces and explanations.
- baselines, ablations, sensitivity checks, repeated seeds, and comparison targets.
- statistical tests, plots, or regression models.
- limitations and claims the authors explicitly avoid.

Design Society0 records so the paper's metrics can be recomputed from tables, not only accepted from summary logs.

## Dirty Work Triage

Do not only extract the headline simulation loop. Many important design lessons live in non-core but hard parts that make the simulation usable. Treat them as first-class work items when they affect credibility, implementation, or interpretation, but triage them by their relationship to Society0 and to the user.

Look for:

- sample construction, quotas, demographic cells, persona compression, weights, and hidden sampling labels.
- treatment wording, placebo design, manipulation checks, attention controls, and random assignment.
- prompt variants, system prompts, output parsers, invalid-output handling, token limits, temperature, and model-selection routines.
- date restriction, leakage prevention, data-vintage control, and real-time information boundaries.
- calibration against human, survey, archival, market, historical, or expert benchmarks.
- bias correction, measurement-error models, post-stratification, bootstrap or uncertainty propagation.
- interpolation versus extrapolation rules for extending treatments or scenarios.
- qualitative coding, reasoning taxonomy, mental-model extraction, DAGs, and open-ended response analysis.
- contagion, network, market-clearing, balance-sheet, inventory, or accounting parameters that are not generated by the LLM.
- robustness checks, ablations, sensitivity sweeps, repeated seeds, and model/provider comparisons.
- operational boundaries: what needs fresh human validation, what needs a custom env, and what is only a Society0 extension.
- high-risk security or conflict boundaries: whether the design is limited to scenario rehearsal, mechanism exploration, robustness checks, historical interpretation, safety evaluation, or research planning; do not turn paper mechanisms into operational military advice, conflict prediction, targeting, evasion, or policy recommendation.

Use this triage:

| Dirty work type | Examples | What the agent should do |
| --- | --- | --- |
| Society0-native modeling | env rules, FoVs, action schemas, hidden `properties`, run loop, records, measurement tables | Design or implement it directly in the Society0 scaffold. |
| Agent-assistable preparation | public data lookup, table extraction, data cleaning, schema normalization, prompt/treatment drafting, persona generation from supplied distributions, analysis scripts | Offer to do it, create scripts or tables when useful, and report assumptions. |
| User/domain input required | private datasets, proprietary survey data, IRB/consent constraints, target population choice, construct definitions, valid treatment wording, acceptance criteria | Ask the user for the missing material or a decision; provide a minimal placeholder only if clearly labeled. |
| External data/infra | web crawling, API credentials, RAG corpus, geocoding, VLM assets, market data feeds, large batch model runs | Propose sources and pipeline; run only when access and permission are available; otherwise scaffold the ingest path. |
| Methodological/statistical | sampling weights, calibration targets, bias correction, bootstrap uncertainty, qualitative coding, human benchmark comparison | Implement or specify the analysis, but ask the user to confirm construct validity and benchmark choice. |
| Scale/cost operations | large persona populations, batching, rate limits, caching, repeated seeds, provider/model comparison | Start with a pilot, then plan scale-up with concurrency, cost, storage, and reproducibility controls. |

When a user asks for a simulation design, include a short dirty-work triage:

```text
Can do now:
  ...
Need user input:
  ...
Optional external pipeline:
  ...
Society0 scaffold impact:
  ...
```

In Society0, dirty work may map to env config, hidden `properties`, FoV construction, action schemas, step params, output tables, and analysis scripts. Some work is outside Society0 itself but still necessary before a credible Society0 run.

## What Not To Extract

Do not create a bloated document by copying every detail.

Usually omit or compress:

- general literature-review paragraphs.
- motivational prose that does not affect modeling.
- equations that are not used in the reproduced mechanism.
- implementation dependencies unrelated to simulation semantics.
- every numerical result when the pattern only needs metric definitions and expected qualitative comparisons.
- unrelated future-work speculation.

Keep details when they change reproduction fidelity:

- parameters, schedules, action bounds, randomization, prompts, model settings, data grounding, and metric formulas.
- appendix or code details that contradict a simplified reading of the main text.
- limitations that affect how Society0 outputs should be interpreted.

The guide should answer: "How would I translate a user's research question into this class of Society0 simulation, what design moves does the paper teach, and which dirty-work items should be done by Society0, by the agent, by external tooling, or by the user?"

## Society0 Mapping

Map each paper into Society0's env-first scaffold:

```text
Setting -> FoVs -> Actions -> Hosted constraints -> Records -> Measurements
```

Use a compact design-transfer matrix when fidelity matters:

```text
Design element:
  ...
Source:
  paper / appendix / code / inference
Society0 mapping:
  env state / FoV / action / rule / behavior / step / output table
Fidelity:
  exact / approximate / extension / unsupported
Notes:
  ...
```

Prefer these mappings:

- Environment and institutions -> custom env or env rules.
- Per-agent observations -> FoVs.
- Agent behavior -> `instruct` with env actions and terminal/completion semantics.
- Surveys, judgments, and post-hoc explanations -> `interview`.
- Deterministic mechanisms -> rules or behaviors.
- Hidden treatment labels -> `properties`, step params, or output tables, not visible `state`.
- Metrics -> `ctx.result(metrics=..., tables=...)` plus analysis scripts.

Do not weaken Society0's core semantics for convenience. Do not bypass `World.instruct_agent()` / `AgentGroup.instruct()` for action-bearing LLM behavior. Do not silently disable memory or action loops because the paper's run is expensive. Do not collapse careful survey, calibration, or analysis work into a single prompt if that work is what makes the study credible.

## Guide Consolidation

Do not create one reference file per paper by default. Consolidate by discipline and simulation target.

Use or create domain guides like:

```text
economics-finance-simulation-design.md
history-simulation-design.md
communication-simulation-design.md
governance-institution-simulation-design.md
```

Within a domain guide, group papers by target when useful:

```text
Economics and finance
  - Macro household economies
  - Consumer markets
  - Financial markets
  - Banking and crisis simulation
  - Policy and taxation
```

Merge a new paper into an existing domain guide when it shares a discipline or target with existing material. Add a separate file only when:

- the discipline has no guide yet.
- the existing guide would become confusing because the target is genuinely different.
- the new material establishes a reusable method family large enough to justify separate loading.

When merging:

- Add the paper as a case study or reproduction pattern.
- Update the domain's general design rules only when multiple papers support the same rule.
- Keep disagreements visible instead of forcing false consensus.
- Distinguish "paper-specific recipe" from "domain-level principle."

Create a general cross-domain guide only after several domain guides exist. Derive it from observed commonalities across domains, not from prior beliefs about social simulation.

## Output Checklist

Before finishing a distillation update, verify:

- The full paper was read, or missing parts are explicitly disclosed.
- Appendix/supplement/code/config were checked when available.
- Every design-critical claim has a source or is labeled as Society0 inference.
- The guide is organized by domain or simulation target, not by an isolated paper unless justified.
- The text is concise enough for future agents to load and use.
- The Society0 mapping respects env-first design, FoVs, actions, rules, memory, and run artifacts.
- The guide triages non-headline dirty work that affects sample construction, treatments, calibration, validation, analysis, or interpretation.
- The guide teaches how to transform a user's research question into a simulation scaffold, not only how to copy a paper.
- The guide includes baselines, ablations, validation checks, and limits of interpretation.
- The main `SKILL.md` links to any new reference file.
- `quick_validate.py` passes for the skill folder.

## Failure Modes

Avoid these failures:

- **Abstract-only distillation**: producing confident design rules after reading only the abstract or introduction.
- **Search-hit distillation**: using `rg` matches as if they were a full reading.
- **Brain-fill**: filling missing methods with plausible ABM or LLM-agent assumptions.
- **Paper-per-file sprawl**: creating many narrow references that future agents will not know how to select.
- **Summary instead of method**: describing claims and results without extracting environment, FoV, action, loop, and measurement design.
- **Reproduction tunnel vision**: treating the goal as copying a paper rather than learning design moves for new Society0 simulations.
- **Dirty-work loss**: preserving the headline agent loop while dropping sample construction, treatment design, validation, calibration, parsing, or analysis machinery.
- **Dirty-work dumping**: listing difficult prerequisites without saying whether the agent can do them, Society0 should host them, external tooling is needed, or the user must decide/provide data.
- **Prompt-only worldbuilding**: turning institutional or market mechanics into prose prompts instead of env state and rules.
- **Metric opacity**: recording final metrics without enough tables to recompute them.
- **Overgeneralization**: promoting a single paper's design choice into a domain principle.
- **Unmarked inference**: presenting a Society0 implementation decision as if the original paper specified it.
- **Schema-first compression**: turning semantic information into a large event taxonomy before proving which fields the environment must enforce or the study must measure.
