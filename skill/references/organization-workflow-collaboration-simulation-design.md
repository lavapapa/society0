# Organization, Workflow, And Collaboration Simulation Design

Use this guide for Society0 simulations about organizations, management,
workflows, collaboration, team behavior, enterprise task environments, human-AI
teamwork, workplace coordination, software-company workflows, and
organization-level decision behavior. Load it after `research-design.md`,
`environment-design.md`, `agent-design.md`, and `step-dsl.md`; also load
`simulation-paper-distillation.md` when adapting a paper.

This guide is not a generic multi-agent-framework guide. Extract organization,
team, workflow, role, delegation, artifact, and task-environment mechanisms only.
Use ChatDev, MetaGPT, and AutoGen as supporting evidence for workflow design
patterns, not as a reason to add a broad MAS reference file.

Contents:

- Domain stance
- Evidence map
- Target taxonomy
- Loading order
- Society0 construction rules
- Paper-derived patterns
- Dirty-work triage
- Scaffold checklist
- Baselines, ablations, and validation
- Reproduction boundaries
- Failure modes

## Domain Stance

Model organization and workflow simulations as **work-environment-hosted
coordination systems**:

```text
organization / team setting / task environment / tool workspace
-> role-bounded FoV -> LLM interpretation, communication, delegation, or work
-> env task, artifact, permission, and dependency updates
-> auditable traces -> mechanism exploration or benchmark interpretation
```

The environment owns the organization: roles, circles, reporting lines,
delegation topology, permissions, work queues, tickets, calendars, artifacts,
workspaces, tools, validators, deadlines, evaluation rubrics, public
announcements, and process logs. LLM agents own situated work behavior:
interpreting requirements, negotiating task ownership, communicating with
teammates, deciding what to delegate, writing or revising artifacts, using tools,
and explaining process judgments under bounded information.

Use `plain` only for a first controlled workflow or survey prototype. Use a
custom env when role permissions, task dependency graphs, artifacts, calendars,
tool actions, UI/browser state, validators, or organization records are central
to the research claim. Keep organization-specific mechanics inside the env or
experiment, not in `Society0`, `CodeSchedule`, `World`, or generic agent APIs.

High-stakes boundary: organization and work simulations can support mechanism
exploration, workflow rehearsal, task-benchmark design, and research planning.
They cannot by themselves evaluate workers, justify hiring, firing, discipline,
performance management, compensation, surveillance, union strategy, automation
policy, or consequential enterprise deployment. Require consent, privacy review,
human and domain expert evaluation, labor-impact assessment, and accountable
institutional governance before any consequential workplace use.

## Evidence Map

Read `simulation-paper-distillation.md` before using this guide for paper
reproduction. Candidate lists are not evidence.

| Paper or source | Evidence status | Main design target |
| --- | --- | --- |
| Zhu et al., *Generative Organizational Behavior Simulation using Large Language Model based Autonomous Agents: A Holacracy Perspective*, arXiv:2408.11826, https://arxiv.org/abs/2408.11826 | Full arXiv PDF inspected. Public official code was not confirmed in this pass. | CareerAgent holacracy simulation with construction, execution, and evaluation phases; roles, circles, tasks, meetings, competence, stress, task completion, and communication networks. |
| Zhu et al., *Can LLM Agents Sustain Long-Horizon Organizational Dynamics?*, arXiv:2606.01199, https://arxiv.org/abs/2606.01199 and https://github.com/ZhuXuanCH/TaskWeave | Full arXiv PDF and official repository README checked. | TaskWeave long-horizon organizational coherence with role hierarchy, delegation topology, planning-state propagation, dependency-aware execution, trace memory, tools, and incident adaptation. |
| Zou et al., *Simulating Organized Group Behavior: New Framework, Benchmark, and Analysis*, arXiv:2604.09874, https://arxiv.org/abs/2604.09874 and https://huggingface.co/datasets/jayzou3773/GROVE | Full arXiv PDF inspected. Paper lists a GROVE dataset link; a separate official code repository was not confirmed in this pass. | Organization or group as unit of analysis, historical context-decision pairs, codified decision trees, temporal drift, cross-group transfer, and multidimensional evaluation. |
| Almutairi et al., *Simulating Teams with LLM Agents: Interactive 2D Environments for Studying Human-AI Dynamics*, arXiv:2510.08242, https://arxiv.org/abs/2510.08242 | Full arXiv PDF inspected. Public official code was not confirmed in this pass. | VirT-Lab configurable team simulations with 2D environment, roles, movement, communication, action logs, team metrics, agent metrics, and post-hoc interviews. |
| Ju and Aral, *Collaborating with AI Agents: A Field Experiment on Teamwork, Productivity, and Performance*, arXiv:2503.18238, https://arxiv.org/abs/2503.18238 | Full arXiv PDF inspected. Treat as empirical process evidence, not as an LLM-agent simulation paper. | Pairit randomized human-human versus human-AI collaboration, real-time workspace logs, task-oriented communication, delegation, AI recognition, quality tradeoffs, and diversity collapse. |
| Xu et al., *TheAgentCompany: Benchmarking LLM Agents on Consequential Real World Tasks*, arXiv:2412.14161, https://arxiv.org/abs/2412.14161 and https://github.com/TheAgentCompany/TheAgentCompany | Full arXiv PDF and official repository README checked. | Self-contained software-company benchmark with workplace web apps, simulated coworkers, checkpoints, partial-credit evaluators, and diverse professional tasks. |
| Drouin et al., *WorkArena: How Capable are Web Agents at Solving Common Knowledge Work Tasks?*, arXiv:2403.07718, https://arxiv.org/abs/2403.07718 and https://github.com/ServiceNow/WorkArena | Full arXiv PDF and official repository README checked. | Enterprise web-task benchmark with BrowserGym observations/actions, ServiceNow tasks, oracle/validator machinery, and human comparison. |
| Boisvert et al., *WorkArena++: Towards Compositional Planning and Reasoning-based Common Knowledge Work Tasks*, arXiv:2407.05291, https://arxiv.org/abs/2407.05291 and https://github.com/ServiceNow/WorkArena | Full arXiv PDF and official repository README checked. | Compositional knowledge-work workflows with L2/L3 tasks, ticket instructions, task composition, validators, and oracle traces. |
| Qian et al., *ChatDev: Communicative Agents for Software Development*, arXiv:2307.07924, https://arxiv.org/abs/2307.07924 and https://github.com/OpenBMB/ChatDev | Full arXiv PDF and official repository README checked. | Software-company collaboration through phase roles, chat chain, communicative dehallucination, design/coding/testing loops, and review records. |
| Hong et al., *MetaGPT: Meta Programming for A Multi-Agent Collaborative Framework*, arXiv:2308.00352 / ICLR 2024, https://arxiv.org/abs/2308.00352 and https://github.com/geekan/MetaGPT | Full arXiv PDF and official repository README checked. | SOP-based role specialization, structured artifacts, publish-subscribe message pool, executable feedback, and assembly-line handoffs. |
| Wu et al., *AutoGen: Enabling Next-Gen LLM Applications via Multi-Agent Conversation*, arXiv:2308.08155, https://arxiv.org/abs/2308.08155 and https://github.com/microsoft/autogen | Full arXiv PDF and official repository README checked. | Conversable agents, conversation programming, human/tool/LLM role composition, termination, executor, safeguard, and oversight patterns. |

If a user needs exact reproduction, re-check the latest paper version,
appendices, repository state, data licenses, prompt/config files, benchmark
instances, and evaluator scripts.

## Target Taxonomy

Choose the target by the work mechanism being simulated:

- **Organizational behavior dynamics**: roles, circles, meetings, delegation,
  workload, stress, task completion, competence, social networks, and governance
  routines inside a defined organization.
- **Long-horizon organizational coherence**: goals propagate through hierarchy,
  tasks depend on prior outputs, artifacts accumulate, incidents arrive, and
  later decisions must remain consistent with earlier plans and traces.
- **Organized group decision behavior**: a corporation, agency, party, or other
  organized group is modeled as the decision unit using historical
  context-decision records. Do not treat this as individual employee behavior.
- **Team coordination in task environments**: agents with roles communicate,
  move, perceive local state, act in a shared environment, and produce team and
  individual performance metrics.
- **Human-AI collaboration experiments**: human and AI collaborators work in a
  shared task workspace; the research target is communication, delegation,
  recognition, quality, productivity, and output diversity.
- **Enterprise workflow and web-task evaluation**: agents interact with
  enterprise UI, tickets, forms, knowledge bases, service catalogs, coworker
  messages, code repositories, files, and validators.
- **Software-company workflow simulation**: roles produce structured handoff
  artifacts through design, implementation, testing, review, and documentation
  phases.
- **Workflow orchestration mechanisms**: conversation patterns, SOPs,
  termination conditions, human oversight, executor/safeguard roles, and
  artifact feedback. Use these only when they serve an organization or workflow
  research target.

Use the governance guide when formal public authority, elections, regulation, or
public accountability are the mechanism. Use the city guide when the team is
situated in a spatial emergency, urban, or mobility environment. Use the
communication guide when platform visibility, diffusion, or audience effects are
central. Use economics/finance guides when the organization is mainly a firm,
bank, market participant, or macro actor in an economic mechanism.

## Loading Order

For organization, workflow, collaboration, and team behavior work, load only the
files needed for the target:

1. `research-design.md`
2. `environment-design.md`
3. `agent-design.md`
4. `step-dsl.md`
5. `simulation-paper-distillation.md` when adapting a paper
6. This guide
7. `governance-institution-public-policy-simulation-design.md` when role
   authority, compliance, sanctions, public legitimacy, or accountable
   institutional decisions are central
8. `city-community-emergency-simulation-design.md` when the team is situated in
   a spatial, urban, mobility, traffic, or emergency environment
9. `communication-social-media-simulation-design.md` when workplace
   communication is mediated by public feeds, audience visibility, rumors, or
   platform interventions
10. `economics-finance-simulation-design.md` when firm, labor, market, banking,
    or macroeconomic mechanisms are central
11. `run-monitor-analyze.md` when planning repeated runs, output tables,
    validators, trace replay, or qualitative review
12. Source files only when implementing or debugging a concrete env: start from
    `src/society0/env/`, `src/society0/environment.py`,
    `src/society0/schedule.py`, and `src/society0/agent/core.py`

Load this guide when the user mentions organization, management, workflow,
collaboration, teamwork, team behavior, human-AI team, workplace, enterprise,
knowledge work, task automation, digital worker, coworker, delegation, role
hierarchy, holacracy, software company, ChatDev, MetaGPT, AutoGen as a
workflow mechanism, TheAgentCompany, WorkArena, BrowserGym, CareerAgent,
TaskWeave, GROVE, organized group behavior, VirT-Lab, Pairit, task trace,
artifact handoff, SOP, or long-horizon organizational dynamics.

## Society0 Construction Rules

### Put The Organization In The Environment

Represent organization structure as env state or experiment config:

- role taxonomy, reporting lines, circles, teams, departments, or group entity
  labels.
- permissions: who can view, assign, approve, edit, merge, close, escalate, or
  override each work item.
- task graph: goals, tickets, subtasks, dependencies, deadlines, assignees,
  status, blockers, priority, and acceptance criteria.
- artifact registry: documents, code, designs, decisions, meeting notes,
  comments, files, evaluation outputs, and version history.
- communication channels: direct messages, team chat, meeting transcript,
  comments, review threads, announcements, and handoff notes.
- tool/workspace state: browser pages, forms, service catalogs, knowledge-base
  articles, repositories, files, dashboards, and external incidents.
- records: action log, message log, artifact diffs, validator results,
  partial-credit checkpoints, dependency satisfaction, and review outcomes.

Do not hide these mechanics in prompt prose. If task dependencies, approval
rights, or artifact handoffs matter, make them inspectable and replayable.

### Separate Work Actions From Measurement

Use `instruct(...)` with typed env actions for work:

- `claim_task(task_id, rationale)`
- `delegate_task(task_id, assignee_id, reason)`
- `send_message(channel_id, content, target_ids)`
- `submit_artifact(task_id, artifact_type, content, references)`
- `review_artifact(artifact_id, decision, comments)`
- `request_clarification(task_id, question)`
- `approve_or_reject(item_id, decision, rationale)`
- `use_tool(tool_name, arguments)` only when the env exposes a bounded tool
- `close_task(task_id, evidence)` as a terminal action only after validation or
  required evidence succeeds

Use `interview(...)` for measurement:

- perceived workload, stress, trust, coordination quality, fairness, clarity,
  role conflict, psychological safety, or retrospective explanations.
- post-simulation interviews after the work round has finished.
- human-AI recognition or perceived partner identity checks when the study
  design includes them.

Do not expose ordinary work actions during an interview by default. Do not save
memory from interviews unless the study explicitly treats the interview as a new
experience that should affect later behavior.

### Preserve Hierarchy, Dependency, And Trace Memory

Long-horizon organization studies fail when each turn is plausible but the
organization forgets prior commitments. Preserve:

- role consistency: each agent sees only the responsibilities, permissions, and
  reporting links relevant to its role.
- intent propagation: high-level goals become subtasks with explicit parent
  links and expected artifacts.
- dependency satisfaction: a task cannot be validly completed until required
  prior artifacts or external evidence exist.
- trace memory: later prompts should retrieve relevant prior decisions,
  artifacts, blockers, and validator outcomes.
- incident adaptation: external changes should update the task graph and be
  visible only to roles that would receive them.
- auditability: each final output should cite the trace records or artifacts it
  used, not only the agent's private reasoning.

In Society0, keep `memory=True` for LLM work rounds unless the experiment is
explicitly a no-memory baseline. Add environment-owned trace tables as well;
agent memory and env records solve different problems.

### Make Communication A Hosted Channel

Communication is a work artifact, not incidental chat. Define:

- who can speak to whom.
- whether messages are public, team-only, manager-only, or private.
- whether messages can mention tasks, artifacts, priorities, or deadlines.
- whether unread messages, notifications, and summaries appear in FoV.
- whether a meeting has an agenda, turn order, facilitator, decision rule, and
  minutes.
- whether task-oriented, interpersonal, delegation, conflict, or recognition
  codes will be computed later.

For software-style workflows, prefer structured handoff artifacts over idle
role-play conversation. For team-dynamics research, preserve the raw message log
and code it separately from final performance.

### Keep Human And Tool Boundaries Explicit

If humans, tools, or validators participate:

- model humans as real participants, simulated agents, or external evaluators
  explicitly. Do not blur categories.
- expose tools through env actions with argument validation and logged outputs.
- include executor, verifier, or safeguard roles only when the workflow needs
  them and their authority is defined.
- use completion or terminal actions only when the semantic endpoint succeeds,
  such as validated submission, approval, or ticket closure.
- keep human override and stop conditions visible for consequential workplace
  tasks.

## Paper-Derived Patterns

### CareerAgent: Holacracy And Organizational Behavior Dynamics

Use this pattern when the question is how decentralized roles, circles,
competence, meetings, workload, and stress interact over weeks of organizational
work.

Source support:

- Supported by paper: CareerAgent uses construction, execution, and evaluation
  phases; models individuals, organizations, tasks, and meetings; simulates a
  holacracy over eight weeks with weekly rounds and multiple tasks per round;
  analyzes stress, work completion, circle structure, workload, and
  communication networks.
- Supported by official code/config: Unknown or unavailable in this pass.
- Inference for Society0 mapping: represent holacracy as env-owned roles,
  circles, policies, meeting routines, task queue, workload state, and network
  records.

Society0 scaffold:

- setting: a bounded organization with circles, roles, task types, meeting
  cadence, competence attributes, and workload/stress state.
- FoVs: current role duties, circle membership, open tasks, meeting agenda,
  prior assignments, visible teammate availability, and relevant policies.
- actions: accept task, decline with reason, request support, delegate,
  coordinate in meeting, update task status, propose circle change, and report
  workload.
- hosted constraints: role permissions, weekly task issuance, meeting order,
  workload capacity, stress update rule, circle membership, and task completion
  scoring.
- records: task assignments, completion evidence, meeting transcript, circle
  changes, communication network, workload, stress, and individual/team metrics.
- measurements: average task completion, workload distribution, stress,
  centrality, circle size, sub-community formation, and qualitative explanations.

Do not turn holacracy into generic "flat organization" prompting. The useful
design move is the hosted work constitution: roles, duties, circles, meetings,
and task accountability are explicit before agents speak.

### TaskWeave: Long-Horizon Organizational Coherence

Use this pattern when a simulation must preserve organizational state over
months, many dependency chains, and accumulated process artifacts.

Source support:

- Supported by paper: TaskWeave formulates long-horizon organizational
  simulation as a memory-centered coordination problem; uses explicit role
  hierarchy, delegation topology, Formulate-Partition-Diagnose-Align planning
  states, dependency-aware execution, trace memory, tools, and external incident
  injection in a year-long IT company simulation.
- Supported by official code/config: the official repository README exposes
  company and role configuration examples and a company simulation entrypoint.
- Inference for Society0 mapping: implement planning state and trace memory as
  env tables plus Society0 memory retrieval for each LLM work round.

Society0 scaffold:

- setting: organization background, hierarchy, departments, role agents, annual
  or quarterly objectives, recurring planning cadence, and incident stream.
- FoVs: role-local goals, parent plan, assigned subtasks, dependencies, prior
  artifacts, incident notices, tool outputs, and unresolved blockers.
- actions: formulate plan, partition task, diagnose blocker, align plan,
  assign/delegate, execute work item, attach evidence, update trace, and
  escalate.
- hosted constraints: task graph, parent-child plan links, dependency rules,
  deadline windows, role permissions, incident visibility, and validator rules.
- records: planning-state versions, dependency graph, execution trace, artifact
  lineage, incident-response history, and coherence failures.
- measurements: role consistency, intent propagation, dependency satisfaction,
  groundedness in prior artifacts, completion rate, rework, and downstream
  utility of generated process artifacts.

Do not simulate a long organization as a sequence of independent chat turns.
When later work depends on earlier plans, earlier artifacts must be addressable
by ID and retrieved into FoV or memory.

### GROVE: Organized Group As Decision Unit

Use this pattern when the research target is how an organized group would decide
in a new situation based on historical group behavior.

Source support:

- Supported by paper: GROVE formalizes organized group behavior simulation as
  predicting decisions from context-decision records; covers 44 entities, 8,052
  records, and nine domains; evaluates consistency, initiative, scope,
  magnitude, and horizon; uses codified decision trees, traceable evidence
  nodes, temporal adapters, and cross-group transfer.
- Supported by official data: the paper lists a Hugging Face GROVE dataset link.
  A separate official code repository was not confirmed in this pass.
- Inference for Society0 mapping: implement an organized group either as a
  group-level LLM agent or as an env rule/behavior derived from records, but
  keep member-level simulations separate.

Society0 scaffold:

- setting: group entity, historical contexts, historical decisions, domain,
  time window, and new scenario.
- FoVs: relevant past context-decision examples, current situation, external
  constraints, and visible decision options.
- actions: choose decision type, specify scope, magnitude, initiative level,
  horizon, and rationale with evidence links.
- hosted constraints: chronological train/test split, data-vintage boundary,
  decision taxonomy, evidence-node provenance, and adapter update rules.
- records: retrieved examples, inferred decision rule, predicted decision,
  evidence links, evaluator dimensions, and drift/adaptation results.
- measurements: five-dimension decision similarity, time-split performance,
  cross-group transfer, qualitative traceability, and error taxonomy.

Do not infer the thoughts of executives or employees from group-level behavior.
This pattern models a group as an analytical decision unit. If the user wants
member interaction, use an organization or governance scaffold instead.

### VirT-Lab: Team Behavior In A Shared Environment

Use this pattern when the question is how team composition, roles, spatial
layout, or environmental pressure changes coordination and performance.

Source support:

- Supported by paper: VirT-Lab lets users configure scenarios, agent
  attributes, entities, and 2D environments; agents move, communicate, and act;
  the system logs agent-environment interactions and produces team metrics,
  agent metrics, and post-hoc interviews. The paper demonstrates alignment with
  empirical evaluations and a user study.
- Supported by official code/config: Unknown or unavailable in this pass.
- Inference for Society0 mapping: implement the 2D environment as a custom env
  only when location, movement, shared objects, or team visibility matter.

Society0 scaffold:

- setting: map, rooms or zones, entities, hazards or tasks, team roles, scenario
  objective, and time limit.
- FoVs: local map view, visible entities, teammate locations or messages,
  role-specific instructions, and current objective status.
- actions: move, inspect, pick up, hand off, communicate, request help, mark
  complete, and submit final team decision.
- hosted constraints: movement rules, visibility radius, task prerequisites,
  role permissions, communication range, and event timing.
- records: movement trace, action trace, conversation log, task state changes,
  team outcome, agent outcome, and post-hoc interview table.
- measurements: completion, time, coordination quality, communication pattern,
  role coverage, handoff failures, and qualitative after-action explanations.

Do not use a spatial team environment when the first mechanism is only a survey
or role assignment. Start with `plain` unless movement, visibility, or shared
objects create the phenomenon.

### Pairit: Human-AI Collaboration Process Measurement

Use this pattern when the research question concerns how AI collaborators change
human teamwork, delegation, output quality, or diversity.

Source support:

- Supported by paper: Pairit randomized 2,234 participants into human-human and
  human-AI teams; logged messages, edits, selections, generated images,
  intermediate outputs, and API calls; measured productivity, text quality,
  image quality, field ad performance, task-oriented communication,
  interpersonal communication, delegation, AI recognition, and diversity
  collapse.
- Supported by official code/config: Unknown or unavailable in this pass.
- Inference for Society0 mapping: use Pairit as a measurement and experimental
  design reference, not as evidence that simulated teams can replace human
  collaboration experiments.

Society0 scaffold:

- setting: shared workspace, human/agent assignment condition, task artifacts,
  communication channel, tool actions, and output rubric.
- FoVs: current artifact, teammate messages, available tools, task goal,
  deadline, and partner identity condition if experimentally visible.
- actions: message, edit text, revise artifact, select asset, request
  generation, delegate component, accept suggestion, reject suggestion, and
  submit.
- hosted constraints: random assignment, partner-identity disclosure or
  concealment, equal action affordances where possible, time limit, and output
  rubric.
- records: full event stream, message codes, edit counts, delegation events,
  partner-recognition checks, artifact versions, quality ratings, and diversity
  measures.
- measurements: productivity, quality by modality, task orientation,
  interpersonal communication, delegation, recognition, homogeneity, and
  external/human evaluation where available.

Do not infer labor-market effects from simulated Pairit-like pilots. Use human
benchmarks and real evaluators before making claims about productivity or work
quality.

### TheAgentCompany And WorkArena: Enterprise Task Environments

Use this pattern when the target is whether an agent can complete realistic
professional tasks in enterprise software or a self-contained company setting.

Source support:

- Supported by paper: TheAgentCompany builds a self-contained software-company
  environment with workplace web apps, code, file storage, simulated coworkers,
  175 tasks, checkpoints, execution-based evaluators, and partial credit.
- Supported by official code/config: TheAgentCompany official repository README
  confirms the benchmark, self-hosted services, website, and setup materials.
- Supported by paper: WorkArena uses BrowserGym observations/actions for
  ServiceNow-based knowledge-work tasks, with 33 tasks and 19,912 instances.
  WorkArena++ adds 682 compositional L2/L3 tasks, ticket-style instructions,
  validators, and oracle traces.
- Supported by official code/config: WorkArena official repository README
  confirms WorkArena and WorkArena++ papers, benchmark contents, BrowserGym
  integration, gated instances, and package setup.
- Inference for Society0 mapping: use these as evaluation-env patterns when
  building Society0 workplace environments with validators and task records.

Society0 scaffold:

- setting: company workspace, user roles, tickets, browser pages, repositories,
  file store, coworker directory, knowledge base, forms, service catalog, and
  dashboards.
- FoVs: current ticket or goal, browser observation, relevant files, coworker
  messages, task history, and previous tool outputs.
- actions: browse, fill form, search knowledge base, update ticket, write code,
  run command, upload file, message coworker, request information, and close
  task.
- hosted constraints: authentication state, UI affordances, task maximum steps,
  permission boundaries, tool limits, evaluator checkpoints, and database
  isolation between tasks.
- records: observations, actions, UI state, tool outputs, coworker messages,
  submitted artifacts, checkpoints, validator results, and partial-credit score.
- measurements: success, partial credit, step count, failure type, human
  baseline, oracle gap, repeated-seed variance, and task-category performance.

Do not treat a web-agent success rate as a social simulation result by itself.
For Society0, the design lesson is how to host a realistic work environment
with certifiable tasks and auditable traces.

### ChatDev, MetaGPT, And AutoGen: Workflow Mechanisms Only

Use this pattern when the user needs task-oriented collaboration patterns for a
workflow or software-company simulation.

Source support:

- Supported by paper and official code: ChatDev uses specialized software roles,
  chat-chain phases, design/coding/testing loops, and communicative
  dehallucination to reduce incomplete or inaccurate code generation.
- Supported by paper and official code: MetaGPT encodes SOPs into prompt
  sequences, uses role specialization, structured artifacts, publish-subscribe
  message sharing, and executable feedback.
- Supported by paper and official code: AutoGen defines conversable agents,
  conversation programming, flexible human/tool/LLM roles, executor agents,
  safeguard patterns, and termination/human-involvement choices.
- Inference for Society0 mapping: translate useful workflow mechanisms into env
  state, action schemas, channel rules, artifact records, validators, and
  terminal/completion semantics.

Society0 scaffold:

- setting: workflow phases, roles, handoff artifacts, message channels,
  executor/verifier roles, and completion conditions.
- FoVs: phase objective, relevant prior artifact, role responsibility,
  subscribed messages, test output, and review comments.
- actions: publish structured message, produce artifact, request clarification,
  execute test, review result, revise artifact, approve handoff, and terminate
  phase after successful criteria.
- hosted constraints: SOP sequence, role subscriptions, artifact schema,
  maximum action calls, required actions, executable feedback, and safety
  review.
- records: message pool, artifact versions, test logs, review outcomes, phase
  transitions, and termination reason.
- measurements: artifact completeness, execution success, rework, handoff
  failures, hallucinated dependencies, idle chatter, and cost/latency.

Do not import a whole framework concept into Society0 as the research design.
Ask what organization, work, or collaboration mechanism the framework teaches,
then encode that mechanism in the environment and step loop.

## Dirty-Work Triage

Can do now:

- draft role, permission, task, artifact, communication, and validator schemas.
- define FoVs and typed actions for a small workflow pilot.
- build process logs, message tables, artifact lineage, and metric tables.
- create rule-based baselines and no-memory/no-hierarchy ablations.
- write qualitative coding rubrics for communication, delegation, role conflict,
  and coordination failures.

Need user input:

- real organization, team, or workflow boundaries.
- whether the simulation uses fictional, public, synthetic, or private
  workplace data.
- acceptable claims, consent/privacy constraints, and human review process.
- role definitions, task acceptance criteria, performance rubrics, and what
  counts as a valid artifact.
- whether human participants, simulated agents, or external evaluators are part
  of the study.

Optional external pipeline:

- private task logs, enterprise UI snapshots, calendar data, code repositories,
  file stores, or ticket exports.
- data de-identification, access-control review, and secure artifact storage.
- UI/browser harness, validators, oracle traces, or external human quality
  ratings.
- process-mining, network analysis, artifact similarity, and qualitative coding
  scripts.

Society0 scaffold impact:

- private or sensitive data should enter through explicit env resources or
  preprocessed artifacts, not prompt text pasted into the guide.
- hidden treatment labels belong in `properties`, step params, or output tables,
  not visible agent state.
- validators and task acceptance criteria should be env rules or analysis
  scripts so results can be recomputed from traces.
- when tool or browser actions are essential, expose bounded actions and log
  observations, arguments, outputs, and errors.

## Scaffold Checklist

Before running an organization or workflow simulation, specify:

- research target: organization dynamics, group decision, team coordination,
  human-AI collaboration, enterprise task completion, or software workflow.
- setting: fictional or empirical organization, team size, roles, hierarchy,
  time unit, run length, task domain, and data vintage.
- env state: roles, permissions, tasks, dependencies, artifacts, channels,
  tools, calendars, incidents, validators, and metrics.
- agents: role personas, expertise, authority, goals, memory, hidden labels, and
  whether a role is human, LLM, rule-based, or evaluator-only.
- FoVs: role-local tasks, messages, artifact links, tool outputs, prior traces,
  deadlines, and aggregate status visible to each role.
- actions: typed work actions, communication actions, review actions,
  delegation actions, tool actions, and terminal/completion endpoints.
- run loop: planning cadence, assignment, execution, communication, review,
  validation, incident injection, memory update, and measurement.
- records: full action log, message log, artifact lineage, task graph, trace
  memory table, validator outputs, interviews, and qualitative notes.
- claims: what the simulation may support, what needs human benchmark evidence,
  and what cannot be inferred.

## Baselines, Ablations, And Validation

Minimum baselines:

- rule or scripted workflow baseline with no LLM interpretation.
- single-agent or flat-team baseline when hierarchy/delegation is the treatment.
- no-memory or no-trace baseline only as an explicit ablation.
- no-communication or restricted-communication baseline when teamwork is the
  treatment.
- oracle/human baseline for enterprise tasks when available.

Useful ablations:

- remove role hierarchy while keeping tasks fixed.
- remove dependency-aware trace retrieval while keeping agent memory.
- remove agent memory while keeping env traces.
- swap public aggregate status for role-local FoV.
- compare free chat with structured handoff artifacts.
- vary task load, incident frequency, deadline pressure, and permission rules.
- compare human-AI recognition/disclosure conditions when ethically approved.

Validation checks:

- task validity: validators can recompute success or partial credit from logs.
- process validity: action traces satisfy dependencies, permissions, and time
  order.
- role validity: agents do not act outside role authority or see hidden labels.
- artifact validity: human or expert evaluators review important outputs.
- communication validity: coding rubrics distinguish task-oriented,
  interpersonal, delegation, conflict, and recognition messages.
- coherence validity: later decisions cite or use prior plans, artifacts, and
  evidence rather than contradicting them.
- sensitivity: repeat seeds, compare providers/models when feasible, and report
  failure modes rather than only best runs.
- privacy and safety: redact workplace secrets, personal data, credentials, and
  sensitive performance information before sharing artifacts.

## Reproduction Boundaries

- Exact workplace reproduction often requires private tasks, internal tools,
  employee data, or proprietary rubrics. If unavailable, label the run as a
  synthetic or approximate mechanism study.
- Human-AI collaboration evidence from Pairit is empirical process evidence; it
  does not validate a fully simulated human workforce.
- Enterprise benchmarks evaluate agent task execution in hosted software; they
  are not direct evidence about organizational productivity, labor value, or
  worker replacement.
- Group-level decision prediction should not be interpreted as the beliefs,
  motives, or welfare of individual group members.
- Software-company frameworks show useful role and artifact mechanics, but their
  results do not generalize automatically to management, teams, public
  institutions, or physical work.
- Do not use simulated worker outputs for consequential employment decisions.
  Keep simulations in research, training, or design-support settings unless a
  separate governance process authorizes a bounded deployment.

## Failure Modes

- **Generic MAS drift**: writing about agents talking to agents without a real
  organization, task environment, artifact, or validator.
- **Prompt-only workflow**: placing role hierarchy, permissions, or dependencies
  in prose instead of env state.
- **Local plausibility, global incoherence**: each turn sounds reasonable but
  later work forgets prior plans, decisions, artifacts, or blockers.
- **Unbounded tool autonomy**: agents can browse, edit, or execute without
  explicit action schemas, permission checks, logs, or stop conditions.
- **Survey/work confusion**: using `interview` as if it were work behavior, or
  exposing work actions during measurement.
- **Hidden surveillance assumptions**: designing workplace simulations that
  imply employee monitoring or evaluation without consent and governance.
- **False productivity claims**: treating simulated task completion as evidence
  of real organizational productivity without human benchmarks and external
  validation.
- **Role leakage**: agents see hidden treatment labels, global evaluator
  answers, private coworker information, or task solutions.
- **Artifact opacity**: final metrics cannot be recomputed because messages,
  tool outputs, task states, and artifact versions were not recorded.
- **Overgeneralized frameworks**: copying ChatDev, MetaGPT, or AutoGen as a
  universal design instead of extracting bounded workflow mechanisms.
