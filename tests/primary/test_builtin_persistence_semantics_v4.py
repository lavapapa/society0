"""内置环境的 v4 持久化语义合同。

这些测试先冻结环境状态树的公开事实边界：混合增长对象必须拆成
append-only 事实、replaceable 投影和 transient 缓存；声明编译器不能
依赖保存时扫描历史来猜测字段语义。
"""

from __future__ import annotations

import copy

import pytest

from society0.core_data import World
from society0.incremental_checkpoint import PersistenceKind, PersistenceSchema
from society0.env.plain.env import PLAIN_STATE_SCHEMA
from society0.env.round_robin.env import ROUND_ROBIN_STATE_SCHEMA
from society0.env.social_network.env import SOCIAL_NETWORK_STATE_SCHEMA, SocialNetworkEnv


pytestmark = pytest.mark.primary


def _compile(schema: dict) -> PersistenceSchema:
    return PersistenceSchema.compile(schema, root_path=("environment", "state"))


def _rule(schema: PersistenceSchema, *parts: str):
    return schema.resolve(("environment", "state", *parts))


def test_plain_environment_keeps_an_empty_explicit_state_schema():
    assert PLAIN_STATE_SCHEMA["properties"] == {}
    assert PLAIN_STATE_SCHEMA["additionalProperties"] is False

    world = World()
    world.environment_data["state"] = {"user_value": 1}
    with pytest.raises((TypeError, ValueError), match="user_value"):
        world.configure_persistence(_compile(PLAIN_STATE_SCHEMA))


def test_round_robin_state_is_split_by_persistence_semantics():
    schema = _compile(ROUND_ROBIN_STATE_SCHEMA)

    # 配置、分组和当前激活缓存可由配置/调度重建，不进入 recoverable delta。
    assert _rule(schema, "config").kind is PersistenceKind.TRANSIENT
    assert _rule(schema, "groups").kind is PersistenceKind.TRANSIENT
    assert _rule(schema, "pairing_active_pairs").kind is PersistenceKind.TRANSIENT
    assert _rule(schema, "active_messages").kind is PersistenceKind.TRANSIENT

    # 当前投影按 Agent/字段替换，历史配对和消息事实只能追加。
    assert _rule(schema, "pairing_current_round").kind is PersistenceKind.REPLACEABLE
    assert _rule(schema, "pairing_total_rounds").kind is PersistenceKind.REPLACEABLE
    assert _rule(schema, "pairing_current_partner", "agent-a").kind is PersistenceKind.REPLACEABLE
    assert _rule(schema, "conversation_current", "agent-a").kind is PersistenceKind.REPLACEABLE
    assert _rule(schema, "pairing_completed_pairs").kind is PersistenceKind.APPEND_ONLY_LIST
    assert _rule(schema, "conversation_partner_history").kind is PersistenceKind.APPEND_ONLY_LIST
    assert _rule(schema, "message_facts").kind is PersistenceKind.APPEND_ONLY_LIST
    assert _rule(schema, "message_counter").kind is PersistenceKind.REPLACEABLE


def test_social_network_state_splits_post_notification_and_recommendation_semantics():
    schema = _compile(SOCIAL_NETWORK_STATE_SCHEMA)

    assert _rule(schema, "post_creation_facts").kind is PersistenceKind.APPEND_ONLY_MAP
    assert _rule(schema, "post_interaction_facts").kind is PersistenceKind.APPEND_ONLY_LIST
    assert _rule(schema, "post_projection", "post-1").kind is PersistenceKind.REPLACEABLE
    assert _rule(schema, "author_post_facts").kind is PersistenceKind.APPEND_ONLY_LIST
    assert _rule(schema, "post_counter").kind is PersistenceKind.REPLACEABLE
    assert _rule(schema, "trending_post_ids").kind is PersistenceKind.TRANSIENT
    assert _rule(schema, "recommended_posts", "agent-a").kind is PersistenceKind.REPLACEABLE
    assert _rule(schema, "notification_facts").kind is PersistenceKind.APPEND_ONLY_LIST
    assert _rule(schema, "notification_state", "notif-1").kind is PersistenceKind.REPLACEABLE


def test_split_state_proxy_captures_only_current_fact_and_projection(tmp_path):
    schema = _compile(SOCIAL_NETWORK_STATE_SCHEMA)
    state = {
        "post_creation_facts": {},
        "post_interaction_facts": [],
        "post_projection": {},
        "author_post_facts": [],
        "post_counter": 0,
        "trending_post_ids": [],
        "recommended_posts": {},
        "notification_facts": [],
        "notification_state": {},
    }
    world = World(event_log_path=str(tmp_path / "events.jsonl"))
    world.environment_data["state"] = copy.deepcopy(state)
    world.configure_persistence(schema)
    world.begin_persistence_tick(1)
    proxy = world.create_environment_state_proxy()

    proxy["post_creation_facts"]["post-1"] = {"post_id": "post-1", "author_id": "agent-a"}
    proxy["post_interaction_facts"].append({"kind": "like", "post_id": "post-1", "agent_id": "agent-b"})
    proxy["post_projection"]["post-1"] = {"view_count": 1}
    proxy["post_counter"] = 1
    proxy["trending_post_ids"] = ["post-1"]

    delta = world.seal_persistence_tick()
    assert [tuple(entry["path"]) for entry in delta.replacements] == [
        ("environment", "state", "post_projection", "post-1"),
        ("environment", "state", "post_counter"),
    ]
    assert [tuple(entry["path"]) for entry in delta.appends] == [
        ("environment", "state", "post_creation_facts"),
        ("environment", "state", "post_interaction_facts"),
    ]
    assert all("trending_post_ids" not in entry["path"] for entry in (*delta.replacements, *delta.appends))


@pytest.mark.asyncio
async def test_social_post_vectors_use_tick_branch_epoch_view_and_shadow(tmp_path):
    class FakeCollection:
        def __init__(self):
            self.upserts = []
            self.where = None

        def upsert(self, **kwargs):
            self.upserts.append(kwargs)

        def query(self, *, query_embeddings, n_results, include, where=None):
            self.where = where
            return {
                "ids": [["post-1", "post-1", "post-2"]],
                "distances": [[0.0, 0.2, 0.1]],
                "metadatas": [[
                    {
                        "post_id": "post-1",
                        "logical_post_id": "post-1",
                        "branch_id": "main",
                        "created_step": 7,
                        "visible_until_step": 2**63 - 1,
                        "write_epoch_id": "main:7:active",
                    },
                    {
                        "post_id": "post-1",
                        "logical_post_id": "post-1",
                        "branch_id": "source",
                        "created_step": 4,
                        "visible_until_step": 2**63 - 1,
                        "write_epoch_id": "source:4:committed",
                    },
                    {
                        "post_id": "post-2",
                        "logical_post_id": "post-2",
                        "branch_id": "main",
                        "created_step": 8,
                        "visible_until_step": 2**63 - 1,
                        "write_epoch_id": "main:8:unpublished",
                    },
                ]],
            }

    world = World(event_log_path=str(tmp_path / "events.jsonl"))
    world.environment_data = {
        "type": "social_network",
        "config": {"social_media": {"recommendation": {"use_embedding_similarity": True}}},
        "state": {
            "post_creation_facts": {
                "post-1": {
                    "post_id": "post-1",
                    "author_id": "author",
                    "content": "post",
                    "tags": [],
                    "created_tick": 7,
                }
            },
            "post_projection": {"post-1": {"view_count": 0, "special_tags": []}},
        },
    }
    world.step = 7
    world._memory_branch_id = "main"
    world._memory_branch_lineage = [("source", 5)]
    world._active_memory_epoch_id = "main:7:active"
    world._committed_memory_epoch_ids = {"source:4:committed"}
    env = SocialNetworkEnv(world)
    collection = FakeCollection()
    env._post_collection = collection

    async def embed(texts, metadata=None):
        return {"result": [[0.1, 0.2, 0.3] for _ in texts], "model": "fake", "dimensions": 3}

    env._embed_call = embed
    await env._embed_and_store_posts_batch(
        [{"post_id": "post-1", "content": "post", "tags": [], "created_tick": 7, "author_id": "author"}]
    )
    metadata = collection.upserts[0]["metadatas"][0]
    assert metadata["created_step"] == 7
    assert metadata["visible_until_step"] == 2**63 - 1
    assert metadata["branch_id"] == "main"
    assert metadata["source_branch_id"] == "source"
    assert metadata["write_epoch_id"] == "main:7:active"

    env._build_agent_preference_text = lambda _agent: "Social feed preference"
    scores = await env._semantic_similarity_scores(
        type("AgentRef", (), {"id": "viewer"})(),
        [{"post_id": "post-1"}],
    )
    assert scores == {"post-1": 1.0}
    assert len(collection.where["$or"]) == 2
    where_text = str(collection.where)
    assert "created_step" in where_text and "visible_until_step" in where_text
