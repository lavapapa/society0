import builtins

from society0.core_data import World


def test_plain_environment_does_not_import_networkx(monkeypatch):
    real_import = builtins.__import__
    blocked = ("networkx", "json_repair", "chromadb")

    def guarded_import(name, *args, **kwargs):
        if name in blocked or name.startswith(tuple(f"{item}." for item in blocked)):
            raise AssertionError(f"plain environment should not import {name}")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)

    world = World()
    world.environment_data["type"] = "plain"

    env = world.get_environment()

    assert env.__class__.__name__ == "PlainEnvironment"
