import builtins

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
