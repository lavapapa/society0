# Economics And Finance Expectations And Survey Simulation

Use this guide to design Society0 simulations of macroeconomic expectations, inflation expectations, professional forecasts, text-generated beliefs, and LLM-based survey experiments.

Contents:

- Scope
- Source status
- Dirty work triage
- Core paradigm
- Household and expert expectation agents
- Inflation RCT and central-bank communication
- Date-restricted dynamic survey panels
- Professional forecaster simulation
- Text-to-belief generation
- Survey sample and experiment-recognition methods
- Society0 scaffold
- Baselines, ablations, and validation
- Common mistakes

## Scope

Expectation and survey simulations are usually **measurement-centered**, not market-clearing-centered. The environment hosts the survey protocol, historical date, treatment assignment, information exposure, and response schema. The agent generates beliefs, forecasts, reasons, and subjective uncertainty.

Use `interview` for survey responses and forecasts. Use `instruct` only when the agent takes a world-changing action, such as trading or withdrawing deposits after forming expectations.

## Source Status

Supported by full papers:

- **Simulating Macroeconomic Expectations in Survey Experiments with LLM-based Economic Agents**: construction -> design -> simulation -> evaluation; household agents with PCM/PEPM/SMIM; expert agents with PBM/PEPM/KAM; open-ended responses; selective recall; DAG mental models; module ablations.
- **Generating Inflation Expectations with Large Language Models**: SCE-like synthetic household personas, prior distributions, ten treatment groups, posterior forecasts, multi-model comparisons, demographic heterogeneity, temperature/model robustness, central-bank communication use case.
- **LLM Survey Framework**: date-restrictive prompting, internal consistency, 200 fixed SCE personas, RCT treatments T1/T2/T3, retrospective coverage to 1990, dynamic follow-up, clean identification, reasoning coding.
- **Simulating the Survey of Professional Forecasters**: SPF-style forecaster personas, real-time data, past SPF median forecasts, t through t+4 point forecasts, prompt-input ablations, temporal-leakage checks.
- **The Ghost in the Machine**: news article -> LLM belief -> aggregate balance statistic; survey validation; generated sentiment; DAG mental model analysis.
- **Homo Silicus**: LLMs as simulated economic agents; recapitulation vs replication; theory-grounded personas; robustness via prompt variations and model comparisons.
- **Aher, Argyle, Cui**: Turing experiments, silicon samples, scenario-experiment replication, prompt validation without outcome leakage, distribution-level comparison, effect-size inflation and social-sensitivity limits.

Supported by code/config:

- No official code was fully inspected for the expectation survey papers except linked infrastructure/project pages where available. Treat prompt supplements as source material to re-check for exact reproduction.

Inference for Society0 mapping:

- Survey protocols map naturally to env state + `interview`.
- Treatment arm, sample cell, wave, and date restriction must be hidden in `properties` and run tables unless the respondent is explicitly told.
- Open-ended reasons should be recorded as first-class tables because they are often analyzed separately from numeric forecasts.

Unknown or unavailable:

- Some prompt supplements and exact run scripts were not available locally during this pass.

## Dirty Work Triage

Expectation and survey-agent papers are often not about a fancy interaction loop. Their credibility comes from the survey and measurement machinery. Triage:

- **Build into Society0**: treatment assignment, hidden labels, FoVs, survey schemas, prior/posterior separation, date restrictions, response tables, and leakage checks.
- **Agent can help now**: clean SCE/SPF-style tables, normalize demographic bins, draft questionnaire wording, build probability-bin parsers, prepare open-ended coding prompts, write analysis scripts.
- **Ask the user**: target population, proprietary survey data, construct definitions, valid treatment wording, benchmark choice, and ethics constraints.
- **External pipeline**: social-media collection, knowledge-acquisition/RAG modules, real-time data vintage retrieval, and large model/provider sweeps.

Do not collapse a survey-agent design into "ask a persona what they expect."

## Core Paradigm

The shared pattern is:

```text
sample / persona construction -> survey design and information condition
-> date- and FoV-bounded interview -> numeric belief + reason
-> distribution / treatment / text analysis -> human comparison and ablation
```

Do not ask whether the LLM "got the answer right" in isolation. Ask which target is being evaluated:

- distributional similarity to human responses.
- treatment-effect recapitulation.
- qualitative reasoning or selective recall.
- forecast accuracy.
- historical coverage or high-frequency generated beliefs.
- dynamic effect decay.
- mechanism identification.

## Household And Expert Expectation Agents

Use the macro-expectations framework when simulating households and experts in macro survey experiments.

### Household Agents

Paper-supported modules:

- **PCM**: personal characteristics such as age, gender, political affiliation, education, and related demographic variables.
- **PEPM**: prior expectations and perceptions from survey data, such as inflation, unemployment, or home-price expectations.
- **SMIM**: social-media information module, retrieving and cleaning relevant text from X/social media for a specified topic and time window; high-engagement posts are used as a proxy for salient exposure.
- **RD**: random disturbances in generation hyperparameters to represent unobserved heterogeneity.
- initialization prompt: role, task objective, and module-invocation rules grounded in expectation-formation theory.

Society0 mapping:

```text
agent.state:
  visible demographics, prior expectations, confidence level, salient media excerpts.

agent.properties:
  sample id, treatment arm, survey source row, hyperparameter draw, hidden validation labels.

FoV/interview context:
  exact questionnaire wording, date, current information signal, prior belief, media snippets,
  confidence instruction.

Measurement:
  numeric expectation, probability distribution, confidence, open-ended reason.
```

### Expert Agents

Paper-supported modules:

- **PBM**: professional background from official websites or LinkedIn; can generate semi-synthetic profiles when sample size is too small.
- **PEPM**: prior expectations from expert surveys.
- **KAM**: knowledge acquisition via web search/RAG for current professional information.
- **RD** and initialization prompts analogous to household agents.

Society0 mapping:

```text
agent.state:
  professional role, institution type, field, experience, prior forecasts,
  curated professional knowledge snippets.

FoV/interview context:
  survey date, scenario/vignette/treatment, real-time macro context when allowed,
  professional background and prior expectations.
```

## Inflation RCT And Central-Bank Communication

Use Zarifhonarvar-style simulation when the target is household inflation expectations and information-provision effects.

### Design Pattern

Paper-supported elements:

- Synthetic personas follow Survey of Consumer Expectations microdata; one author version uses 7,580 observations.
- Persona variables include age, gender, marital status, education, income, and state; extra experiments include political affiliation and location.
- Three-stage flow: pre-treatment prior beliefs, information provision, post-treatment expectations.
- Pre-treatment questions elicit probability distributions for short-run and longer-run expectations.
- Agents are assigned to one control group and nine treatment groups.
- Example treatments include placebo population information, current federal funds rate, FFR plus projections, past inflation, current inflation, inflation plus forecasts, and 30-year mortgage rate.
- Post-treatment questions ask for point predictions about future inflation.
- Multiple models are compared: OpenAI, Anthropic, Llama, DeepSeek, and others.
- Model architecture, parameter size, knowledge cutoff, and temperature materially affect outputs.
- Demographic heterogeneity is evaluated against known household-survey patterns.

### Society0 Scaffold

```text
Setting:
  Inflation-expectations survey experiment with information treatments.

Agents:
  household_survey_respondent.

FoV:
  demographic role statement, survey date if relevant, prior elicitation,
  assigned information treatment text, and exact post-treatment question.

Actions:
  none for world change.

Interview outputs:
  short_run_distribution, long_run_distribution, posterior_short_point,
  posterior_long_point, confidence, open_reason.

Hosted constraints:
  random assignment, treatment text, hidden arm labels, probability-bin schema,
  model/provider/temperature, sample weights.

Records:
  persona_table, assignment_table, prior_distribution, treatment_text,
  posterior_forecast, open_reason, model_config, invalid_response.
```

### Validation

Compute:

- prior mean from probability-bin midpoints.
- average treatment effects relative to control.
- interaction between treatment and prior expectations.
- distribution moments by model and demographic group.
- treatment-induced convergence or homogenization.
- model-by-treatment heterogeneity.
- robustness to model, temperature, prompt wording, and reversed framing.

Do not infer central-bank communication effectiveness for humans from LLM results alone. Use the simulation to pre-test messages and generate hypotheses.

## Date-Restricted Dynamic Survey Panels

Use the LLM Survey Framework pattern when the target is retrospective coverage, dynamic effects, or clean identification.

### Design Pattern

Paper-supported elements:

- Knowledge/date restriction prompt freezes the respondent's knowledge to the survey date.
- Date restriction is validated with salient historical events before/after their occurrence.
- Internal consistency fixes personas across treatment arms and waves.
- Personas are sampled from SCE demographics; the validation uses 200 personas.
- Survey sequence mirrors Weber et al. inflation RCT: prior, treatment, posterior.
- Treatments: past inflation, Fed target, Fed forecast.
- Agents articulate reasoning before reporting a numerical forecast.
- Retrospective extension can create more than 50 waves back to 1990.
- Dynamic treatment effects are measured with follow-up interviews up to 12 months after treatment.
- Clean identification uses factual treatments released after the frozen survey date.

### Society0 Scaffold

```text
agent.properties:
  persona_id, stratum, treatment_arm, wave_id, survey_date, followup_horizon,
  clean_identification_flag.

FoV:
  "You are answering in {survey_date}; do not rely on later events."
  demographic persona, exact prior question, treatment text if assigned,
  no hidden treatment label.

Interview outputs:
  prior_distribution, reasoning_text, posterior_point, followup_point.

Records:
  date_restriction_check, wave_assignment, treatment_values,
  prior, posterior, followup, coded_reasoning, treatment_effects.
```

### Clean Identification Rule

Store future factual treatment values in env `properties` or step params. Reveal only the treatment text at the treatment moment. Never include future facts in persona state, memory, or prior FoV.

## Professional Forecaster Simulation

Use the SPF pattern when simulating expert macro forecasts rather than household expectations.

### Design Pattern

Paper-supported elements:

- Forecaster personas use hand-collected SPF participant characteristics from public sources.
- Persona variables include inferred gender, affiliation, affiliation type, job title, highest degree, degree field, graduation year, alma mater, company location, media engagement, and social media status.
- Forecasts are generated for 1999 Q1-2023 Q4, with an out-of-sample check for 2024.
- Prompt role: participant on the Survey of Professional Forecasters panel.
- FoV includes real-time macroeconomic data available at the survey date and past SPF median forecasts.
- Outputs are point forecasts for current quarter `t`, `t+1` through `t+4`, and this-year/next-year annual averages.
- Main model in paper: GPT-4o mini with temperature 1.0; robustness checks use other models.
- Ablations remove forecaster characteristics, real-time data, and past median SPF forecasts.
- Recall task asks the model to recall actual values to check whether performance is forecasting or memorization.

### Society0 Scaffold

```text
Setting:
  Quarterly SPF-style forecast panel.

Agents:
  professional_forecaster.

FoV:
  forecaster background, forecast date, variable definitions,
  real-time macro data available at date, prior SPF median forecasts,
  instruction not to use future data.

Interview outputs:
  forecast_vector=(t, t+1, t+2, t+3, t+4, this_year, next_year),
  optional 1-2 sentence explanation.

Hosted constraints:
  real-time data vintage, variable list, release date, anonymity, output parser.

Records:
  forecaster_persona, variable_definitions, real_time_data_snapshot,
  past_spf_median, forecast_output, forecast_error, ablation_condition,
  recall_task_output.
```

### Validation

Compute:

- mean absolute error against realized vintage/actuals.
- human vs AI median and individual forecast distributions.
- horizon-specific accuracy.
- ablation ratios relative to fully informed baseline.
- temporal-leakage diagnostics via recall task and post-cutoff forecasts.

Past human forecasts may proxy unobservable human information. If removed, performance can degrade sharply; do not treat them as incidental prompt decoration.

## Text-To-Belief Generation

Use Bybee-style design when the object is a time series of generated beliefs from news text, not a persona survey.

### Design Pattern

Paper-supported elements:

- Input corpus: historical news, such as WSJ or NYT.
- Sample article-level texts by period.
- Prompt asks whether a given article will increase or decrease a target economic or financial variable, plus confidence, magnitude, and short explanation.
- Article-level labels aggregate to period-level beliefs with a balance statistic: share increase minus share decrease among non-uncertain responses.
- Targets include S&P 500, CPI, unemployment, GDP, rates, and other macro/finance variables.
- Validate against AAII, Duke CFO Survey, SPF revisions, known survey moments, and out-of-sample/post-cutoff periods.
- Use explanations to build DAGs or coded mental models.

### Society0 Scaffold

```text
Setting:
  Text corpus as the information environment for belief formation.

Agents:
  belief_generator or representative_reader.

FoV:
  article headline/body excerpt, publication date, target variable definition.

Interview outputs:
  direction=increase/decrease/uncertain, confidence, magnitude, explanation.

Hosted constraints:
  corpus sampling, time window, target variable list, aggregation frequency,
  no future articles in current period.

Records:
  article_sample, prompt_input, article_belief, aggregate_belief,
  survey_benchmark, coded_DAG, sentiment_beta if used in finance analysis.
```

This pattern can be combined with financial-market simulations by feeding aggregate generated beliefs into trader FoVs or risk indicators.

## Survey Sample And Experiment-Recognition Methods

Use these P1 lessons across survey-agent designs:

- Aher's Turing Experiments simulate multiple subjects in a specific human-subject study and compare records to known findings. Prompt validation should not inspect the experimental outcomes, to avoid prompt p-hacking.
- Argyle's silicon sampling conditions the model on socio-demographic backstories from real survey respondents and evaluates whether response distributions preserve complex subgroup relationships. Use population/sample distributions, not hand-authored stereotypes.
- Cui et al. replicate many scenario-based psychology/management experiments and find high but imperfect main-effect recapitulation, weaker interaction-effect performance, larger effect sizes, and lower performance on socially sensitive topics. Treat LLM survey results as pilot/hypothesis evidence, not replacement evidence.
- Homo Silicus uses theory-grounded personas and persona mixtures to improve similarity to human behavior, and tests robustness through translated, alternative, and adversarial prompt variations. Use "recapitulate" unless a true replication standard is met.

## Society0 Scaffold

For most expectation/survey studies:

```python
@engine.step(name="survey_wave")
async def survey_wave(ctx):
    respondents = ctx.agents.where(type="respondent")

    prior = await respondents.interview(
        "Answer the prior expectation question.",
        fovs=["survey_prior_fov"],
        output=PriorExpectation,
        name="prior_expectation",
        memory=False,
    )

    await ctx.rule("assign_or_reveal_treatment")

    posterior = await respondents.interview(
        "Answer the follow-up expectation question.",
        fovs=["survey_treatment_fov"],
        output=PosteriorExpectation,
        name="posterior_expectation",
        memory=False,
    )

    return ctx.result(
        metrics={"n": len(respondents.ids())},
        tables={
            "prior": prior.table(),
            "posterior": posterior.table(),
            "assignment": ctx.table("assignment"),
        },
    )
```

Use `memory=False` for static survey interviews when the paper does not allow cross-wave memory. Use explicit state/history only when a dynamic panel or follow-up design requires the same agent to remember prior questions.

## Baselines, Ablations, And Validation

Minimum baselines:

- naive persona only.
- no persona / generic forecaster.
- no priors.
- no real-time data.
- no social media/RAG/text module.
- no date restriction.
- no reasoning-first instruction.
- alternate LLM provider/model.
- fixed vs randomized temperature/top-p if heterogeneity is part of the design.

Validation metrics:

- distribution shape similarity: Pearson/cosine over histogram bins.
- ATE and scaled-slope estimates for information treatments.
- forecast MAE/RMSE by variable and horizon.
- demographic subgroup moments.
- response diversity and homogeneity.
- coded open-ended response categories.
- DAG node/edge overlap and complexity.
- temporal leakage checks.
- prompt variation/translation/adversarial robustness.

## Common Mistakes

- Do not put treatment labels or clean-identification flags in visible agent state.
- Do not use direct JSON output for behavior, but survey measurement can use `interview` structured output.
- Do not compare only means when the paper evaluates distributions, treatment effects, or reasoning.
- Do not claim individual-level replication when the source only recapitulates population-level patterns.
- Do not use post-outcome prompt tuning without recording it; this is p-hacking.
- Do not allow historical agents to use future facts through memory, RAG, hidden state, or FoV leakage.
- Do not ignore model architecture and temperature; several papers show they materially change expectations.
