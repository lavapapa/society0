# Economics And Finance Simulation Design

Use this reference as the entry point for economics, macroeconomics, banking, market, policy, expectations, and finance simulations in Society0. Load it after `research-design.md`, `environment-design.md`, and `step-dsl.md`; then load the target guide that matches the user's study.

Contents:

- Domain stance
- Evidence map
- Target taxonomy
- Loading order
- General construction rules
- Design-transfer and dirty-work triage
- Reproduction boundaries

## Domain Stance

Model economics and finance simulations as **institution-hosted decision systems**:

```text
institution / market / survey protocol -> bounded FoV -> LLM or rule decision -> env rule / measurement -> auditable records -> economic interpretation
```

The environment owns institutions: prices, markets, budgets, tax schedules, ledgers, order books, banking rules, inventories, interest rates, survey assignment, historical dates, and disclosure constraints. LLM agents own situated interpretation: expectations, reasoning, attention, trust, preference articulation, communication response, and bounded actions.

Keep deterministic economic mechanics in environment rules. Do not ask the LLM to clear markets, calculate taxes, update bank balance sheets, maintain order books, or compute macro indicators unless the study is explicitly about human misunderstanding of those quantities.

Use `instruct` with environment actions for behavior that changes the simulated world: work, consume, borrow, save, trade, invest, withdraw deposits, hire, fire, set prices, or choose policy. Use `interview` for measurement: surveys, expectations, reasons, forecasts, perceived risk, and post-hoc explanations.

Use paper distillation to learn design moves for new Society0 studies, not only to copy paper recipes. Ask how a research concern becomes a simulated institution, bounded FoV, action or interview, hosted consequence, record, and validation target. Triage non-headline work such as sample construction, treatment wording, calibration, bias correction, qualitative coding, and robustness checks by whether it belongs in Society0, can be done by the agent, requires external tooling, or needs user input.

## Evidence Map

Read `simulation-paper-distillation.md` before using these guides for paper reproduction. The following papers were distilled into target-level references rather than one file per paper.

| Paper or source | Evidence status | Target guide |
| --- | --- | --- |
| EconAgent: Large Language Model-Empowered Agents for Simulating Macroeconomic Activities | Full ACL paper and official repo/config inspected | `economics-finance-macro-urban-simulation.md` |
| SimCity: Multi-Agent Urban Development Simulation with Rich Interactions | Full arXiv PDF including appendix prompt examples inspected; no official repo found during this pass | `economics-finance-macro-urban-simulation.md` |
| EconGym: A Scalable AI Testbed with Diverse Economic Tasks | Full arXiv PDF and official repo/user manual inspected | `economics-finance-macro-urban-simulation.md`, `economics-finance-method-synthesis.md` |
| Simulating Macroeconomic Expectations in Survey Experiments with LLM-based Economic Agents | Full arXiv PDF inspected; prompt supplement link noted but not downloaded into the skill | `economics-finance-expectations-survey-simulation.md` |
| Generating Inflation Expectations with Large Language Models | Author PDF and JME metadata inspected | `economics-finance-expectations-survey-simulation.md` |
| LLM Survey Framework: Coverage, Reasoning, Dynamics, Identification | Full NBER PDF inspected | `economics-finance-expectations-survey-simulation.md` |
| Simulating the Survey of Professional Forecasters | Full Stanford Digital Economy Lab PDF inspected | `economics-finance-expectations-survey-simulation.md` |
| The Ghost in the Machine: Generating Beliefs with Large Language Models | Full author PDF inspected | `economics-finance-expectations-survey-simulation.md`, `economics-finance-financial-market-simulation.md` |
| Large Language Models as Simulated Economic Agents: What Can We Learn from Homo Silicus? | Full NBER PDF inspected | `economics-finance-expectations-survey-simulation.md`, `economics-finance-method-synthesis.md` |
| Simulating Financial Market via Large Language Model based Agents | Full arXiv PDF and available ASFM single-step repo inspected | `economics-finance-financial-market-simulation.md` |
| Bank Run, Interrupted: Modeling Deposit Withdrawals with Generative AI | Full SSRN PDF and online appendix supplied locally and inspected; official code/config not inspected | `economics-finance-financial-market-simulation.md` |
| Aher et al., Using LLMs to Simulate Multiple Humans and Replicate Human Subject Studies | Full PMLR PDF inspected | `economics-finance-expectations-survey-simulation.md`, `economics-finance-method-synthesis.md` |
| Argyle et al., Out of One, Many | Full arXiv PDF inspected | `economics-finance-expectations-survey-simulation.md`, `economics-finance-method-synthesis.md` |
| Cui et al., Can LLMs Replace Human Subjects? | Full arXiv PDF inspected; Nature article page/PDF access was limited | `economics-finance-expectations-survey-simulation.md`, `economics-finance-method-synthesis.md` |
| Generative Agents | Full arXiv/UIST PDF and repo link inspected | `economics-finance-method-synthesis.md` |
| AgentSociety | Full arXiv PDF inspected | `economics-finance-method-synthesis.md` |
| GenSim | Full arXiv PDF and official repo inspected | `economics-finance-method-synthesis.md` |
| AdaSociety | Full arXiv PDF and official repo inspected | `economics-finance-method-synthesis.md` |

If a future user needs legal-grade or paper-specific reproduction, re-check latest paper versions and repos before claiming exact fidelity.

## Target Taxonomy

Choose the target by the economic object being simulated:

- **Household macroeconomy**: households decide work and consumption; env applies labor income, taxes, redistribution, goods constraints, wages, prices, interest, and macro indicators. Use `economics-finance-macro-urban-simulation.md`.
- **Urban multi-role macroeconomy**: households, firms, government, central bank, goods, labor, financial system, housing, and spatial layout co-evolve. Use `economics-finance-macro-urban-simulation.md`.
- **Economic task/testbed and policy benchmark**: roles have MDP-style observations/actions/rewards and algorithms are compared across tasks. Use `economics-finance-macro-urban-simulation.md` plus `economics-finance-method-synthesis.md`.
- **Expectations or survey-agent simulation**: agents answer inflation, macro, housing, or financial-expectations surveys, often with treatments and open-ended reasoning. Use `economics-finance-expectations-survey-simulation.md`.
- **Professional forecaster simulation**: expert personas use real-time macro data and past forecast information to produce SPF-style forecasts. Use `economics-finance-expectations-survey-simulation.md`.
- **Text-to-belief generation**: news or documents are used to generate expectations or sentiment time series. Use `economics-finance-expectations-survey-simulation.md`; if the target is asset pricing or bubble sentiment, also use `economics-finance-financial-market-simulation.md`.
- **Stock-market simulation**: trader agents observe price histories, order book/news, and use buy/sell/hold actions; env owns order matching and settlement. Use `economics-finance-financial-market-simulation.md`.
- **Bank-run or crisis-communication simulation**: depositor personas receive panic messages and bank communications; env maps withdrawal propensities into liquidity or contagion dynamics. Use `economics-finance-financial-market-simulation.md`.

## Loading Order

For economics/finance work, load only the files needed for the target:

1. `research-design.md`
2. `environment-design.md`
3. `step-dsl.md`
4. `simulation-paper-distillation.md` when adapting a paper
5. This overview
6. One or more target guides:
   - `economics-finance-macro-urban-simulation.md`
   - `economics-finance-expectations-survey-simulation.md`
   - `economics-finance-financial-market-simulation.md`
   - `economics-finance-method-synthesis.md`

Load `economics-finance-method-synthesis.md` when the user asks for a new design, reproduction fidelity, robustness plan, or cross-paper methodology.

## General Construction Rules

Use this env-first split in every economics/finance simulation:

| Component | Society0 location |
| --- | --- |
| Taxes, prices, rates, order matching, goods inventory, budget constraints, bank liquidity, survey wave dates, treatment labels | env state, env config, step params, `properties`, or output tables |
| Per-agent profile, job, income, wealth, holdings, prior beliefs, confidence, latest visible report | agent `state` if visible; agent `properties` if hidden |
| Role, demographic condition, synthetic sampling cell, treatment arm, random seed, baseline assignment | agent `properties` or run config, not visible FoV |
| What the agent can see at decision time | FoV |
| World-changing behavior | `instruct` with env actions |
| Survey answer, forecast, reason, confidence, mental-model text | `interview` |
| Accounting and market consequences | rules/behaviors after decisions |
| Validation and analysis | `ctx.result(metrics=..., tables=...)` plus analysis scripts |

Always record raw agent output, validated action, fallback reason, model/provider, prompt version, FoV version, seed, and treatment assignment in tables that make metrics recomputable.

## Design-Transfer And Dirty-Work Triage

When the user brings a research idea rather than a paper, translate it like this:

```text
research idea -> construct -> institution/protocol -> roles
-> FoV -> action/interview -> env consequence or measurement
-> records -> validation/benchmark -> interpretation boundary
```

Do not start by inventing personas. Start by deciding what economic or financial institution hosts the behavior: market, survey protocol, bank, policy regime, tax system, city economy, or forecast panel. Then decide which part of the phenomenon needs LLM interpretation rather than a deterministic rule.

Treat dirty work as design and triage it by who can act:

- **Build into Society0**: env rules, FoVs, action validators, hidden `properties`, output tables, run stages, and measurement schemas.
- **Agent can help now**: public data lookup, data cleaning, demographic binning, treatment drafting, persona-table generation, crawler/RAG scaffolding, calibration scripts, analysis scripts, and qualitative coding drafts.
- **Ask the user**: private or sensitive data, target population, construct definitions, treatment acceptability, benchmark choice, legal/IRB constraints, credentials, and whether a synthetic placeholder is acceptable.
- **External pipeline**: large-scale crawling, market data feeds, RAG corpora, geocoding/VLM assets, and large batch model runs.

Always tell the user which dirty-work items are required for the pilot versus for a credible full study.

## Reproduction Boundaries

Use these fidelity labels:

- **exact**: the paper/code specifies the mechanism and Society0 can implement it directly.
- **approximate**: the paper mechanism is clear, but Society0 implementation differs in interface, model, provider, or infrastructure.
- **extension**: the design is inspired by the paper but intentionally adds Society0-specific structure.
- **unsupported**: source material is missing or the paper does not specify enough to reproduce.

Do not present an experiment as one-to-one reproduced unless all reproduction-critical pieces are specified: population construction, FoV, action schema, scheduling, deterministic rules, model settings, prompt wording or prompt family, output parsing, baselines, ablations, and metrics.

Prefer the phrase **recapitulate** when comparing LLM survey or social-science results to human studies. Many papers in this area explicitly distinguish recapitulating qualitative patterns from statistically replicating individual-level human responses.
