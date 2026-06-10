import os
import sys
import tempfile
import random
from pathlib import Path

import pytest
import chromadb

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from simengine.agent.memory import Memory
from simengine.persistence import PersistenceManager


def _deterministic_vec(text: str, dim: int = 32):
    rng = random.Random(abs(hash(text)) % (10**9))
    return [rng.random() for _ in range(dim)]


async def _embed_call(texts, dimensions=512):
    return {
        "result": [_deterministic_vec(text, 32) for text in texts],
        "model": "test-embed",
        "dimensions": 32,
    }


@pytest.mark.asyncio
async def test_memory_shared_collection_isolation():
    prev_collection = os.environ.get("CHROMA_MEMORY_COLLECTION_NAME")
    os.environ["CHROMA_MEMORY_COLLECTION_NAME"] = "agent_memories_test"
    try:
        with tempfile.TemporaryDirectory(prefix="mem_shared_") as td:
            client = chromadb.PersistentClient(path=td)
            mem_a = Memory(
                agent_id="A0001",
                vector_client=client,
                embed_call=_embed_call,
                llm_call=None,
                embedding_dim=32,
            )
            mem_b = Memory(
                agent_id="A0002",
                vector_client=client,
                embed_call=_embed_call,
                llm_call=None,
                embedding_dim=32,
            )

            assert mem_a.collection_name == mem_b.collection_name == "agent_memories_test"

            text_a = "agent A0001 own memory"
            text_b = "agent A0002 own memory"
            await mem_a.add_episodic_memory(text_a, timestamp=1, importance=3.0, metadata={})
            await mem_b.add_episodic_memory(text_b, timestamp=1, importance=3.0, metadata={})

            got_a = await mem_a.retrieve(query=text_a, top_k=5, current_step=1)
            got_b = await mem_b.retrieve(query=text_b, top_k=5, current_step=1)
            assert any(text_a in item for item in got_a)
            assert not any(text_b in item for item in got_a)
            assert any(text_b in item for item in got_b)
            assert not any(text_a in item for item in got_b)

            exported_a = mem_a.export_memories()
            exported_b = mem_b.export_memories()
            assert len(exported_a) == 1
            assert len(exported_b) == 1
            assert exported_a[0]["metadata"]["agent_id"] == "A0001"
            assert exported_b[0]["metadata"]["agent_id"] == "A0002"
    finally:
        if prev_collection is None:
            os.environ.pop("CHROMA_MEMORY_COLLECTION_NAME", None)
        else:
            os.environ["CHROMA_MEMORY_COLLECTION_NAME"] = prev_collection


def test_persistence_manager_tmpfs_runtime_sync_and_cleanup():
    prev_mode = os.environ.get("CHROMA_RUNTIME_MODE")
    prev_root = os.environ.get("CHROMA_TMPFS_ROOT")
    prev_cleanup = os.environ.get("CHROMA_TMPFS_CLEANUP_ON_CLOSE")
    try:
        with tempfile.TemporaryDirectory(prefix="pm_save_") as save_dir, tempfile.TemporaryDirectory(prefix="pm_tmpfs_") as tmpfs_root:
            os.environ["CHROMA_RUNTIME_MODE"] = "tmpfs"
            os.environ["CHROMA_TMPFS_ROOT"] = tmpfs_root
            os.environ["CHROMA_TMPFS_CLEANUP_ON_CLOSE"] = "1"

            pm = PersistenceManager(save_dir=save_dir)
            assert pm._using_fallback_runtime is True
            assert str(pm.chroma_runtime_path).startswith(str(Path(tmpfs_root)))

            client = pm.get_chroma_client()
            collection = client.get_or_create_collection("tmpfs_sync_case")
            collection.upsert(
                ids=["id1"],
                documents=["hello"],
                embeddings=[[0.1] * 8],
                metadatas=[{"k": "v"}],
            )

            pm._sync_chroma_to_store()
            assert (Path(save_dir) / "chroma_store" / "chroma.sqlite3").exists()

            # 验证磁盘 store 中可读
            disk_client = chromadb.PersistentClient(path=str(Path(save_dir) / "chroma_store"))
            disk_collection = disk_client.get_collection("tmpfs_sync_case")
            assert disk_collection.count() >= 1

            runtime_path = pm.chroma_runtime_path
            pm.close()
            assert not runtime_path.exists()
    finally:
        if prev_mode is None:
            os.environ.pop("CHROMA_RUNTIME_MODE", None)
        else:
            os.environ["CHROMA_RUNTIME_MODE"] = prev_mode

        if prev_root is None:
            os.environ.pop("CHROMA_TMPFS_ROOT", None)
        else:
            os.environ["CHROMA_TMPFS_ROOT"] = prev_root

        if prev_cleanup is None:
            os.environ.pop("CHROMA_TMPFS_CLEANUP_ON_CLOSE", None)
        else:
            os.environ["CHROMA_TMPFS_CLEANUP_ON_CLOSE"] = prev_cleanup
