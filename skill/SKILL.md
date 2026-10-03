---
name: society0
description: "Help humanities, social science, communication, economics, finance, and interdisciplinary researchers design, implement, and analyze environment-first social simulations with LLM Agents in Society0, adapt simulation papers, configure providers, inspect outputs, and debug experiments."
---

# Society0

Use this skill to help researchers translate a social-science question or paper into an environment-first social simulation with LLM Agents, then inspect the outputs and interpret them with appropriate methodological caution.

For a first-time or undecided user, or whenever someone asks to learn Society0, read [references/researcher-onboarding.md](references/researcher-onboarding.md) and offer its numbered starting paths. Do not assume the user already has a research question or force a tutorial when they ask for a specific task. In the step-by-step learning path, explain one idea at a time and end each teaching response with at least three relevant next questions under “你可能还想问：” when speaking Chinese; localize that label and the suggestions to the user's language. Before a formal experiment, ask whether the researcher wants a token-and-cost estimate; follow the onboarding guide only if they opt in.

For substantive new designs, paper adaptation, or complex mechanisms, read `references/founder-experience.md`, then the matching domain guide. The founder notes address evidence boundaries, subject layers, env-hosted consequences, and scale discipline. A simple introductory pilot can start from the operating loop below and the complete starter; load larger references when the question needs their methods.

Before the first implementation or run, read [references/runtime-quickstart.md](references/runtime-quickstart.md). Adapt [assets/minimal_experiment.py](assets/minimal_experiment.py), which includes declared state, a real action loop, explicit thread-memory extraction, and measurement. Check the installed runtime's signatures once, reuse its existing environment, then initialize the configuration and run a small pilot. Domain code sketches require their own environment capabilities; use the complete starter for runtime wiring.

For an introductory study that fits the starter's exposure–memory–measurement sequence, the implementation reading set is runtime quickstart plus the starter, and workbench guide if requested. This includes changing its message, participants, condition labels or measured outcome. Keep the starter's thread and persistence lifecycle when adapting these research fields. Read onboarding when teaching. Add `step-dsl.md`, agent/environment guides, founder notes or a domain guide when a concrete design or API question needs them. A topic such as message credibility alone does not require all domain references for a two-tick introduction.

## Operating Loop

1. Start and maintain a visible todo list for the experiment. Use researcher-facing phases such as clarify phenomenon, design environment, define agents, write steps, run pilot, inspect outputs, analyze results, and refine.
2. Translate the user's observation into: research question, constructs, environment, agents, interaction loop, intervention/control, and measurements.
3. Before expanding the design, establish the evidence boundary and subject layer: what the supplied material can support, what it cannot support, which entities can actually perceive/decide/act, and which entities are only resources, records, institutions, graph nodes, or process slots.
4. Design the **environment first**: the social setting, visibility rules, possible actions, hosted constraints, interaction records, and institution/platform consequences. Agents only become meaningful inside that environment.
   For recommendation experiments, explicitly state the recommendation pool, scoring weights, pruning thresholds, and displayed post count; these are experimental conditions, not neutral plumbing.
5. Choose a built-in environment or propose a new one:
   - Start with `plain` for first surveys, simple state transitions, and rule baselines.
   - Use `social_network` for feeds, posts, endorsements, replies, recommendations, and diffusion.
   - Use `round_robin_conversation` for paired or rotating conversations.
6. Choose agent style:
   - Prefer **LLM-based agents** for interpretation, language, memory, persuasion, trust, identity, interviews, and social meaning.
   - Use **rule-based agents** for baselines, deterministic mechanisms, controls, parameter sweeps, fixtures, or non-linguistic updates.
7. For LLM agents, verify both provider layers: one LLM endpoint and one embedding endpoint. Suggest Ollama locally or OpenAI-compatible hosted providers such as OpenRouter, SiliconFlow, OpenAI, or Claude-compatible routes where appropriate.
8. Explain concurrency in plain language before running. If the user's LLM provider has a known concurrent request limit, set it on `LLMModel(..., concurrency=N)`; if unknown, use 5. `instruct` and `interview` automatically use this limit unless explicitly overridden. After running, verify batch-level `concurrency` and `concurrency_source` in `summary.json`.
9. For LLM action rounds and surveys, set a bounded `max_tokens` when the expected response is short, and inspect `summary.json` fields such as `total_input_characters`, `total_tools_characters`, `total_payload_characters`, and `outputs.total_bytes` when runtime is slow or run artifacts are large.
10. Treat memory as part of the simulation. `retrieve_memory=True` retrieves existing experience; durable writes are explicit. Open an Agent Thread, pass `thread_ids_by_agent` to the behavior round, then call `extract_thread_memories(...)` after it succeeds. Include extraction in the cost estimate and verify its result separately. See `references/step-dsl.md` and the complete starter.
11. Treat the tool/action loop as part of the model of the social situation. Do not replace an action-bearing `instruct` round with direct JSON output just to reduce latency; use direct structured output only for action-free measurement tasks.
12. Use `terminal_actions=[...]` only when an action is semantically the named endpoint of the current task, such as submitting a final decision, leaving a round, or handing in a ballot. For social browsing rounds where read tools may continue but one real write interaction should finish the round, prefer `completion_action_tags=["social_write"]` instead of pretending each social action is terminal. Read actions can return user IDs and post IDs; when calling `comment`, `like_post`, `repost`, or `get_post_details`, use the explicit `post_id` shown by the environment.
13. Create one clean experiment folder per study. Strongly prefer a `versions/<version-id>/` folder for each experiment configuration, with its `runs/<run-id>/` folders inside; keep analysis and the workbench at the study level. Existing layouts may be retained when reorganizing them would disrupt the study. This is a researcher-facing organization convention, not a Society0 runtime requirement. Never overwrite an earlier configuration or mix its run outputs with a later version. Read [references/workbench-guide.md](references/workbench-guide.md) for the layout and conversion contract when a workbench is requested.
14. After a concrete configuration draft exists and before the first pilot, offer the researcher an optional static visual workbench to check Agent settings, environment parameters, FoV definitions, and the planned version. Ask once, in terms of this study: “实验配置已有初稿。我可以做一个可视化工作台，让你按主体检查〔填入已定义的配置内容〕并提出修改；页面会把修改整理成一段可复制的请求，发回给我后才会改实验文件。你需要吗？” If they opt in, read [references/workbench-guide.md](references/workbench-guide.md), extract the current effective configuration into a versioned workbench data file, and generate the HTML even when there are no runs. If they decline, proceed without repeating the offer for this study. Then build the smallest useful pilot: a few agents, a few ticks, explicit metrics, one qualitative table, and a clear run directory.
15. Inspect artifacts and explain what happened. Before substantive interpretation or proposing a follow-up comparison, read the quantitative and qualitative analysis sections of [references/run-monitor-analyze.md](references/run-monitor-analyze.md). Separate observed results from possible mechanisms and state which contrast would test each explanation. Use checkpoints for full state; default `events.jsonl` is a semantic monitoring log and does not include raw state-change rows.
    If the workbench was already requested, update the same workbench with this version's saved run records. Otherwise, after inspecting the first completed run, offer it once if the researcher has not declined it for this study: “这次实验已有保存结果。我可以把〔填入已核对的指标、事件或会话〕加入可视化工作台，按配置版本、试运行、主体和 tick 查看。你需要吗？” The workbench never reads live data or controls the simulation. Use [references/workbench-guide.md](references/workbench-guide.md) to create an experiment-specific conversion script; preserve earlier versions and their run records. Keep FoV eligibility, recorded session content, and measured outcomes distinct. The supplied components are starting points; choose or add others when the study needs them.
    When a researcher sends a copied workbench change request, compare `baseVersionId`, `configSource`, `before`, and the actual experiment files; clarify conflicts, apply accepted changes in a new version folder, leave earlier versions intact, and regenerate the static workbench. Do not treat a browser draft as an applied experiment change or rerun the study without a separate request.
16. If the user creates a useful environment, finds a bug, or develops a clear need from research practice, help them draft a focused GitHub issue or pull request for Society0.

## Researcher-Friendly Collaboration

Treat the researcher as the domain expert and the agent as the technical assistant. Ask for the observed phenomenon, social setting, actors, information flow, possible actions, and intended measurements; translate those into env, agents, steps, and outputs without forcing the user to learn framework internals. Before each run, summarize the experiment in everyday research language, including provider readiness and concurrency: "This run will let up to N LLM agents think at the same time." After each run, explain both quantitative metrics and qualitative traces, and clearly separate simulation output from empirical evidence.

Keep progress visible in a short researcher-facing status. First studies need a concise design summary and a small pilot; create a longer design document when its complexity warrants it or the researcher asks. Resolve routine implementation choices from the accepted design and ask about choices that change the research question, mechanism, or interpretation. A model's stated reasons are qualitative clues; causal attribution requires controls and repeated observations.

## Minimal Entrypoints

Imports:

```python
from society0 import EmbedModel, LLMModel, Society0
```

For a complete first experiment, copy `assets/minimal_experiment.py` to the chosen version directory and adapt its configuration, FoV, action, and measurement. It supports initialization-only checking and a two-tick pilot. See `references/runtime-quickstart.md` for provider setup and commands.

Every custom state field needs a schema and a persistence declaration. For example:

```python
config = {
    "agent_types": [{"id": "reader", "archetype": "llm", "state_schema": {
        "type": "object", "additionalProperties": False,
        "properties": {"trust": {"type": "number", "persistence": {"kind": "replaceable"}}},
    }}],
    "agents": [
        {"id": "alice", "type": "reader", "persona": "A skeptical reader.", "state": {"trust": 0.45}}
    ],
    "environment": {"type": "plain", "state": {"topic": "misinformation"}, "state_schema": {
        "type": "object", "additionalProperties": False,
        "properties": {"topic": {"type": "string", "persistence": {"kind": "replaceable"}}},
    }},
}
```

Providers:

```python
llm = LLMModel.ollama(model="llama3.1", concurrency=5)
embed = EmbedModel.ollama(model="nomic-embed-text", concurrency=5)
engine = Society0(save_dir="runs/demo", base_config=config, llm=llm, embed=embed)
```

Use `Society0(..., agent_concurrency=N)` only when the experiment should globally override the LLM model's concurrency. Per-call `users.instruct(..., concurrency=N)` and `users.interview(..., concurrency=N)` are higher-priority overrides for special cases.

Experiment workspace:

```text
experiments/trust_pilot/
  versions/
    v001/
      experiment.py
      runs/
        pilot-001/
    v002/
      experiment.py
      runs/
  analysis/
    build_workbench.py
  workbench.html
  report.md
```

Do not reuse a run directory for a different experiment or model setup. Run artifacts can contain prompts, FoVs, memory retrievals, LLM outputs, interviews, and researcher data; keep them inside the experiment folder and do not commit or share them without review.

Code step:

```python
from pydantic import BaseModel, Field

class TrustSurvey(BaseModel):
    trust_score: int = Field(ge=1, le=7)
    reason: str

@engine.step(name="measure_trust")
async def measure_trust(ctx):
    users = ctx.agents.where(type="reader")
    survey = await users.interview("请评价这条信息的可信度。", output=TrustSurvey)
    return ctx.result(metrics={"avg_trust": survey.mean("trust_score")}, tables={"survey": survey.table()})
```

Run:

```python
await engine.run(steps=3)
```

Rule-only baseline:

```python
@engine.step(name="rule_update")
async def rule_update(ctx):
    for agent_id in ctx.agents.where(type="reader").ids():
        ctx.world.agents_data[agent_id]["state"]["trust"] *= 0.95
```

## Read References As Needed

- `references/engine-components.md`: Current Society0 components and how they map to the codebase.
- `references/founder-experience.md`: Cross-domain founder-level design lessons for evidence boundaries, subject layers, env-hosted consequences, semantic-rich FoVs, ABM drift, and scale discipline.
- `references/environment-design.md`: Why environment comes first, built-in environments, FoVs, actions, rules, and how to add a new env.
- `references/agent-design.md`: Agent types, personas, state, properties, models, memory, and reasoning stages.
- `references/step-dsl.md`: CodeSchedule, StepContext, AgentGroup, instruct/interview, results, outputs.
- `references/research-design.md`: Convert social science observations into simulation experiments.
- `references/researcher-onboarding.md`: First-use paths, step-by-step Society0 learning, experiment preparation, and optional pre-run token/cost estimates.
- `references/runtime-quickstart.md`: First implementation, existing Python setup, initialization check, explicit memory, provider verification, and complete pilot starter.
- `references/study-patterns.md`: Reusable study patterns for communication, interview/deliberation, governance, city, organization, education, law/legal society, public health, consumer/marketplace, economy, and IR/security simulations.
- `references/simulation-paper-distillation.md`: Meta-guide for reading full papers and distilling LLM-based social simulation methods into consolidated domain guides.
- `references/domain-distillation-coverage-audit.md`: Compact audit of accepted, routed, duplicate, generic, and evidence-gap domain-specific LLM social simulation papers.
- `references/communication-social-media-simulation-design.md`: Entry point for social media, news diffusion, rumor/fake-news, information diffusion, polarization, echo chamber, platform intervention, social movement, group-agent, and hybrid-scale communication simulations.
- `references/interview-survey-deliberation-simulation-design.md`: Entry point for interview, survey, simulated respondent, focus group, deliberation, public-opinion, social-psychology, management/psychology scenario, human-subject replication, silicon sample, Habermas Machine, Plurals, Turing Experiments, and Generative Agent Simulations of 1,000 People designs.
- `references/governance-institution-public-policy-simulation-design.md`: Entry point for governance, institution, public-policy, legislative, coalition, commons, norm, moderation, election, roll-call, accountability, and policy-practice simulations.
- `references/international-relations-conflict-security-simulation-design.md`: Entry point for international relations, crisis escalation, conflict/security, strategic-game, diplomacy/security decision-making, historical conflict, historical battle emulation, wargaming, WarAgent, EscalAItion, BattleAgent, WarBench, and ARMOR designs, with high-risk non-operational boundaries.
- `references/education-learning-classroom-simulation-design.md`: Entry point for education, classroom, learning, tutoring, teacher scaffolding, student misconceptions, peer learning, informal classroom social dynamics, and AgentSchool-inspired designs.
- `references/law-justice-crime-simulation-design.md`: Entry point for law, justice, legal society, crime propensity, legal deterrence, legislation, adjudication, enforcement, litigation, legal aid, rights protection, regulatory evasion, and Law in Silico-inspired designs.
- `references/public-health-simulation-design.md`: Entry point for public health, health behavior, vaccine hesitancy, risk communication, heatwave/climate health stress, vulnerability, protective behavior, community resilience, VacSim, and heatwave population health designs.
- `references/consumer-marketing-marketplace-simulation-design.md`: Entry point for consumer behavior, marketing interventions, price promotions, word-of-mouth, buyer/seller agentic markets, search, negotiation, transaction, marketplace design, welfare, manipulation, and Magentic Marketplace-inspired designs.
- `references/economics-finance-simulation-design.md`: Entry point for economics/finance simulation targets, evidence map, taxonomy, loading order, and reproduction boundaries.
- `references/economics-finance-macro-urban-simulation.md`: Household macroeconomy, urban multi-role economy, and economic testbed designs distilled from EconAgent, SimCity, and EconGym.
- `references/economics-finance-expectations-survey-simulation.md`: Macroeconomic expectations, inflation expectations, professional forecasts, text-generated beliefs, and survey-agent experiment designs.
- `references/economics-finance-financial-market-simulation.md`: LLM trader, stock-market, investor-belief, bank-run, depositor-withdrawal, and crisis-communication simulation designs.
- `references/economics-finance-method-synthesis.md`: Cross-target economics/finance principles, fidelity checklist, calibration, FoV control, baselines, ablations, validation, and failure modes.
- `references/run-monitor-analyze.md`: Monitor runs and analyze quantitative and qualitative outputs.
- `references/workbench-guide.md`: After a configuration draft or saved run and user opt-in, build one static workbench for versioned configuration review, proposed changes, and saved results.
- `references/debugging.md`: Provider, Chroma, schema, import, memory, and runtime troubleshooting.
- `references/field-examples.md`: Representative generative-agent and LLM social simulation examples.

## Domain Simulation Guides

Use this catalog once a substantive design or paper-adaptation question needs domain methods. The topic words below select the relevant guide at that point; introductory teaching and an unchanged starter follow the smaller reading set above. For paper adaptation, read `references/founder-experience.md` and `references/simulation-paper-distillation.md`, then the matching domain guide and any core API reference the implementation needs. Domain guides teach how to turn a research question into Society0's env-first scaffold: setting, FoVs, actions, hosted constraints, records, measurements, baselines, ablations, and interpretation boundaries. Use them to examine data preparation, persona construction, treatment design, calibration, parsing, qualitative coding, and validation as the study requires.

- Economics and finance overview: read `references/economics-finance-simulation-design.md` first for the target taxonomy, paper evidence map, loading order, and fidelity labels.
- Communication and social media overview: read `references/communication-social-media-simulation-design.md` when the user mentions social media, communication, feeds, posting, reposting, comments, likes, follows, recommendation algorithms, news diffusion, information diffusion, rumor, fake news, misinformation, disinformation, belief spread, attitude dynamics, emotion propagation, opinion dynamics, polarization, echo chambers, bridging algorithms, platform interventions, social movements, online events, group agents, hybrid scale, OASIS, S3, FPS, HiSim, GA-S3, MIDSim, LAID, FDE-LLM, or TopoSim.
- Interview, survey, deliberation, and social psychology overview: read `references/interview-survey-deliberation-simulation-design.md` when the user mentions interviews, surveys, simulated respondents, AI respondents, survey experiments, social psychology experiment replication, management/psychology vignette or scenario experiments, silicon samples, interview-grounded agents, human-subject replacement claims, deliberation, group discussion, focus groups, mini-publics, citizens' juries, Habermas Machine, Plurals, Turing Experiments, Out of One Many, Belief Engine, Do not simulate human psychology, or Generative Agent Simulations of 1,000 People.
- Governance, institution, and public policy overview: read `references/governance-institution-public-policy-simulation-design.md` when the user mentions governance, institutions, public policy, policy simulation, legislatures, committees, coalitions, government formation, manifestos, roll-call voting, elections, representative voting, commons, public goods, sanctions, norm emergence, rule compliance, legitimacy, accountability, content governance, moderation policy, fact-checking arms, appeals, Community Notes-like mechanisms, GovSim, CRSEC, MOSAIC, ElectionSim, Political Actor Agent, European Parliament voting, Artificial Leviathan, or policy-use preconditions.
- International relations, conflict, crisis, and security overview: read `references/international-relations-conflict-security-simulation-design.md` when the user mentions international relations, IR, diplomacy, crisis escalation, de-escalation, conflict/security simulation, wargames, wargaming, strategic games, security dilemma, alliances, deterrence, arms races, military/diplomatic decision-making, historical conflict, historical battle emulation, WarAgent, EscalAItion, BattleAgent, WarBench, or ARMOR. Keep the work research-bounded: scenario rehearsal, mechanism exploration, robustness checks, historical interpretation, safety evaluation, and research planning only; no operational military advice, conflict prediction, targeting, evasion, or policy recommendation.
- Education, classroom, and learning overview: read `references/education-learning-classroom-simulation-design.md` when the user mentions education, learning, classroom, tutoring, students, teachers, scaffolding, misconceptions, peer learning, recess/classroom social dynamics, educational intervention rehearsal, or AgentSchool.
- Law, justice, crime, and legal society overview: read `references/law-justice-crime-simulation-design.md` when the user mentions legal society, law, justice, crime propensity, deterrence, legislation, adjudication, enforcement, litigation, legal aid, rights protection, regulatory evasion, legal-system transparency, or Law in Silico. Keep the work research-bounded; do not provide legal advice, real-person risk scoring, policing recommendations, or evasion guidance.
- Public health, risk, and health behavior overview: read `references/public-health-simulation-design.md` when the user mentions public health, health behavior, vaccine hesitancy, vaccination attitude, risk communication, heatwaves, climate health stress, vulnerability, protective behavior, community resilience, VacSim, or heatwave population health. Keep the work research-bounded; do not provide medical advice or public health policy recommendations.
- Consumer, marketing, and marketplace overview: read `references/consumer-marketing-marketplace-simulation-design.md` when the user mentions consumer behavior, marketing intervention, discount, promotion, word-of-mouth, purchase behavior, customer journey, buyer/seller agents, agentic markets, search, negotiation, transaction, marketplace welfare, manipulation, or Magentic Marketplace.
- Household macro, urban macro, or economic testbed: read `references/economics-finance-macro-urban-simulation.md` when the user mentions EconAgent, SimCity, EconGym, work/consumption, tax redistribution, labor/goods/financial markets, city development, policy tests, GDP, inflation, unemployment, or stylized macro facts.
- Economics expectations experiments: read `references/economics-finance-expectations-survey-simulation.md` when the user mentions inflation expectations, macro expectations, professional forecasters, SCE/SPF-style panels, inflation-specific treatment effects, text-generated macro beliefs, recapitulation, or inflation/macro-expectations LLM respondents.
- Financial markets or banking crises: read `references/economics-finance-financial-market-simulation.md` when the user mentions ASFM, LLM traders, buy/sell/hold orders, order matching, investor beliefs, bank runs, deposit withdrawals, panic posts, or crisis communication.
- New economics/finance designs and robustness planning: read `references/economics-finance-method-synthesis.md` when the user asks for a new design, cross-paper synthesis, reproduction fidelity, calibration, FoV control, baselines, ablations, validation, or known failure modes.

Consolidate distillation products by discipline or simulation target instead of creating one reference file per paper. Create a general cross-domain simulation guide only after multiple domain guides exist and there is enough evidence to extract shared principles without flattening discipline-specific design constraints.

If the skill or references are not specific enough, inspect the source directly. Start from `src/society0/society.py`, `src/society0/schedule.py`, `src/society0/environment.py`, `src/society0/env/`, and `src/society0/agent/core.py`. Treat source behavior as authoritative.

## Contribution Support

When a researcher wants to contribute, treat their research artifact as the source of truth. Help turn useful environments, reproducible bugs, documentation gaps, and experiment-driven feature ideas into concise issues or focused pull requests. Keep contribution text legible to maintainers and explain the research use case, expected behavior, reproduction steps, and minimal code or output evidence.

## Guardrails

- Do not describe Society0 as a traditional ABM system with LLMs merely swapped in for rules. It is a language-mediated simulation paradigm that can borrow ABM rigor.
- Do not design agents before the environment. The environment defines what agents can see, do, and leave behind as evidence.
- Do not hide provider requirements. LLM agents require working LLM and embedding providers.
- Do not ask researchers to tune concurrency by default. Put known provider limits on the model declaration; use 5 when unknown.
- Do not turn off memory, actions, terminal/completion semantics, or the agent loop simply because a run is slow. Diagnose first; only simplify when the user explicitly accepts the modeling tradeoff.
- Do not mix multiple studies in one run folder. Create a fresh experiment folder before writing code, running simulations, or analyzing outputs.
- Do not make first experiments large. Prototype, inspect, then scale.
- Do not overclaim from one run. Treat outputs as simulated evidence requiring robustness checks and researcher interpretation.
