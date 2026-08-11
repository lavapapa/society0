import builtins

import pytest

from society0.persistence import PersistenceManager


def test_persistence_manager_does_not_import_chromadb_on_init(tmp_path, monkeypatch):
    real_import = builtins.__import__

    def guarded_import(name, *args, **kwargs):
        if name == "chromadb" or name.startswith("chromadb."):
            raise AssertionError("chromadb should be loaded lazily, not during PersistenceManager.__init__")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)

    manager = PersistenceManager(str(tmp_path / "run"))

    assert manager._chroma_client is None
    manager.close()


def test_live_chroma_store_has_no_checkpoint_backup_surface(tmp_path):
    manager = PersistenceManager(str(tmp_path / "run"))
    try:
        assert manager.chroma_store_path.is_dir()
        assert not (tmp_path / "run" / "chroma_backups").exists()
        for obsolete in (
            "chroma_backup_dir",
            "get_available_chroma_backups",
            "_backup_chroma_store",
            "_restore_chroma_store",
        ):
            assert not hasattr(manager, obsolete), obsolete
    finally:
        manager.close()


def test_close_propagates_sync_failure_and_retains_runtime_for_retry(tmp_path, monkeypatch):
    monkeypatch.setenv("CHROMA_RUNTIME_MODE", "tmpfs")
    monkeypatch.setenv("CHROMA_TMPFS_ROOT", str(tmp_path / "tmpfs"))
    manager = PersistenceManager(str(tmp_path / "run"))
    runtime_path = manager.chroma_runtime_path
    store_path = manager.chroma_store_path
    runtime_path.mkdir(parents=True, exist_ok=True)
    store_path.mkdir(parents=True, exist_ok=True)
    (runtime_path / "new.txt").write_text("new", encoding="utf-8")
    (store_path / "old.txt").write_text("old", encoding="utf-8")
    client = object()
    manager._chroma_client = client

    def fail_sync():
        raise OSError("sync failed")

    monkeypatch.setattr(manager, "_sync_chroma_to_store", fail_sync)

    with pytest.raises(OSError, match="sync failed"):
        manager.close()

    assert runtime_path.exists()
    assert (runtime_path / "new.txt").read_text(encoding="utf-8") == "new"
    assert (store_path / "old.txt").read_text(encoding="utf-8") == "old"
    assert manager._chroma_client is client

    monkeypatch.undo()
    manager.close()
    assert not runtime_path.exists()
    assert (store_path / "new.txt").read_text(encoding="utf-8") == "new"
    manager.close()


def test_close_success_closes_client_and_cleans_runtime(tmp_path, monkeypatch):
    monkeypatch.setenv("CHROMA_RUNTIME_MODE", "tmpfs")
    monkeypatch.setenv("CHROMA_TMPFS_ROOT", str(tmp_path / "tmpfs"))
    manager = PersistenceManager(str(tmp_path / "run"))
    runtime_path = manager.chroma_runtime_path
    runtime_path.mkdir(parents=True, exist_ok=True)
    (runtime_path / "new.txt").write_text("new", encoding="utf-8")
    client = object()
    manager._chroma_client = client
    sync_calls = []

    def sync():
        sync_calls.append(True)

    monkeypatch.setattr(manager, "_sync_chroma_to_store", sync)

    manager.close()

    assert sync_calls == [True]
    assert manager._chroma_client is None
    assert not runtime_path.exists()
    manager.close()
    assert sync_calls == [True]
