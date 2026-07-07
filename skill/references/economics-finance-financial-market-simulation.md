# Economics And Finance Financial Market Simulation

Use this guide to design Society0 simulations of LLM traders, stock markets, investor beliefs, bank runs, deposit withdrawals, and crisis communications.

Contents:

- Scope
- Source status
- Stock-market trading simulation
- Bank-run and depositor simulation
- Text-generated financial beliefs
- Society0 scaffolds
- Baselines, ablations, and validation
- Common mistakes

## Scope

Financial simulations require a stricter institution boundary than ordinary survey studies. The exchange, bank, order book, settlement rules, liquidity constraints, and contagion model must live in the environment. LLM agents may interpret news, form beliefs, choose trades, decide whether to withdraw deposits, or respond to communications.

Do not let the LLM "simulate the market" in prose. A financial-market simulation is only auditable when orders, balances, positions, deposits, withdrawals, and prices are recorded as state transitions.

## Source Status

Supported by full paper:

- **ASFM / Simulating Financial Market via LLM-based Agents**: simulated listed companies, opening and continuous order matching, four investor profiles, observation prompt with 15-day price history/order information/news, buy/sell/hold tools, daily trade limits, stock metrics, profile/observation ablations, policy shock scenarios.
- **Bank Run, Interrupted: Modeling Deposit Withdrawals with Generative AI**: LLM-based survey simulation of panic-driven depositor behavior; human validation sample; model-human bias correction; interpolated message variants; error-corrected withdrawal propensities; contagion model with activation, private signals, public survival signal, peer effects, redeposits, and mental-model DAG analysis.
- **The Ghost in the Machine**: generated stock-return expectations from news; survey validation; generated economic sentiment for asset-pricing/bubble analysis.

Supported by official/available code:

- The ASFM paper does not expose a complete official full-market codebase in the inspected source. A public `asfm-trading-agent` repo reproduces the single-step profile-conditioned decision logic with Pydantic structured output and post-hoc constraints, but explicitly does not implement full multi-day order matching.

Unknown or unavailable:

- Bank Run official code/config was not inspected in this pass; the guide is based on the full SSRN PDF and online appendix supplied locally.
- ASFM full code/config for order matching was not available in the inspected repo.

## Stock-Market Trading Simulation

Use ASFM when the target is how heterogeneous LLM traders react to prices, order book information, and economic news inside a simulated exchange.

### What To Preserve

Paper-supported elements:

- Market contains simulated listed companies with business descriptions, sectors, historical prices, and initial prices based on real data.
- Listed companies cover 11 sectors: energy, materials, industrial, consumer discretionary, consumer staples, healthcare, financial, information technology, telecommunication services, utilities, and real estate.
- Order matching has two mechanisms:
  - opening matching with price and time priority.
  - continuous matching where unmatched indicative orders are observable and agents can adjust bids.
- Transaction price is the average of matched buy and sell prices in the described opening mechanism.
- Trading stops when one side is exhausted or lowest sell price exceeds highest buy price.
- Closing price is average transaction price for the day.
- Agent account state includes cash, holdings, and return/statistics.
- Four investor profiles:
  - value investor.
  - institutional investor.
  - contrarian investor.
  - aggressive investor.
- Observation includes recent stock prices, historical order-book information, and economic policy news.
- Actions are buy, sell, and hold tools with stock code, quantity, and price.
- Agents are limited to no more than two buy/sell operations per stock per day.
- Evaluation metrics: order number, order execution rate, turnover rate, and volatility.
- Ablations: no profile, no observation, all value investors, all aggressive investors.
- Scenarios: interest-rate cut, inflation shock, trader behavior bias, large trader impact.

### Society0 Mapping

```text
Setting:
  Simulated exchange with listed companies, order book, news, trader accounts,
  matching, settlement, and price updates.

Agents:
  trader_llm with profile in state/properties: value, institutional,
  contrarian, aggressive; optional large_trader flag.

FoV:
  investor profile, wallet cash, current holdings, recent 15-day prices,
  business description, visible order book / indicative orders, current news,
  current portfolio return, trading constraints.

Actions:
  place_buy_order(stock_code, quantity, limit_price)
  place_sell_order(stock_code, quantity, limit_price)
  hold(stock_code=None)

Hosted constraints:
  cash checks, holding checks, per-stock daily action limit,
  order priority, matching, settlement, close price, position valuation.

Records:
  orders_raw, orders_validated, trades, order_book_snapshot,
  holdings_daily, cash_daily, price_daily, news_events, metrics_daily,
  invalid_orders, run_config.
```

### Minimal Step Loop

```python
@engine.step(name="trading_day")
async def trading_day(ctx):
    await ctx.rule("publish_news_and_open_books")

    traders = ctx.agents.where(type="trader")
    decisions = await traders.instruct(
        "Review today's market information and submit at most two orders per stock.",
        fovs=["trader_market_fov"],
        actions=["trade_action"],
        completion_action_tags=["trade_write"],
        max_turns=3,
        max_tokens=180,
        memory=True,
        name="trader_order_round",
    )

    validation = await ctx.rule("validate_orders")
    trades = await ctx.rule("match_and_settle_orders")
    close = await ctx.rule("update_closing_prices_and_accounts")

    return ctx.result(
        metrics=close,
        tables={
            "decision_calls": decisions.table(),
            "orders": decisions.actions(),
            "validation": validation,
            "trades": trades,
        },
    )
```

Use terminal actions only if one submitted order is supposed to end the round. If agents may place several orders, prefer `completion_action_tags=["trade_write"]` plus a turn/action cap.

### Fidelity Matrix

| Paper element | Society0 mapping | Fidelity |
| --- | --- | --- |
| Four trader profiles | agent type/profile prompt | exact |
| Buy/sell/hold tools | env actions | exact in semantics |
| Opening and continuous order matching | env rules | approximate unless full formulas copied |
| One/two operations per stock per day | action validator | exact |
| 11 sector company set | env config | exact if appendix company list copied |
| Policy news shocks | news event table/FoV | exact in semantics |
| Single-step public repo | prompt/action validation reference | approximate, not full market |

## Bank-Run And Depositor Simulation

Use the Bank Run pattern when studying depositor panic, crisis communication, or withdrawal contagion.

### Design Pattern

The paper's headline design is simple, but the credible simulation depends on several non-headline steps. Preserve both:

- Build synthetic depositor personas from demographic cells: gender, income, education, and age. The main text uses 200 demographic groups.
- Present a common panic scenario: a local bank run, a viral bank-run tweet, respondent is not a financial expert, and respondent has some uninsured deposits.
- Randomize treatment arms: baseline, network-effect friend withdrawal cue, bank email, bank text, president message, Fed message, cautionary tale, and placebo.
- Ask a binary withdrawal question plus a free-text explanation.
- Collect a human validation sample with the same instrument; the appendix reports a representative U.S. Prolific survey fielded in March 2025 with 1,200 participants, while the main table reports 1,158 usable human responses.
- Compare multiple LLMs under identical prompts and choose the primary open-weight model after human calibration. The paper evaluates GPT-4o, GPT-4o-mini, GPT-4.1, o3, Gemma-2-27B-Instruct, Qwen-2.5-72B-Instruct, Llama-3.3-70B-Instruct, and DeepSeek-R1; main estimates use Gemma.
- Treat LLM output as a fallible measurement device, not ground truth. Estimate model-human error at demographic-treatment cell level and subtract predicted bias before downstream analysis.
- Use population weighting/post-stratification when estimating representative effects.
- Generate interpolative message variants only inside the validated treatment design space. Extrapolative variants need fresh human validation.
- Feed error-corrected treatment propensities into a deterministic/stochastic contagion model rather than asking the LLM to narrate contagion.
- Use open-ended reasons to compare human and LLM mental models; the paper maps explanations to a shared DAG factor taxonomy.

### Dirty Work Triage

This paper is especially useful because it shows how much work surrounds the LLM call:

```text
validated survey instrument
-> demographic cells and weights
-> identical human and LLM prompts
-> model selection under leakage constraints
-> model-human bias correction
-> bootstrap uncertainty
-> interpolative message generation
-> contagion parameterization
-> mental-model coding
```

Triage it:

- **Build into Society0**: hidden assignment, FoV builders, response schemas, calibration tables, treatment/message tables, contagion rules, network state, and analysis output tables.
- **Agent can help now**: extract the survey instrument, normalize demographics, generate the 200-cell persona table, draft interpolative message variants, write bias-correction/bootstrap scripts, code free-text reasons with a transparent taxonomy.
- **Ask the user**: human validation data, target population, whether Prolific/U.S. representativeness is acceptable, policy-message approval, sensitive banking assumptions, and whether an action-mode liquidity extension is desired.
- **External pipeline**: new human survey collection, social-media/news crawling, large model comparison, and network-scale simulations beyond a pilot.

Do not compress this into "ask agents whether they withdraw."

### Survey Instrument

Represent the paper's survey as a fixed protocol, not as an ad hoc prompt:

```text
Demographics:
  household income, education, gender, age.

Common scenario:
  local-bank run in respondent's state, viral panic tweet, not a financial expert,
  some uninsured deposits.

Treatment arm:
  baseline, network effect, bank email, bank text, president message,
  Fed message, cautionary tale, or placebo.

Questions:
  binary withdrawal decision.
  free-text explanation.
```

For Society0, put demographics and assignment in `properties`, render only the intended persona and treatment text into the FoV, and keep the exact prompt version in the response table.

### Society0 Mapping

```text
Setting:
  Bank depositor population exposed to panic information and bank communication.

Agents:
  depositor_llm with demographics, account salience, bank relationship,
  risk tolerance, local network node. Hidden properties include demographic cell,
  survey weight, treatment assignment, model condition, calibration fold, and seed.

FoV:
  depositor profile, account/bank context, viral panic post,
  bank communication message if assigned, visible local-network cues
  if the contagion condition exposes them.

Interview outputs:
  withdrawal_intent, intended_withdrawal_fraction, trust, panic_reason,
  perceived_bank_survival, confidence.

Optional actions:
  withdraw_deposit(amount_or_fraction)
  keep_deposit()
  request_information()

Hosted constraints:
  treatment assignment, message variants, placebo/control, survey weights,
  human-calibration table, bias-correction model, bank liquidity, withdrawal queue,
  network contagion, proximity graph, communication policy.

Records:
  persona_table, network_edges, panic_exposure, communication_assignment,
  interview_response, free_text_reason, withdrawal_action, model_selection,
  human_benchmark_comparison, bias_correction, bootstrap_replicate,
  message_variant_metrics, interpolation_boundary, bank_liquidity,
  contagion_step, mental_model_factor.
```

Reproduce these scenario elements before claiming Bank Run fidelity:

- demographic variables: gender, household income, education, and age.
- uninsured-deposit framing.
- panic tweet and binary withdrawal question.
- baseline and seven active/placebo treatment arms.
- free-text explanation after the binary choice.
- system instruction to stay in character and answer `Yes` or `No` plus a brief reason.
- non-reasoning temperature comparison at `T in {0, 0.7}` and temperature-free reasoning models if doing model-selection reproduction.
- non-reasoning completion cap around 100 tokens and reasoning cap around 600 tokens when matching the paper's design.

### Calibration And Bias Correction

The paper's most important design lesson is that LLM survey outputs should be calibrated before being used as policy evidence.

Society0 mapping:

```text
1. Run a small human benchmark or import an existing human benchmark table.
2. Run LLM respondents with the same scenario, persona cells, and treatment arms.
3. Aggregate by demographic-treatment cell.
4. Estimate cell-level model-human error.
5. Regress error on demographics and treatment indicators.
6. Subtract predicted error from raw LLM labels.
7. Estimate treatment effects from corrected labels.
8. Bootstrap both calibration and primary synthetic samples when uncertainty matters.
```

Records:

```text
human_response
llm_response_raw
cell_aggregate
bias_model_fit
llm_response_corrected
ate_table
bootstrap_replicate
```

Do not compare raw LLM treatment rates to policy conclusions unless the user explicitly wants an uncalibrated pilot.

### Interpolative Message Variants

Use this pattern when a user wants to design crisis communications, not merely reproduce the original arms.

The paper distinguishes:

- **interpolative variants**: new messages that keep validated source, channel, tone, timing, framing, and semantic intensity within the already validated design space.
- **extrapolative variants**: new messenger, medium, audience, platform, language, unusually strong tone, or substantively new policy claim.

Society0 should store message features explicitly:

```text
message_id, source, channel, tone, assurance_strength, survival_clause,
framing, timing, treatment_text, interpolation_status
```

Only use calibrated LLM scale-up for interpolative variants. Mark extrapolative variants as requiring fresh human validation.

Paper-supported variant families include strong/standard/minimal versions of bank email, bank text, Fed message, president message, cautionary tale, placebo, and bank-originated variants with or without a survival clause such as a statement that the bank continues to meet withdrawals and operate normally.

### Contagion Model

After estimating individual propensities, the paper embeds them into a He-and-Manela-style contagion process. In Society0, this is an environment rule layer, not an LLM conversation.

Paper-supported parameters and mechanics:

- network: Watts-Strogatz small-world, `n=600`, `k=8`, `p=0.1`.
- initial beliefs: treatment log-odds shift from corrected calibration, mapped through logistic function.
- activation probability: `beta=0.4`.
- private signal precision: `q_i_tau = q0 + kappa * effort`; appendix uses `q0=0.6`, `kappa=0.6`, effort cost parameter `alpha=0.6`.
- public survival signal with `lambda=0.5`, `eta=5`, and decaying `zeta`.
- peer effect: neighbor withdrawal share enters a logit belief update; peer coefficient ramps over first `R=3` rounds to `gamma=1.0`.
- thresholds: truncated normal with mean `0.56` and standard deviation `0.18`.
- signal classes: low signal withdraws, high signal never withdraws, neutral waits and may withdraw when belief crosses threshold.
- redeposit: neutral withdrawers redeposit when updated belief falls below threshold.
- stopping: two consecutive no-change rounds or 30 rounds.

Society0 records:

```text
network_edges
agent_threshold
activation_round
private_signal
belief_update
neighbor_withdrawal_share
withdrawal_state
redeposit_event
contagion_summary
```

Keep survey-intent results separate from contagion dynamics. The LLM measures calibrated propensity; the env simulates propagation.

### Mental-Model Analysis

The paper uses free-text explanations to diagnose whether LLMs and humans reason similarly. This is not decorative; it explains why bias correction matters.

Use a shared factor taxonomy when analyzing reasons:

- FDIC Insurance.
- Risk and Safety.
- Trust and Doubt.
- Information/Evidence.
- Financial Constraints.
- Social/Panic.
- Institutional Credibility.
- Personal Experience.

Society0 should store factor coding separately from the raw explanation:

```text
reason_raw, coded_factor, decision, source_population, coder_model,
coding_prompt_version, factor_presence
```

Use this analysis to refine message design. If LLMs mention the right factors but over-weight them relative to humans, treat the model as directionally aligned but miscalibrated.

### Two Implementation Modes

Use **survey-intent mode** when reproducing the paper's core design:

```python
responses = await depositors.interview(
    "After reading the information available to you, state whether you intend to withdraw deposits.",
    fovs=["bank_run_survey_fov"],
    output=WithdrawalIntent,
    name="withdrawal_intent",
    memory=False,
)
await ctx.rule("propagate_withdrawal_propensities")
```

Use **action mode** when the user wants an actual bank balance-sheet simulation:

```python
await depositors.instruct(
    "Decide what to do with your deposits today.",
    fovs=["bank_run_action_fov"],
    actions=["bank_deposit_action"],
    required_actions=["withdraw_deposit", "keep_deposit"],
    terminal_actions=["withdraw_deposit", "keep_deposit"],
    max_turns=2,
    memory=True,
)
await ctx.rule("settle_withdrawals_and_update_liquidity")
```

Do not mix these modes without labeling them. Survey intent measures propensity; action mode changes bank state.

### Fidelity Matrix

| Paper element | Society0 mapping | Fidelity |
| --- | --- | --- |
| Demographic cells | persona construction + hidden cell properties | exact in semantics; normalize education categories carefully |
| Human validation sample | imported or newly collected benchmark table | exact in method if same instrument and sampling frame used |
| Viral panic post | FoV treatment text | exact in semantics; exact wording available in appendix |
| Randomized bank communication | hidden assignment + FoV message | exact |
| Bias correction | analysis table and correction rule | exact in method |
| Interpolative message variants | env treatment table with feature bounds | exact in method |
| Withdrawal propensities | `interview` output + corrected label | exact in semantics |
| Contagion model and proximity network | env rule/network table | exact in method if parameters above are used |
| Mental-model DAGs | coded free-text reasons | exact in method; coding implementation may vary |
| Actual bank liquidity simulation | Society0 extension | extension |

## Text-Generated Financial Beliefs

Use Bybee-style generated beliefs as either:

- a standalone financial-belief measurement system, or
- an exogenous input into trader/depositor FoVs.

Pattern:

```text
news corpus -> article-level belief interview -> aggregate belief/sentiment
-> validation against AAII/CFO/SPF -> financial-market analysis
```

Society0 mapping:

```text
Agents:
  representative_investor_reader or belief_generator.

FoV:
  article date, headline/body, target variable definition.

Interview output:
  direction, confidence, magnitude, explanation.

Rules:
  aggregate balance statistic by period, compute sentiment, join to market returns
  or feed to trader FoV.
```

When feeding generated beliefs into ASFM-style trading, record the belief source separately from trader decisions so analysis can distinguish exogenous belief signal effects from trader interpretation.

## Society0 Scaffolds

### Exchange Environment

```text
environment:
  type: financial_exchange
  state:
    listed_companies
    order_books
    price_history
    trader_accounts
    news_events
    trading_calendar
    pending_orders
```

FoVs:

- `trader_market_fov`: profile, account, holdings, prices, order book, news, constraints.
- `market_summary_fov`: aggregate market indicators for institutional or regulator agents.

Actions:

- `place_buy_order`
- `place_sell_order`
- `hold`
- optional `cancel_order`

Records:

- order and trade ledgers with enough fields to recompute ON, OER, TR, and volatility.

### Bank Run Environment

```text
environment:
  type: bank_run
  state:
    bank_liquidity
    deposit_accounts
    panic_messages
    communication_messages
    message_variant_table
    human_calibration_table
    bias_correction_model
    proximity_network
    current_wave
    pending_withdrawals
```

FoVs:

- `bank_run_survey_fov`: persona, panic post, assigned bank communication.
- `bank_run_action_fov`: persona, account balance, panic post, communication, observed local withdrawals if exposed.

Actions:

- `withdraw_deposit`
- `keep_deposit`
- `request_information`

Records:

- intent/interview table, free-text reasons, human benchmark table, raw/corrected LLM labels, action table, liquidity table, contagion table, message performance table, mental-model coding table.

## Baselines, Ablations, And Validation

Stock-market baselines:

- no profile.
- no observation/order-book/news.
- all value investors.
- all aggressive investors.
- rule traders with fixed strategy.
- no news shock.
- same news with different model/provider.

Stock-market validation:

- cash and holdings never go negative after validation.
- every trade has matched buy and sell orders.
- order execution rate recomputes from order/trade ledgers.
- turnover uses shares outstanding or configured float.
- volatility recomputes from price history.
- policy shock direction matches target qualitative prediction only after repeated runs.

Bank-run baselines:

- no panic post.
- panic post without bank communication.
- generic communication.
- direct personalized reassurance.
- strong vs weak reassurance.
- explicit survival clause vs omitted survival clause.
- raw LLM labels without bias correction.
- closed model versus open-weight calibrated model.
- interpolative variants versus extrapolative variants flagged for re-validation.
- no network propagation.
- rule-based withdrawal propensity.

Bank-run validation:

- compare intent distributions to the human benchmark.
- estimate and apply model-human bias correction before policy interpretation.
- estimate message ATEs on withdrawal intent.
- verify demographic-treatment cell coverage and survey weights.
- code free-text reasons and compare mental-model factors across humans and LLMs.
- track contagion amplification separately from initial intent.
- sensitivity to network topology and exposure probability.
- liquidity stress tests under simultaneous vs sequential withdrawals.

## Common Mistakes

- Do not use a single LLM response as a market price. Prices come from matching and settlement.
- Do not let traders buy without cash or sell without holdings unless margin/shorting is modeled.
- Do not summarize order books so heavily that order-matching metrics cannot be recomputed.
- Do not compare trader profits without recording risk, exposure, turnover, and liquidity.
- Do not treat withdrawal intent as actual withdrawal unless the simulation includes a bank action and liquidity rule.
- Do not use uncorrected LLM withdrawal rates as policy evidence when the design calls for human calibration.
- Do not treat extrapolative crisis messages as validated variants.
- Do not expose hidden treatment labels or network statistics unless the paper condition exposes them.
- Do not claim Bank Run code-level reproduction until official code/config is inspected or reimplemented from the paper.
