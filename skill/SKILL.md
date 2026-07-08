---
name: society0
description: "Help humanities, social science, communication, economics, finance, and interdisciplinary researchers use the Society0 simulation engine: design env-first LLM-based multi-agent social simulation experiments from observations or papers, choose or extend environments, configure rule-based or LLM-based agents, reproduce domain-specific simulation designs, write code-driven step(ctx) runs, configure LLM and embedding providers, monitor outputs, analyze results, and debug runtime issues."
---

# Society0

Use this skill to help a non-engineer researcher turn a social phenomenon into a small, runnable Society0 experiment, then inspect outputs and interpret results with appropriate methodological caution.

For new or complex designs, strongly prefer reading `references/founder-experience.md` before designing agents, writing code, or loading discipline-specific guides. Then load the relevant domain guide. The founder notes are the cross-domain guardrail for avoiding traditional ABM drift, prompt-only worldbuilding, over-structured event schemas, hidden macro assumptions, and scale before mechanism.

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
10. Treat memory as part of the simulation, not a speed optimization target. `memory=True` retrieves memory and saves extractive memory by default; use `extract_memory=False` only when the user explicitly accepts a lightweight pilot that is less faithful.
11. Treat the tool/action loop as part of the model of the social situation. Do not replace an action-bearing `instruct` round with direct JSON output just to reduce latency; use direct structured output only for action-free measurement tasks.
12. Use `terminal_actions=[...]` only when an action is semantically the named endpoint of the current task, such as submitting a final decision, leaving a round, or handing in a ballot. For social browsing rounds where read tools may continue but one real write interaction should finish the round, prefer `completion_action_tags=["social_write"]` instead of pretending each social action is terminal. Read actions can return user IDs and post IDs; when calling `comment`, `like_post`, `repost`, or `get_post_details`, use the explicit `post_id` shown by the environment.
13. Create one clean experiment folder per study. Put the experiment code, run outputs, analysis notebooks or scripts, and final report in that folder so runs do not mix.
14. Build the smallest useful run first: a few agents, a few ticks, explicit metrics, one qualitative table, and a clear run directory.
15. Inspect artifacts, explain what happened, then recommend repeated runs, controls, ablations, and sensitivity checks before making research claims. Use checkpoints for full state; default `events.jsonl` is a semantic monitoring log and does not include raw state-change rows.
16. If the user creates a useful environment, finds a bug, or develops a clear need from research practice, help them draft a focused GitHub issue or pull request for Society0.

## Researcher-Friendly Collaboration

Treat the researcher as the domain expert and the agent as the technical assistant. Ask for the observed phenomenon, social setting, actors, information flow, possible actions, and intended measurements; translate those into env, agents, steps, and outputs without forcing the user to learn framework internals. Before each run, summarize the experiment in everyday research language, including provider readiness and concurrency: "This run will let up to N LLM agents think at the same time." After each run, explain both quantitative metrics and qualitative traces, and clearly separate simulation output from empirical evidence.

Keep the todo list visible and update it as work progresses. The todo list should help the researcher see where they are in the experimental workflow, not expose incidental coding chores.

## Minimal Entrypoints

Imports:

```python
from society0 import EmbedModel, LLMModel, Society0
```

Base config:

```python
config = {
    "agent_types": [{"id": "reader", "archetype": "llm"}],
    "agents": [
        {"id": "alice", "type": "reader", "persona": "A skeptical reader.", "state": {"trust": 0.45}}
    ],
    "environment": {"type": "plain", "state": {"topic": "misinformation"}},
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
  experiment.py
  runs/
  analysis.py
  report.md
```

Do not reuse a run directory for a different experiment or model setup. Run artifacts can contain prompts, FoVs, memory retrievals, LLM outputs, interviews, and researcher data; keep them inside the experiment folder and do not commit or share them without review.

Code step:

```python
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
- `references/study-patterns.md`: Reusable study patterns for communication, interview/deliberation, governance, city, organization, economy, and IR/security simulations.
- `references/simulation-paper-distillation.md`: Meta-guide for reading full papers and distilling LLM-based social simulation methods into consolidated domain guides.
- `references/communication-social-media-simulation-design.md`: Entry point for social media, news diffusion, rumor/fake-news, information diffusion, polarization, echo chamber, platform intervention, social movement, group-agent, and hybrid-scale communication simulations.
- `references/interview-survey-deliberation-simulation-design.md`: Entry point for interview, survey, simulated respondent, focus group, deliberation, public-opinion, social-psychology, management/psychology scenario, human-subject replication, silicon sample, Habermas Machine, Plurals, Turing Experiments, and Generative Agent Simulations of 1,000 People designs.
- `references/governance-institution-public-policy-simulation-design.md`: Entry point for governance, institution, public-policy, legislative, coalition, commons, norm, moderation, election, roll-call, accountability, and policy-practice simulations.
- `references/international-relations-conflict-security-simulation-design.md`: Entry point for international relations, crisis escalation, conflict/security, strategic-game, diplomacy/security decision-making, historical conflict, historical battle emulation, wargaming, WarAgent, EscalAItion, BattleAgent, WarBench, and ARMOR designs, with high-risk non-operational boundaries.
- `references/economics-finance-simulation-design.md`: Entry point for economics/finance simulation targets, evidence map, taxonomy, loading order, and reproduction boundaries.
- `references/economics-finance-macro-urban-simulation.md`: Household macroeconomy, urban multi-role economy, and economic testbed designs distilled from EconAgent, SimCity, and EconGym.
- `references/economics-finance-expectations-survey-simulation.md`: Macroeconomic expectations, inflation expectations, professional forecasts, text-generated beliefs, and survey-agent experiment designs.
- `references/economics-finance-financial-market-simulation.md`: LLM trader, stock-market, investor-belief, bank-run, depositor-withdrawal, and crisis-communication simulation designs.
- `references/economics-finance-method-synthesis.md`: Cross-target economics/finance principles, fidelity checklist, calibration, FoV control, baselines, ablations, validation, and failure modes.
- `references/run-monitor-analyze.md`: Monitor runs and analyze quantitative and qualitative outputs.
- `references/debugging.md`: Provider, Chroma, schema, import, memory, and runtime troubleshooting.
- `references/field-examples.md`: Representative generative-agent and LLM social simulation examples.

## Domain Simulation Guides

When the user asks to reproduce, adapt, or design a simulation from a discipline-specific paper, first read `references/founder-experience.md` and `references/simulation-paper-distillation.md`, then load the matching domain guide after the core Society0 references. Domain guides should teach how to turn a research question, research hunch, or intuitive idea into Society0's env-first scaffold: setting, FoVs, actions, hosted constraints, records, measurements, baselines, ablations, and interpretation boundaries. They are not just paper summaries or reproduction checklists; use the references to triage non-headline work such as data preparation, persona construction, treatment design, calibration, parsing, qualitative coding, and validation.

- Economics and finance overview: read `references/economics-finance-simulation-design.md` first for the target taxonomy, paper evidence map, loading order, and fidelity labels.
- Communication and social media overview: read `references/communication-social-media-simulation-design.md` when the user mentions social media, communication, feeds, posting, reposting, comments, likes, follows, recommendation algorithms, news diffusion, information diffusion, rumor, fake news, misinformation, disinformation, belief spread, attitude dynamics, emotion propagation, opinion dynamics, polarization, echo chambers, bridging algorithms, platform interventions, social movements, online events, group agents, hybrid scale, OASIS, S3, FPS, HiSim, GA-S3, MIDSim, LAID, FDE-LLM, or TopoSim.
- Interview, survey, deliberation, and social psychology overview: read `references/interview-survey-deliberation-simulation-design.md` when the user mentions interviews, surveys, simulated respondents, AI respondents, survey experiments, social psychology experiment replication, management/psychology vignette or scenario experiments, silicon samples, interview-grounded agents, human-subject replacement claims, deliberation, group discussion, focus groups, mini-publics, citizens' juries, Habermas Machine, Plurals, Turing Experiments, Out of One Many, Belief Engine, Do not simulate human psychology, or Generative Agent Simulations of 1,000 People.
- Governance, institution, and public policy overview: read `references/governance-institution-public-policy-simulation-design.md` when the user mentions governance, institutions, public policy, policy simulation, legislatures, committees, coalitions, government formation, manifestos, roll-call voting, elections, representative voting, commons, public goods, sanctions, norm emergence, rule compliance, legitimacy, accountability, content governance, moderation policy, fact-checking arms, appeals, Community Notes-like mechanisms, GovSim, CRSEC, MOSAIC, ElectionSim, Political Actor Agent, European Parliament voting, Artificial Leviathan, or policy-use preconditions.
- International relations, conflict, crisis, and security overview: read `references/international-relations-conflict-security-simulation-design.md` when the user mentions international relations, IR, diplomacy, crisis escalation, de-escalation, conflict/security simulation, wargames, wargaming, strategic games, security dilemma, alliances, deterrence, arms races, military/diplomatic decision-making, historical conflict, historical battle emulation, WarAgent, EscalAItion, BattleAgent, WarBench, or ARMOR. Keep the work research-bounded: scenario rehearsal, mechanism exploration, robustness checks, historical interpretation, safety evaluation, and research planning only; no operational military advice, conflict prediction, targeting, evasion, or policy recommendation.
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
