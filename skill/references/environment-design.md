# Environment Design

Use this reference when the user asks what kind of world, platform, institution, media setting, interaction scene, or experimental condition to model.

## Why Environment Comes First

In Society0, an environment is not scenery. It is the experiment's social situation:

- what agents can see through FoVs.
- what agents can do through actions.
- what records interactions leave behind.
- what constraints, institutions, platform rules, or physical/social context shape behavior.
- what state changes the simulation can measure over time.

Agents are participants inside the environment. A good experiment usually starts by defining the environment, then asks what agent types belong there. For LLM-based simulations, this matters because the environment supplies realism boundaries: visible evidence, allowed behavior, social affordances, and reminders that prevent the model from inventing context.

Use three concepts to design the environment:

- **FoVs**: the environment acts as a visibility machine. It decides what each agent sees, hears, reads, or receives as social evidence.
- **Empower**: the environment grants action possibilities. It decides who may post, reply, move, moderate, buy, vote, recommend, or stay read-only.
- **Hosting**: the environment hosts and constrains state that belongs to the situation, such as location, mute status, role permissions, resource access, exposure counters, or platform penalties.

These are not only implementation details. They are research variables. Recommendation rules, visibility windows, action permissions, and hosted constraints are often the actual mechanism being studied.

## Environment And Agents

The current runtime connects env and agents this way:

- `Society0` loads `environment.type`, `environment.config`, and `environment.state`.
- `World.get_environment()` instantiates the matching env class.
- Env capabilities registered with `@fov`, `@action`, and `@rule` become available to steps and LLM agents.
- `environment.agent_instruction` is injected into the LLM agent system prompt.
- FoV results are inserted into the user prompt for `instruct(...)` or `interview(...)`.
- Actions are exposed as callable tools during `instruct(...)`.
- Agent `state` appears in the LLM prompt. Do not put hidden treatment labels or ground-truth answers there.

Design implication: use env state and FoVs for social context; use agent state for what the agent should know about itself; use agent properties or run tables for researcher-only labels.

Boundary rules:

- The env may shape what agents can see and do.
- The env may update env-owned or hosted state through rules and actions.
- The env should not inspect an agent's private memory directly.
- The env should not rewrite stable persona unless the study explicitly models identity reconstruction and records that choice.
- Hidden treatment labels and ground-truth answers should stay in `properties`, step params, or output tables, not visible `state`.

## Logic Sources

Use "logic" to mean deterministic rule/behavior code:

- Env-provided `rule`: built into an environment to update environment/system state.
- Env-provided `behavior`: built into an environment to update or prepare individual agents in that environment.
- Experiment-specific `rule`: written for one study when the environment needs a custom system update.
- Experiment-specific `behavior`: written for one study when rule-based agents or baselines need custom deterministic behavior.

Examples:

```python
await ctx.rule("advance_round_robin_with_pairing", round_number=1)
await ctx.agents.where(archetype="rule").behavior("mark_conversation_participant", marker="ready")
```

Discover first:

```python
ctx.capabilities.names("rule")
ctx.capabilities.names("behavior")
```

## Built-In Environments

### `plain`

Use for:

- first runnable experiments.
- survey/interview prototypes.
- deterministic rule baselines.
- experiments where the step function directly updates state.

Behavior:

- no built-in FoVs or actions.
- no required config.
- `environment.state` can hold simple study variables.

Example:

```python
"environment": {"type": "plain", "state": {"topic": "misinformation"}}
```

### `social_network`

Use when platform visibility or interaction matters:

- posts, reposts, comments, likes, follows, notifications.
- recommendation feeds and trending posts.
- friend endorsement, social proof, diffusion, polarization, information exposure.
- experiments where FoV design is part of the mechanism.

Important config areas:

- `distribution.type`: `random`, `small_world`, `scale_free`, `complete`, or `cv_targeted`.
- `is_directed`: whether relationships behave like directed follows.
- `social_media.recommendation`: weights for chronology, engagement, similarity, and network proximity.
- `social_media.trending`: whether trending content is calculated and injected.
- `social_media.content_length_limit`: content length guard.

Useful FoVs include:

- `get_recommended_feed` / `recommended_feed`: personalized feed and follow suggestions.
- `get_trending_feed` / `trending_feed`: trending feed when enabled.
- `get_trending_posts` / `trending_posts`: current trending posts.
- `get_notifications` / `notifications`: interactions involving the current agent.
- `get_agent_profile` / `agent_profile`: profile and social statistics.

Useful actions include:

- `publish_post`
- `like_post`
- `comment`
- `repost`
- `follow`
- `unfollow`
- `get_post_details`

For prototypes, call `instruct(..., actions=None)` or `actions=["environment"]`. Narrow later by action name or short tag. Use `actions=["memory"]` only for memory actions.

### `round_robin_conversation`

Use when structured interaction protocol matters:

- pairwise conversation experiments.
- rotating interviews.
- deliberation, peer influence, group discussion, negotiation.
- designs where each agent must meet each other agent or each assigned partner.

Config:

- `group_size`: required, must divide the number of agents.
- `session_duration_minutes`: protocol metadata for the scenario.
- `pairing_strategy`: `standard`, `tournament`, or `custom`.
- `message_persistence`: whether messages persist across rounds.

Useful rules/actions/FoVs:

- `advance_round_robin_with_pairing` or `advance_round_robin`: start or advance rounds.
- `send_message_to_partner`: message current paired partner.
- `broadcast_to_group`: message group members.
- `get_conversation_fov`: current partner, history, and round progress.
- `get_group_fov`: group-level context.

## Designing Realistic LLM Scenes

For LLM-based agents, design the env around constrained evidence:

1. List what the agent could plausibly observe at that moment.
2. Encode that as one or more FoVs.
3. List what the agent could plausibly do.
4. Expose those as actions, not prose-only instructions.
5. Decide what is recorded as state, logs, tables, or memories.
6. Keep hidden variables out of visible agent state.
7. Use `interview(...)` for measurement and `instruct(...)` for behavior.

Example design move:

```text
Research phenomenon: friend endorsement changes credibility.
Environment: social_network.
FoV: recommended_feed includes post text, author, friend endorsements, and engagement counts.
Actions: like/comment/repost/follow.
Measurement: interview trust_score after exposure, with no ordinary actions.
Hidden condition: treatment/control stored in step params or researcher table, not agent state.
```

FoVs can be plain text or structured values. In current use, they should be designed as evidence that can be rendered into prompt text: feed items, notifications, partner messages, local observations, public rules, or institutional signals. Avoid giving the agent a global explanation of the experiment when a situated view is enough.

Actions should represent real affordances, not vague intentions. Prefer `publish_post`, `comment`, `follow`, `send_message`, `vote`, or `apply_for_job` over a broad "respond to society" action. The narrower the action, the easier it is to audit what happened.

Hosting is useful when the social situation should enforce constraints directly. Examples: a platform marks an agent as muted, a city env updates a resident's district, an organization env limits who can approve a decision, or a market env controls remaining inventory.

## Extending Or Writing A New Env

Read source first when adding a real env:

- `src/society0/environment.py`
- `src/society0/decorators.py`
- `src/society0/env/plain/env.py`
- `src/society0/env/social_network/env.py`
- `src/society0/env/round_robin/env.py`

Minimal pattern:

```python
from society0.core_data import ExecutionContext
from society0.decorators import action, env_type, fov, rule
from society0.environment import Environment

CONFIG_SCHEMA = {"type": "object", "properties": {}, "additionalProperties": True}
STATE_SCHEMA = {"type": "object", "properties": {}, "additionalProperties": True}


@env_type(
    type_name="news_room",
    config_schema=CONFIG_SCHEMA,
    state_schema=STATE_SCHEMA,
    agent_managed_fields_schema={"type": "object", "properties": {}},
    display_name="News Room",
    description="A news exposure environment for credibility experiments.",
)
class NewsRoomEnv(Environment):
    @property
    def agent_instruction(self) -> str:
        return "Only rely on articles shown in your FoV. Do not invent unseen sources."

    def initialize(self, agents, world):
        self.state.setdefault("articles", {})
        self.state.setdefault("exposures", [])

    @fov(description="Show articles currently visible to this participant.")
    async def news_feed(self, agent, env) -> str:
        return str(self.state.get("articles", {}))

    @action(description="Record that the participant shares an article.")
    async def share_article(self, context: ExecutionContext, article_id: str) -> str:
        agent_id = getattr(context.caller, "id", "unknown")
        self.state.setdefault("shares", []).append(
            {"agent_id": agent_id, "article_id": article_id, "step": context.step_number}
        )
        return "shared"

    @rule(description="Advance environment-level exposure counters.")
    async def update_exposure(self, context: ExecutionContext) -> str:
        return "updated"
```

For a core contribution, place the env under `src/society0/env/<name>/`, import/register it in `src/society0/env/__init__.py` if needed, add a primary-path test, and document its intended research use. For a one-off experiment, prefer a simple `plain` env plus code steps until the env abstraction is clearly needed.

## Env Design Checklist

- What is the setting: platform, organization, classroom, market, community, media field?
- What does each agent see at each tick?
- Which FoVs expose that evidence?
- What can agents do, and which actions implement those affordances?
- What is recorded for later analysis?
- What is intentionally hidden from agents?
- Which state belongs to env, which state belongs to agents, and which labels belong only to researcher outputs?
- What would make the first run too large, and what can be simplified?
- Which mechanism is being varied: visibility, permission, hosting constraint, agent heterogeneity, memory, or timing?
- What small run would produce one interpretable curve or table before adding realism?
