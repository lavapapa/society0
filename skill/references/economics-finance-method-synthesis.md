# Economics And Finance Method Synthesis

Use this guide when designing a new Society0 economics/finance simulation, translating a research idea into an env-first scaffold, checking reproduction fidelity, or synthesizing methods across macro, survey, and financial-market targets.

Contents:

- Purpose
- Cross-target principles
- From research idea to simulation scaffold
- Society0 design checklist
- Design transfer and dirty work triage
- Source support notes
- Calibration and sample construction
- FoV and temporal information control
- Actions versus measurement
- Memory, reflection, and reasoning
- Baselines and ablations
- Validation and analysis
- Failure modes

## Purpose

This is not a literature review and not mainly a reproduction manual. It distills cross-paper methods into reusable Society0 design rules: how to turn a research question, hypothesis, or intuitive idea into a simulated economic world with actors, institutions, FoVs, actions, measurements, and validation. Promote a rule here only when it is supported by multiple targets or is a direct consequence of Society0's env-first architecture.

Use `founder-experience.md` as the cross-domain preflight before applying this economics/finance guide. The economics guide tells you what domain institutions often matter; the founder notes tell you how to avoid over-agentifying objects, over-structuring semantic information, or letting macro conclusions leak into micro-agent design.

## Cross-Target Principles

### Host Institutions In The Environment

Across EconAgent, SimCity, ASFM, EconGym, and bank-run designs, institutions are not prose. They are hosted by the environment:

- macro accounting, tax, welfare, price, wage, and interest rules.
- goods, labor, housing, and financial markets.
- exchange order books, matching, settlement, and portfolios.
- bank communication, liquidity, withdrawal settlement, and contagion.
- survey dates, treatment assignment, and historical information boundaries.

LLM agents interpret and decide inside these institutions.

### Bound FoV Like An Experimental Treatment

FoV is not a convenience prompt. It is the information treatment. Papers in expectations and finance repeatedly show that information exposure, date restriction, prior forecasts, social media/news, and prompt wording change outcomes.

Design each FoV as:

```text
visible profile + visible local state + visible public/context signals
+ exact task/question/action affordances
- hidden labels, future facts, researcher metadata, and global state not visible in the paper
```

For broad economic news, policy language, forecasts, or market narratives, preserve semantic text in FoVs when the mechanism depends on interpretation. Add numeric updates only when the environment must enforce a quantity, price, date, limit, account, or constraint.

### Separate Behavior From Measurement

Use `instruct` with env actions when the agent changes the world:

- work, consume, save, borrow, invest, trade, withdraw, hire, set prices, choose policy.

Use `interview` when the agent is measured:

- expectations, forecasts, beliefs, reasons, confidence, trust, mental models.

Some studies have both. For example, a bank-run survey may first measure withdrawal intent with `interview`, then an action-mode extension may let agents actually withdraw deposits.

### Recapitulate Before Claiming Replication

The literature often uses **recapitulate** because LLM agents may match qualitative or distributional patterns without replicating individual human responses. Use "replication" only when the design, sample, protocol, analysis, and statistical claims match the original standard.

### Record Enough To Recompute

Every guide should require tables that can recompute the paper's metrics. Summary logs are not enough.

Examples:

- macro: household rows -> tax revenue, consumption, GDP, unemployment.
- survey: persona rows + treatment rows + responses -> ATE, scaled slopes, distribution similarity.
- market: orders/trades/positions -> execution rate, turnover, volatility, returns.
- bank run: intent/action + network + liquidity -> message effects and contagion.

### Triage Dirty Work

Across expectation surveys, Bank Run, ASFM, SimCity, and EconGym, the most time-consuming design work is often not the headline agent loop. It is the machinery that makes the simulation interpretable:

- sample cells, quotas, population weights, and persona compression.
- validated treatment text, placebo or attention controls, random assignment, and manipulation boundaries.
- model selection, prompt settings, structured outputs, invalid-output handling, and provider comparisons.
- bias correction, calibration, bootstrap uncertainty, and human or empirical anchors.
- interpolation/extrapolation rules for extending scenarios.
- qualitative coding of explanations, mental-model analysis, and mismatch diagnostics.
- deterministic networks, markets, accounting, contagion, and state-transition parameters.

Do not merely preserve these items as caveats. Triage them:

| Relationship to Society0 | Economics/finance examples | Agent behavior |
| --- | --- | --- |
| Society0-native | tax rules, market clearing, bank contagion, treatment assignment, FoV boundaries, action validation, output tables | Put in env state/rules, FoVs, actions, `properties`, steps, and records. |
| Agent can prepare | clean ACS/SCE/SPF tables, normalize demographic bins, draft treatment variants, generate persona table from user-approved distributions, write calibration/analysis scripts | Do the work when sources and permissions are available; state assumptions and outputs. |
| User must provide or approve | proprietary survey microdata, target population, sensitive attributes, human-subject constraints, construct definitions, policy message acceptability | Ask for the data/decision; offer a minimal pilot with synthetic placeholders only if labeled. |
| External pipeline | data crawlers, RAG corpora, market feeds, social-media snapshots, geocoding, VLM placement, large batch model jobs | Propose or scaffold the pipeline; run it only with available credentials and acceptable data-use terms. |
| Statistical/methodological | post-stratification, model-human bias correction, bootstrap, treatment support, interpolation/extrapolation boundary, qualitative coding taxonomy | Implement where possible; ask the user to validate construct choices and benchmark relevance. |

A run with good prompts but none of this machinery is usually a demo, not a research simulation.

## From Research Idea To Simulation Scaffold

Use this translation pattern with users:

```text
research concern -> construct -> simulated institution -> actor roles
-> bounded FoV -> action or interview -> env/rule consequence
-> record -> validation target -> interpretation boundary
```

Examples:

- "Will a rumor cause withdrawals?" becomes a bank-run environment with panic exposure, communication treatments, hidden assignment, withdrawal-intent interviews or withdrawal actions, liquidity/contagion rules, and human/calibrated benchmarks.
- "Will tax policy change labor supply?" becomes a household macro environment with wage, price, tax, redistribution, work/consumption actions, accounting rules, and macro/stylized-fact validation.
- "How do investors respond to policy news?" becomes an exchange environment with listed firms, price/order histories, news FoVs, buy/sell/hold actions, matching/settlement rules, and order/trade metrics.

Ask which part of the user's phenomenon requires LLM interpretation. If the answer is only numerical transition, use rules. If the answer involves belief, language, attention, trust, persuasion, identity, expertise, or meaning under bounded information, use LLM agents inside a constrained environment.

## Society0 Design Checklist

Before coding a new economics/finance simulation, write this scaffold:

```text
Research target:
  ...

Construct and hypothesis:
  what latent belief, behavior, market response, or institutional outcome is being studied.

Setting:
  ...

Agents:
  roles, profiles, mutable visible state, hidden properties.

FoVs:
  what each role sees at each decision or interview.

Actions:
  action name, arguments, bounds, validation, fallback.

Hosted constraints:
  accounting, market rules, survey assignment, time, policies, network, shocks.

Dirty-work triage:
  society0_native:
    env/rules/FoV/actions/records that must be built into the simulation.
  agent_can_do:
    data cleaning, public-source research, persona-table generation, scripts, coding drafts.
  user_input_needed:
    datasets, domain definitions, ethics/access decisions, benchmark choice.
  external_pipeline:
    crawler/API/RAG/model-batch work outside the core Society0 run.

Run loop:
  tick meaning, stage order, LLM rounds, env rules, interventions.

Records:
  raw outputs, validated actions, state tables, metrics, config.

Measurements:
  metrics, tests, qualitative analysis, human/benchmark comparison.

Baselines:
  rule, no-profile, no-observation, no-memory, no-treatment, alternate model.

Fidelity:
  exact / approximate / extension / unsupported by paper element.
```

Do not proceed to full runs until the scaffold makes hidden vs visible information explicit.

## Design Transfer And Dirty Work Triage

When borrowing from papers, extract design moves rather than only recipes:

- How did the paper define the simulated world so agents could act meaningfully?
- Which hidden conditions and measurement protocols made the output interpretable?
- Which non-core engineering or methodology steps took the burden off the prompt?
- Which parts should be exact in Society0, and which become approximate or Society0-specific extensions?
- What would be invalid if copied into a user's different domain without fresh calibration?
- Which steps can the agent perform now, which require external data or tools, and which require user judgment?

Do not summarize a paper's claim and stop. Turn the method into a reusable Society0 pattern.

## Source Support Notes

For each paper-derived design, maintain only enough source support to prevent overclaiming. Do not create a separate source registry unless the user asks for it. One stable citation per paper is enough for the guide; add detail only when it affects implementation or fidelity:

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

When writing the final guide, do not include every reading note; include only the parts that affect design, reproduction, or interpretation. Keep source limitations visible.

Fidelity labels:

- **exact**: paper/code gives enough detail and Society0 can implement it directly.
- **approximate**: mechanism is clear but implementation differs.
- **extension**: useful Society0 design beyond the source.
- **unsupported**: source does not specify enough or material was unavailable.

## Calibration And Sample Construction

Use real data where the target depends on human distribution:

- SCE or similar microdata for household expectations.
- SPF public acknowledgments and real-time data for professional forecasters.
- ACS/IPUMS/Census-style data for household income, age, and geography.
- market sector distributions for simulated companies.
- empirical investor-type proportions when studying trader mix.
- survey or human benchmark data for withdrawal intent and message effects.

Store:

- sample source.
- sampling frame and filters.
- weights or strata.
- generated synthetic profile source.
- hidden sample cell in `properties`.
- visible persona text in `state` or FoV.

Do not hand-author stereotypes when a paper grounds profiles in survey microdata or public profiles.

When a paper uses human data as a calibration anchor, keep that anchor visible in the scaffold. For example, Bank Run uses a small human validation survey, model-human bias correction, post-stratification, and bootstrapped uncertainty before scaling synthetic responses. Those steps are not optional polish; they are part of the simulation method.

Practical handling:

- If the user has data, inspect schema, clean bins, document transformations, and build `persona_table` or calibration tables.
- If the data is public, offer to locate, download, clean, and cite it when network/data-use constraints allow.
- If the data is private, sensitive, or requires approval, ask the user to provide a de-identified extract or authorize a synthetic pilot.
- For large persona populations, build them as a table first; sample into Society0 runs from that table rather than hand-writing personas in prompts.
- For crawler-derived context, separate the crawler output from the Society0 run: source table -> cleaning/filtering -> FoV snippets or RAG corpus -> leakage/data-use audit.

## FoV And Temporal Information Control

Use date and information controls aggressively:

- For historical surveys, add date-restriction text to the FoV and prevent RAG/memory from retrieving future facts.
- For professional forecasts, use real-time data vintages, not revised data.
- For news-to-belief generation, include only articles published within the period.
- For market simulations, expose order book and price histories only according to the market design.
- For SimCity/EconAgent, expose prior-period variables when the paper says agents only access previous-step information.

Record a leakage audit table when the study depends on time:

```text
agent_id, wave_id, survey_date, max_allowed_info_date,
retrieval_enabled, retrieved_doc_max_date, violation_flag
```

## Actions Versus Measurement

Action design rules:

- Give each action a narrow economic meaning and typed arguments.
- Validate budget, holdings, inventory, bounds, and action limits.
- Store raw and validated arguments.
- Count invalid outputs.
- Use fallback behavior only when the paper specifies or the run config declares it.

Survey measurement rules:

- Preserve original questionnaire wording when reproducing.
- Use structured outputs for numeric answers and open-ended text.
- Record the exact prompt/FoV version.
- Keep prior and posterior responses separate.
- Use `memory=False` unless the study explicitly uses same-agent history or follow-up.
- Keep survey-intent measurements separate from world-changing actions. A withdrawal-intent interview is not the same as a bank-balance-sheet withdrawal unless an action mode and liquidity rule are added.

## Memory, Reflection, And Reasoning

Do not add memory because it sounds agentic. Add it only when it maps to a mechanism:

- EconAgent: explicit quarterly reflection should be reproduced as a visible reflection channel.
- Generative Agents: memory stream, retrieval, reflection, and planning are relevant when simulating ongoing social life or long-horizon behavior.
- Macro expectation agents: open-ended reasoning is a measured output; it should not automatically become memory unless the panel design requires it.
- LLM Survey Framework: dynamic follow-ups can use fixed persona and prior responses, but date restriction must still prevent future leakage.

When using memory:

- record retrieved memories.
- distinguish observations, reflections, and plans.
- test a no-memory/no-reflection ablation if memory is a claimed mechanism.

## Baselines And Ablations

Cross-target baseline menu:

- rule-based economic mechanics.
- economic-method policy rules: Taylor rule, Saez tax, IMF-like rules.
- real-data replay.
- behavior cloning where empirical microdata exist.
- no persona / generic persona.
- naive persona only.
- no prior expectations.
- no real-time data.
- no observation/order book/news.
- no social media/RAG/KAM/SMIM.
- no date restriction.
- no reflection/memory.
- homogeneous agent mix.
- alternate model/provider.
- temperature/top-p perturbation.
- prompt wording/translation/adversarial variations.
- calibration removed versus calibration retained.
- interpolation-only variants versus extrapolative variants requiring fresh validation.

Use ablations to answer: which component produces distribution fit, qualitative reasoning, market behavior, or macro emergence?

## Validation And Analysis

Match validation to target:

| Target | Validation |
| --- | --- |
| Household macro | accounting identities, macro indicators, Phillips/Okun, decision regressions, rule baselines |
| Urban macro | stylized facts, city layout, robustness across seeds/models, shock response |
| Economic benchmark | role observation/action/reward consistency, algorithm comparisons, repeated seeds |
| Expectations survey | distribution similarity, ATE, scaled slopes, subgroup moments, open-ended coding, leakage checks |
| SPF forecast | MAE by variable/horizon, ablation ratios, human median comparison, recall/out-of-sample checks |
| Text-generated beliefs | survey correlation, known moment signs, balance statistic, DAG/mental-model analysis |
| Stock market | order execution rate, turnover, volatility, holdings/cash accounting, shock response |
| Bank run | withdrawal intent ATE, model-human bias correction, message variant comparison, interpolation/extrapolation boundary, mental-model coding, network contagion, liquidity stress |

For all targets:

- run a small pilot before full scale.
- repeat seeds before interpreting emergent patterns.
- report model/provider, prompt, FoV, and temperature.
- separate environment mechanics from LLM decision effects.
- preserve qualitative traces for human audit.

## Failure Modes

- **Prompt-only institution**: taxes, markets, order books, or bank rules exist only as prose.
- **Dirty-work deletion**: sample design, treatment construction, calibration, parser, coding, or bias correction is dropped because it is not the headline mechanism.
- **Reproduction tunnel vision**: the guide helps copy one paper but does not teach how to turn a user's research idea into a Society0 world.
- **Visible hidden labels**: treatment arm, seed, or ground truth leaks into FoV.
- **Temporal leakage**: historical agents use future facts through RAG, memory, or model recall.
- **Overhomogeneous agents**: synthetic population ignores priors, demographics, social text, or expert background.
- **Effect-size inflation**: LLM scenario experiments produce stronger effects than humans; report this risk.
- **Model overresponsiveness**: LLMs may anchor too strongly on explicit numeric treatments or forward guidance.
- **Metric opacity**: final metrics cannot be recomputed from records.
- **Prompt p-hacking**: prompt revised after seeing outcomes without being recorded.
- **Unmarked extension**: Society0-specific design presented as paper fidelity.
- **Scale illusion**: large agent counts hide weak FoV/action/validation design.
- **Ablation mismatch**: removing a module changes the env or survey protocol too, making interpretation impossible.

Use this rule when uncertain: if a mechanism changes what agents can see, do, remember, or how the environment responds, it belongs in the scaffold and the fidelity matrix.
