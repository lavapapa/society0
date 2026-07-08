# Founder Experience Notes

Use this reference before helping a user design a new Society0 simulation, especially when the user brings an ambitious domain idea, a large knowledge graph, a paper-derived world, an economic or institutional mechanism, or a prediction-oriented use case.

These notes are intentionally domain-neutral. They record reusable founder-level design lessons without preserving any project-specific assets, private examples, company lists, proprietary schemas, or sensitive implementation details.

## Core Stance

Society0 is not a traditional ABM framework with LLMs swapped in for rule functions. It is a language-mediated simulation framework where the environment hosts what can be seen, done, constrained, recorded, settled, and measured, while LLM agents interpret situated evidence and choose bounded actions inside that hosted world.

The most common failure is hardworking but misplaced structure: turning a user's idea into nodes, scalar states, propagation weights, taxonomies, and risk scores before proving that the simulated world has actors, observations, actions, consequences, and records that mean anything.

Before writing code, ask what must be true for the simulation to be legitimate:

- What evidence supports the world structure?
- What can be claimed from that evidence, and what must not be claimed?
- Which entities are actual decision-making subjects, and which are only processes, slots, resources, locations, products, topics, or graph nodes?
- Which consequences are mechanically hosted by the environment?
- Which decisions genuinely require LLM interpretation, planning, language, memory, or judgment?
- Which outputs can be recomputed from records rather than accepted as a final narrative?

## Evidence Boundary First

Start with the evidence boundary, not the agent list.

When the user provides a document, dataset, graph, website, benchmark, paper, or internal artifact, first classify it:

- relatively stable structure, such as roles, relationships, institutions, legal rules, platform constraints, or historical chronology.
- external observations, such as news, announcements, transactions, survey waves, prices, reports, logs, messages, or measurements.
- weak signals or generated text that may help construct hypotheses but should not become deterministic truth.
- missing information that limits what the simulation can claim.

Then state the interpretation boundary in plain research language. For example, an artifact may support a scenario, exposure, qualitative direction, or mechanism exploration, while not supporting causal identification, profit prediction, individual-level replication, or policy proof.

Do not let a rich artifact seduce the design into false completeness. A large graph does not imply every node should be an agent. A long taxonomy does not imply every label should become state. A generated explanation does not imply the environment should treat it as a measured parameter.

## Subject Layer Before Agent Layer

Do not equate every object in the domain with an agent.

First determine the subject layer:

- Who or what can perceive?
- Who or what can decide?
- Who or what can act?
- Who or what bears consequences?
- Which objects only mediate, constrain, store, route, or transform the process?

Many domains contain useful graph nodes, institutions, products, documents, locations, prices, roles, resources, or technical processes that should not become LLM agents. Represent them as environment state, records, resources, parameters, or deterministic placeholders until the design proves they need situated cognition.

LLM agents should be introduced where the study needs interpretation, language, strategic planning, social meaning, negotiation, judgment under incomplete information, memory, or explanation. Deterministic rules should own arithmetic, validation, accounting, settlement, scheduling, permissions, and physical or institutional constraints.

## Minimal Numbers, Maximal Semantics

Avoid over-structuring external information.

A useful rule is: keep numbers minimal and semantics rich. Some external information has hard numeric consequences that must update environment state, such as a quantity, date, capacity, price, budget, vote count, or deadline. Much other information is meaningful because it is text: announcements, rumors, policy language, arguments, warnings, explanations, commitments, uncertainty, or conflicting narratives.

Do not prematurely force semantic information into large event taxonomies with many fields such as confidence, direction, magnitude, duration, visibility, and type unless those fields are necessary for the environment to enforce consequences or for the study's measurement design.

A better pattern is:

```text
external material -> minimal numeric updates where unavoidable
                  -> semantic messages or local evidence in FoVs
                  -> agent interpretation and bounded action
                  -> environment-hosted consequences and records
```

The LLM's intelligence should be exercised on semantically rich situated evidence, not bypassed by giving the agent a pre-digested conclusion.

## Do Not Embed Macro Conclusions In Micro Design

Avoid placing a macro expectation directly inside each agent's visible state or prompt as if it were an individual belief or local observation.

Macro claims, aggregate shocks, institutional summaries, or expert narratives should be converted into:

- environment conditions that are genuinely global.
- public or role-specific information that agents can read.
- numeric constraints only where the mechanism requires them.
- researcher-only labels or step params when the agent should not know them.

If a micro agent should react to a broad development, make the reaction pass through FoV, memory, local state, action affordances, and hosted consequences. Do not make the prompt say the conclusion that the simulation is supposed to discover or stress-test.

## Environment-Hosted Consequences

A world-changing action should not be a mere structured opinion.

When an agent acts, the environment should record the action and later apply whatever deterministic consequences the study defines. The consequence may be immediate or delayed, but it should be hosted by the environment rather than left as prose in the agent's answer.

This matters because many failures look like successful runs:

- The model writes a plausible plan, but no state changes.
- A run completes, but no required action happened.
- A metric is printed, but it cannot be recomputed from records.
- A prompt tells the model the intended causal direction, so the output only mirrors the prompt.
- A propagation score moves, but no actor saw, decided, acted, or bore consequences.

For action-bearing behavior, prefer `instruct` with a narrow environment action set, required action or required action tags when the study demands behavior, and post-run inspection of successful action counts and action records. Use `interview` for measurement, not for hidden world-changing behavior.

## Avoid Traditional ABM Drift

Society0 can borrow ABM discipline, but it should not inherit ABM reflexes blindly.

Borrow:

- explicit mechanisms.
- transparent assumptions.
- environment constraints.
- baselines, ablations, and repeated runs.
- sensitivity checks.
- auditability.

Avoid:

- scalar propagation fields that substitute for social or institutional process.
- risk or utility scores that appear calibrated only because they have parameters.
- making every graph node an agent.
- using agent personas to compensate for a weak environment.
- turning external text into rigid taxonomy before proving which fields matter.
- treating a large run as stronger evidence than a small interpretable run.

If the mechanism can be fully expressed as a deterministic numerical transition, implement it as a rule. If the mechanism depends on meaning, ambiguity, attention, belief, persuasion, identity, negotiation, planning, or language, expose the right evidence and actions to LLM agents.

## Build In Stages

Use this order for new designs:

```text
evidence boundary
-> subject layer
-> environment-hosted state and constraints
-> FoVs and action affordances
-> smallest real step loop
-> pilot run with inspectable records
-> interpretation boundary
-> scale, ablations, and calibration
```

The first pilot should prove that the environment, FoVs, actions, records, and measurements are coherent. It should not try to prove the domain thesis.

Only scale after the pilot shows:

- agents see the intended evidence and not hidden labels.
- actions actually occur when required.
- deterministic consequences are applied in the intended order.
- errors can be explained as provider failure, configuration failure, or domain consequence.
- outputs are recomputable from tables, records, checkpoints, or other explicit artifacts.

## Use AI Intelligence Properly

Do not use prompts to hint at the desired answer. Use prompts, FoVs, and actions to create a situation where the model has to interpret evidence and make a bounded decision.

Good uses of LLM intelligence include:

- interpreting ambiguous text or conflicting accounts.
- planning under incomplete information.
- making role-specific tradeoffs.
- explaining reasons in natural language.
- reacting to communication, persuasion, or institutional signals.
- producing decisions that deterministic rules then validate and settle.

Poor uses include:

- asking the model to calculate deterministic mechanics.
- asking the model to invent missing measurements.
- placing the expected conclusion in the instruction.
- using long personas to replace state, FoV, action, and records.
- relying on a final narrative when the run artifacts do not support it.

## Boundary Discipline

Separate framework improvements from experiment-specific logic.

Generic Society0 improvements may belong in core when they improve the runtime for many domains: model-provider access, `step(ctx)` ergonomics, capability discovery, action-loop diagnostics, provider-neutral configuration, runtime summaries, or env-agnostic abstractions.

Experiment-specific worlds should usually start outside core, using `plain`, experiment registries, custom scripts, or a local custom environment. Promote an environment into core only after the mechanism is stable, reusable, documented, tested, and clearly not private to one study.

Do not commit secrets, proprietary source material, run artifacts, or private datasets into public docs. When extracting lessons from a real project, preserve the general design rule and remove domain-specific entities, examples, and sensitive details.

## Review Questions

Before implementing or approving a Society0 design, ask:

- What claim is this run allowed to support?
- What claim would be overreach?
- What evidence enters the world as structure, observation, message, or numeric constraint?
- Who is the real decision-making subject?
- What is hosted by the environment even if all LLM agents were replaced by rule agents?
- What does the LLM decide that cannot be replaced by a simple rule without changing the study?
- What action changes the world, and where is that change recorded?
- What deterministic rule applies consequences?
- What output table would let a skeptical researcher recompute the result?
- What must be checked before interpreting a completed run as meaningful?

If these questions cannot be answered, stop at a smaller pilot or a design note. Do not scale the run.

