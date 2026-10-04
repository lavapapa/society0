"""最终候选的独立外部消费者回归用例。"""

import pytest

from society0.kernel.information_sql import DatasetSpec, SQLInformation
from society0.kernel.interaction import (
    Action, ActionResult, Actions, Information, InteractionScope, Moment, Ref,
)
from society0.kernel.runtime import Actor, DriverResult, Phase, Runtime
from society0.kernel.storage import StageStore


@pytest.mark.asyncio
async def test_namespace_directory_does_not_disclose_hidden_dataset(tmp_path):
    store = StageStore.create(tmp_path / "run", [
        "CREATE TABLE public_rows(id INTEGER PRIMARY KEY)",
        "CREATE TABLE private_rows(id INTEGER PRIMARY KEY)",
    ])
    try:
        provider = SQLInformation("world", store, {
            "public": DatasetSpec("public_rows", "id", ("id",)),
            "private": DatasetSpec("private_rows", "id", ("id",)),
        })
        information = Information(
            lambda scope, operation, ref: ref.kind != "private"
        )
        information.mount("/world", provider)
        with InteractionScope("alice", Moment(1, "read")) as scope:
            page = await information.list(scope, "/world")
            assert page.total == 1
            assert [item["path"] for item in page.items] == ["/world/public"]
    finally:
        store.close()


@pytest.mark.asyncio
async def test_caught_action_fault_cannot_publish_partial_domain_effect(tmp_path):
    store = StageStore.create(tmp_path / "run", [
        "CREATE TABLE effects(id INTEGER PRIMARY KEY)",
    ])
    actions = Actions(lambda *args: True)

    def handler(scope, target, arguments):
        store.transaction(lambda writer: writer.execute("INSERT INTO effects VALUES(1)"))
        raise ValueError("domain action failed after first write")

    actions.register(Action("domain.effect", ("domain", "thing"), "", {
        "type": "object",
    }, handler))

    class Driver:
        async def run(self, session):
            try:
                await session.actions.invoke("domain.effect", Ref("domain", "thing", "one"), {})
            except ValueError:
                pass
            return DriverResult("completed")

    runtime = Runtime([Actor("alice", Driver())], information=Information(lambda *args: True),
                      actions=actions, store=store)
    try:
        fault = None
        try:
            await runtime.run_step(1, 1, [Phase("act", lambda context: context.activate("alice"))])
        except Exception as error:
            fault = error
        assert store.complete_step == 0
        assert fault is not None
    finally:
        store.close()
