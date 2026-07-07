# Economics And Finance Macro And Urban Simulation

Use this guide to design Society0 simulations of household macroeconomies, urban multi-role economies, and economic-policy testbeds. It consolidates EconAgent, SimCity, and EconGym into target-level patterns.

Contents:

- Scope
- Source status
- Dirty work triage
- Pattern 1: Household macroeconomy
- Pattern 2: Urban multi-role macroeconomy
- Pattern 3: Economic task/testbed benchmark
- Society0 scaffolds
- Runs, baselines, ablations, and validation
- Common mistakes

## Scope

Macro and urban simulations should be built as market/institution engines with LLM agents making bounded economic decisions inside them. The key Society0 question is:

```text
Which institutions and markets must be hosted by the environment so that agent decisions have economic consequences?
```

Use a custom environment for serious reproduction. Use `plain` only for prompt, FoV, schema, and pilot debugging.

## Source Status

Supported by paper and code:

- **EconAgent**: household-only macro simulation with monthly work and consumption propensities; official repo specifies core parameters, tax brackets, action grid, monthly schedule, and reflection logic.
- **SimCity**: LLM households, firms, government, central bank, goods/labor/financial markets, housing, city map, VLM-based firm placement, two simulation phases, monthly steps, prompt/action examples in appendix.
- **EconGym**: modular economic testbed with households, governments, banks, firms, 25+ tasks, role-specific observations/actions/rewards, and multiple agent algorithms.

Supported by official code/config:

- EconAgent official repo includes `config.yaml`, monthly simulation code, labor/consumption/saving/tax modules, and Foundation-style wrappers.
- EconGym official repo includes YAML scenarios, economic role docs, entity modules for households/government/banks/market, and run scripts.
- SimCity official repo was not found in this pass; rely on paper and appendix.

Inference for Society0 mapping:

- EconAgent and SimCity should be implemented as custom envs because their markets, accounting, and clearing order are core semantics.
- EconGym should be used as a benchmark design pattern, not copied wholesale unless the user wants MDP/RL-style policy optimization.

Unknown or unavailable:

- SimCity code/config details beyond the paper appendix.
- Exact prompt files for SimCity beyond appendix examples.

## Dirty Work Triage

Macro/urban papers can look like "agents make economic decisions each month," but the hard design work is in the hosted economy. Triage:

- **Build into Society0**: action grids, bounds, validation, fallback handling, month/phase schedule, accounting order, market clearing, balance constraints, reflection/revision stages, and output ledgers.
- **Agent can help now**: clean initialization tables, convert paper/config parameters, draft shock scripts, build analysis for stylized facts, create pilot-scale synthetic city tables.
- **Ask the user**: target geography, calibration data, policy regime, acceptable simplifications, proprietary city/firm data, and whether exact paper fidelity or design transfer is the goal.
- **External pipeline**: VLM city placement, geospatial datasets, business registries, large firm/household generation, and long multi-seed runs.

Do not replace any required hosted economy with a broad prompt asking agents to "simulate an economy."

## Pattern 1: Household Macroeconomy

Use EconAgent for household macro simulations where the main behavioral object is how LLM households decide work and consumption under changing wages, taxes, prices, savings, and macro conditions.

### What To Preserve

Reproduce these before claiming EconAgent fidelity:

- One tick is one month.
- Canonical run: 100 household agents, 240 months; sensitivity can use 300 agents.
- Household profile includes name, age, city, job, offer, savings, last work status, last consumption, tax history, and wage/skill.
- Skill/wage initialization follows the official config when reproducing: `pareto_param=8`, `payment_max_skill_multiplier=950`, and initial goods price tied to mean hourly wage.
- Action surface is `work` and `consumption`, each in `[0, 1]` on a 0.02 grid.
- `work` is a propensity; env samples a binary labor decision.
- `consumption` is the intended share of current savings plus current income to spend on essential goods.
- Labor supply for a working household is 168 hours per month.
- Government collects progressive income tax monthly and redistributes revenue evenly.
- Consumption is capped by goods inventory.
- Wage and goods price update from supply-demand imbalance with bounded random changes.
- Savings returns and interest-rate updates occur at period boundaries.
- Quarterly reflection is explicit; it is not the same thing as generic vector memory.
- Metrics include inflation, unemployment, nominal/real GDP, wage inflation, growth, inequality, productivity, decision regressions, invalid decisions, and reflection text.

### Society0 Mapping

```text
Setting:
  Monthly household economy with labor income, essential goods, taxes,
  redistribution, savings returns, wages, prices, and macro indicators.

Agents:
  Household LLM agents.
  Government, bank, and market clearer as env rules unless their communication is the study object.

FoV:
  Month, profile, current job/offer, expected wage income, last labor status,
  savings, price level, price direction, interest rate, prior consumption,
  shortage, tax paid, redistribution, tax schedule, and recent reflection.

Action:
  submit_household_decision(work: float, consumption: float)

Hosted constraints:
  action grid, labor sampling, income, tax, redistribution, inventory,
  price/wage update, savings returns, interest-rate rule.

Records:
  decisions_monthly, household_monthly, tax_monthly, consumption_monthly,
  macro_monthly, macro_annual, reflections_quarterly, invalid_decisions,
  run_config.
```

### Minimal Step Loop

```python
@engine.step(name="econagent_month")
async def econagent_month(ctx):
    households = ctx.agents.where(type="household")

    await ctx.rule("prepare_month")

    decisions = await households.instruct(
        "Make this month's household economic decision.",
        fovs=["monthly_economic_fov"],
        actions=["economic_decision"],
        required_actions=["submit_household_decision"],
        terminal_actions=["submit_household_decision"],
        max_turns=2,
        max_tokens=120,
        memory=True,
        name="household_monthly_decision",
    )

    labor_rows = await ctx.rule("apply_labor_from_decisions")
    tax_rows = await ctx.rule("collect_and_redistribute_tax")
    consumption_rows = await ctx.rule("apply_consumption_market")
    macro_row = await ctx.rule("update_macro_markets")

    reflection_rows = []
    if (ctx.step + 1) % 3 == 0:
        reflection_rows = await ctx.rule("run_quarterly_reflection")

    return ctx.result(
        metrics=macro_row,
        tables={
            "decision_calls": decisions.table(),
            "decision_actions": decisions.actions(),
            "labor": labor_rows,
            "tax": tax_rows,
            "consumption": consumption_rows,
            "reflection": reflection_rows,
        },
    )
```

### Fidelity Matrix

| Paper element | Society0 mapping | Fidelity |
| --- | --- | --- |
| Monthly time, 240-month run | step count and run config | exact |
| `work` and `consumption` grid | action validation and quantization | exact |
| Binary labor sampled from `work` propensity | env rule | exact |
| Progressive tax and equal redistribution | env rule with official brackets/rates | exact if official config copied |
| Quarterly reflection | `interview` or action-free `instruct`, stored in visible state/FoV | approximate |
| Foundation environment internals | Society0 custom env/rules | approximate |
| Model-specific GPT-3.5 setup | provider/run config | approximate unless same model accessible |

## Pattern 2: Urban Multi-Role Macroeconomy

Use SimCity when the research target requires multiple economic roles, differentiated goods, firms, housing, financial activity, government policy, central-bank policy, and urban spatial development.

### What To Preserve

Core SimCity elements:

- Four LLM agent types: households, firms, government, and central bank.
- Markets: heterogeneous goods market, frictional labor market, and financial market.
- Housing and urban map matter; new firm placement can be VLM-assisted.
- Two phases: move-in phase of 36 monthly steps; development phase of 144 monthly steps.
- Maximum population in experiments: 1000 households.
- Each step represents one month.
- Agents can only access information from previous steps.
- Agents interact with the environment through function calling; the framework verifies and executes actions.
- Step stages: production/trading, taxation/dividend, metabolic changes, and revision. Only the revision stage involves LLM agents.
- Households decide consumption bundle, labor-market action, housing, and financial activity.
- Firms decide output quantity/price, job vacancies/layoffs/wages, borrowing, and capital investment.
- Government adjusts taxation, UBI/welfare, public investment, and reserves.
- Central bank sets policy interest rate using a modified Taylor rule with smoothing; deposits and loans accrue interest.
- Investment pool aggregates household investment, returns unused funds, and can establish new firms from templates.
- Metrics test stylized facts: Phillips curve, Okun's law, Beveridge curve, Engel's law, law of demand, investment volatility, urban clustering, robustness across seeds, and shock response.

### Society0 Mapping

```text
Setting:
  A city economy with households, firms, government, central bank, housing,
  goods, labor, financial accounts, and spatial placement.

Agents:
  household, firm, government, central_bank.
  Optional investment_pool/city_planner as LLM or VLM-assisted system actor.

FoVs:
  household_monthly_report:
    profile, cash, job, home/rent, prior income/outcome, vacancies,
    available housing, prices and price changes, ROI, bank rates, loans,
    news, 12-month personal history.
  firm_monthly_report:
    cash, employees and skills, input prices/suppliers, demand, inventory,
    capital, debt, wages, vacancies, citywide market stats.
  government_report:
    balance, tax schedules, collected tax, UBI, GDP components, inequality,
    unemployment, homelessness, wage, money supply, bank rates, news.
  central_bank_report:
    inflation, GDP/output gap, unemployment, deposit/loan conditions,
    prior rates, trend/potential output.

Actions:
  Households:
    find_job, resign, modify_needs_percentage, set_invest_rate, borrow,
    payback, save_money, withdraw, move_to_home, wait.
  Firms:
    set_output_price_quantity, post_job, layoff, change_wage, borrow,
    invest_capital, buy_inputs, payback, save_money, wait.
  Government:
    adjust_bracket, adjust_UBI, invest, borrow, payback, save_money,
    withdraw.
  Central bank:
    set_policy_rate, set_reserve_rate, wait.

Hosted constraints:
  production functions, input-output recipes, labor matching, goods trading,
  rent/housing availability, tax/VAT, dividends, bankruptcy, bank accounts,
  interest accrual, loan constraints, price and wage histories.

Records:
  household_monthly, firm_monthly, jobs, goods_trades, housing_moves,
  bank_accounts, loans, tax_dividend, policy_actions, city_layout,
  macro_monthly, shocks, action_validation, run_config.
```

### Step Ordering

Implement the monthly order explicitly:

1. **Production and trading**: firms produce; households/firms buy goods according to prior plans.
2. **Taxation and dividends**: collect household income tax and firm VAT/profit tax; distribute UBI/welfare; pay dividends.
3. **Metabolic stage**: create firms from investment pool, remove bankrupt firms, grow population in move-in phase.
4. **Revision stage**: LLM households, firms, government, and central bank observe prior outcomes and submit actions for the next period.

Do not let revision-stage decisions alter already-cleared current-period accounting unless intentionally deviating from SimCity.

### Fidelity Matrix

| Paper element | Society0 mapping | Fidelity |
| --- | --- | --- |
| Monthly phase lengths 36 + 144 | run config and phase rule | exact |
| Four agent types | agent types and FoVs | exact |
| Function calling | Society0 env actions | exact in semantics |
| VLM city placement | external VLM or rule approximation | approximate |
| Production and financial formulas | env rules | approximate unless all appendix formulas copied |
| Prompt examples | FoV/action prompts adapted from appendix | approximate |
| gpt-4o-mini/gpt-4 setup | provider config | approximate unless same provider/model used |

## Pattern 3: Economic Task/Testbed Benchmark

Use EconGym when the user wants policy benchmarking, task composition, or role/algorithm comparison rather than an LLM-only social simulation.

### What To Preserve

EconGym is organized as economic roles plus agent algorithms:

- Roles: individual, government, bank, firm, market.
- Individual variants: Ramsey and OLG households.
- Government variants: fiscal authority, central bank, pension authority.
- Bank variants: non-profit platform and commercial bank.
- Firm/market variants: perfect competition, monopoly, oligopoly, monopolistic competition.
- Algorithms: rule-based/economic methods, real-data replay, behavior cloning, reinforcement learning, and LLM.
- More than 25 tasks across pension, fiscal policy, monetary policy, market competition, and individual decision-making.
- Each role has observations, actions, and reward/objective definitions.
- Useful comparison principle: benchmark different algorithms under the same role/environment, and combine algorithms across roles.

### Society0 Mapping

Use EconGym as a design pattern for taskized Society0 evaluations:

```text
Task:
  "How does delayed retirement affect macro indicators?"

Roles:
  households=OLG, government=pension authority, bank=non-profit,
  market=perfect competition.

Agent algorithms:
  household=rule/BC/LLM depending on study,
  government=rule/RL/LLM/economic method,
  bank=rule,
  firm=rule or strategic agent.

Records:
  role_observation, role_action, reward_or_objective, macro_indicators,
  task_config, algorithm_assignment.
```

In Society0, do not expose reward functions as if they are visible agent goals unless that is part of the simulated institution. Store algorithm assignment and objective metadata in `properties` or run config.

## Runs, Baselines, Ablations, And Validation

### Pilot Run

Start with:

- EconAgent-style: 6-10 households, 6-12 months.
- SimCity-style: 10-30 households, 2-5 firms, 6-12 months.
- EconGym-style: one task, one role composition, one rule baseline and one LLM condition.

Pilot checks:

- each agent submits required actions exactly once per decision round.
- invalid action rate is low and recorded.
- no hidden treatment, seed, or baseline labels appear in FoVs.
- tax, inventory, bank, and order ledgers balance.
- macro metrics can be recomputed from tables.
- LLM cost/concurrency is visible in run summaries.

### Full Run

Only scale after the pilot passes:

- EconAgent reproduction: 100 households, 240 months; repeated seeds or 300-agent sensitivity when needed.
- SimCity reproduction: 36-month move-in + 144-month development; up to 1000 households if provider budget allows.
- EconGym-style benchmark: repeated seeds and algorithm combinations; keep task config frozen across algorithms.

### Baselines

Use baselines to isolate LLM effects from env mechanics:

- Rule households using CATS/LEN-like consumption and work heuristics.
- Economic-method government/central bank rules such as Taylor rule or Saez tax.
- Real-data policy replay when historical data are available.
- No-profile or homogeneous-profile LLM ablation.
- No-FoV-detail or no-observation ablation.
- No-reflection or no-memory ablation only when reflection/memory is a tested mechanism.
- Same env with different LLM providers/models.

### Validation

Macro/urban validations should include:

- accounting identity checks: taxes, redistribution, goods inventory, bank interest, loans, production.
- stylized facts only after repeated runs: Phillips curve, Okun's law, Beveridge curve, Engel's law, law of demand, investment volatility.
- policy/shock response comparisons against baseline and no-shock runs.
- qualitative trace audit of agent reasons and actions.
- sensitivity to seed, model, prompt version, and population sample.

## Common Mistakes

- Do not make agents calculate the economy. Agents choose bounded actions; env rules calculate consequences.
- Do not collapse EconAgent's `work` propensity into hours worked unless intentionally deviating.
- Do not hide world-changing behavior inside `interview`.
- Do not let SimCity agents act on current-step information that should only be available next step.
- Do not compare macro curves across models without matching time scale and measurement definitions.
- Do not claim urban realism from a text-only layout if the design target depends on VLM/spatial placement.
- Do not use EconGym rewards as visible motivations unless the simulated agent is supposed to know that reward function.
