# Education, Learning, And Classroom Simulation Design

Use this guide for Society0 simulations about classroom learning, tutoring, teacher scaffolding, student misconceptions, peer learning, informal classroom social dynamics, and educational intervention rehearsal. Load it after `founder-experience.md`, `research-design.md`, `environment-design.md`, `agent-design.md`, and `step-dsl.md`; also load `simulation-paper-distillation.md` when adapting a paper.

This guide is not a generic education-agent reference. Use it only when learning state, classroom interaction, teacher action, peer relation, or educational assessment is the simulation target.

## Evidence Map

| Source | Evidence status | Society0 lesson |
| --- | --- | --- |
| AgentSchool: An LLM-Powered Multi-Agent Simulation for Education, arXiv:2605.30144, https://arxiv.org/abs/2605.30144 | Supported by paper. Official code URL appears in the paper, but code/config details were not verified in this pass. | Treat learning as state transition inside a classroom scene: student knowledge, misconceptions, teacher scaffolds, informal peer relations, records, and assessments must be hosted and inspectable. |

## Domain Stance

Model education simulations as **learning-field state transitions**:

```text
classroom / learning field -> bounded student and teacher FoVs
-> teaching, response, peer action, or assessment action
-> hosted updates to knowledge, misconceptions, participation, and relations
-> auditable learning traces -> cautious educational interpretation
```

The environment owns curriculum objects, classroom scene, resources, rules, grouping, task sequence, assessment prompts, peer network, participation records, and state-update procedures. LLM agents own situated interpretation: explanation, question asking, confusion, reasoning, social meaning, teacher diagnosis, scaffolding choice, and reflection.

Do not make "student" a static persona with a final answer. A student agent needs mutable learning state if the claim concerns growth, misconception repair, participation, or social belonging. Do not let a teacher agent merely produce a lesson plan; connect teacher moves to later student-state evidence.

## Target Taxonomy

- **Lesson simulation**: teacher and students interact around a defined knowledge point or task; measure mastery, weak nodes, misconceptions, and scaffold effect.
- **Tutoring or adaptive support**: one teacher/tutor agent diagnoses readiness and chooses explanation, hint, question, feedback, or task redesign.
- **Peer learning and classroom social life**: students choose partners, initiate conversation, exchange knowledge, exclude peers, form cliques, or become opinion leaders.
- **Educational intervention rehearsal**: new AI tutor, classroom format, grouping rule, or assessment pressure is tested as a scenario, not deployed as policy proof.

## Society0 Construction Rules

Use this split:

| Component | Society0 location |
| --- | --- |
| Curriculum graph, tasks, scene, seating/grouping, schedule, assessment rubrics, resources, classroom rules | env state, env config, rules, or step params |
| Student knowledge nodes, misconceptions, confidence, attention, participation, anxiety, social position | agent `state` when visible to self; `properties` for hidden sampling or benchmark labels |
| Teacher subject knowledge, pedagogical principles, prior lesson notes, reflected teaching experience | agent state/memory or env resources |
| Lesson prompt, peer messages, visible group work, previous teacher feedback, assessment question | FoV |
| Explain, ask, answer, hint, demonstrate, group, give feedback, challenge misconception, choose peer, invite, exclude, support | `instruct` with typed env actions |
| Mastery rating, misconception coding, learning reflection, post-lesson survey | `interview` or analysis step |
| Mastery updates, relation updates, participation counters, assessment scoring | env rules or code steps |

## Paper-Derived Patterns

### Student State Must Be Inspectable

Supported by paper:
AgentSchool represents student learning with dialogue memory, weighted knowledge graphs, thinking-workflow pools, learning parameters, and explicit misconception objects. Its lesson experiment compares differentiated mastery and misconception traces against a baseline simulator.

Inference for Society0 mapping:
Store learning state as records that can be inspected after each lesson:

```text
knowledge_node:
  agent_id
  concept_id
  mastery
  evidence_event_id
  uncertainty
misconception:
  agent_id
  concept_id
  claim
  persistence
  first_seen_tick
  last_challenged_tick
```

Fidelity:
Approximate unless the exact AgentSchool graph construction, update equations, prompt templates, and analysis procedure are reproduced.

### Teacher Actions Should Target Readiness

Supported by paper:
AgentSchool teacher agents plan, scaffold, diagnose, and reflect using the Zone of Proximal Development. Teacher reflection records what was attempted, what evidence was observed, which misconceptions persisted, and which scaffolds appeared useful.

Society0 mapping:
Expose teacher FoVs that include student-visible evidence, not hidden answer keys unless the teacher is meant to know them. Use actions such as `explain`, `ask_question`, `give_hint`, `challenge_misconception`, `assign_group`, and `redesign_task`. After the action, a rule updates lesson records and student-state evidence.

### Informal Peer Scenes Are Educational State

Supported by paper:
AgentSchool treats recess conversation, gossip, hobby groups, spontaneous topics, peripheral participation, clique formation, aggressor-induced cohesion, and opinion-leader emergence as part of the educational ecosystem.

Society0 mapping:
If peer dynamics matter, model the peer graph as env state. Record initiations, replies, exclusions, supports, and knowledge exchanges. Measure social participation separately from mastery so the run can show a student who understands a concept but becomes isolated, or a central peer who mediates adoption of a classroom intervention.

## Dirty-Work Triage

Can do now:
- Build a small classroom pilot with 3-8 students, one teacher, one topic, typed teaching/response actions, and mastery/misconception records.
- Draft concept nodes, assessment questions, and rubric tables from user-supplied curriculum material.

Need user input:
- Target learners, curriculum standard, construct validity for mastery, acceptable teacher intervention, and whether minors or protected educational data are involved.

Optional external pipeline:
- Import lesson materials, human assessment data, sociometric observations, or classroom transcripts when licensed and approved.

Society0 scaffold impact:
- Start with `plain` for a lesson pilot. Write a custom env when grouping, seating, peer network, resource access, or reusable classroom records become central.

## Validation And Boundaries

- Compare to human classroom evidence before claiming educational validity.
- Treat hidden knowledge state as a modeling instrument, not proof of real cognition.
- Run ablations for teacher access to student state, memory, peer network, and scaffold policy.
- Report model/provider, prompt version, state-update rules, and scoring procedure.
- Do not use simulated students as a substitute for learner participation, safety review, teacher expertise, accessibility review, or educational policy evaluation.
