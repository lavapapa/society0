"""Next 独立进程适配：经 Actions/Information 取得 RR 的共同语义。"""
import argparse
import asyncio
import json
from pathlib import Path

from society0.kernel.actors import ActorRecord, actor_plugin
from society0.kernel.composition import compose
from society0.kernel.interaction import interaction_plugin, InteractionScope, Moment, Ref, Query
from society0.plugins.round_robin import round_robin_plugin


async def record(directory, source=None, start=1, stop=3):
    members = list("abcd")
    plugins = [actor_plugin({"rule": lambda r: None}, records=[ActorRecord(a, "rule",
                persona=f"{a}：谨慎的讨论参与者，保留完整个人设定。",
                state={"opinion": f"{a}的独立判断", "confidence": i + 1}) for i, a in enumerate(members)]),
               interaction_plugin(lambda *a: True), round_robin_plugin(members, group_size=4)]
    results = []
    async with compose(directory, plugins, source=source) as host:
        env = host.service("conversation", "mechanism")
        actions = host.service("interaction", "actions")
        information = host.service("interaction", "information")
        async def rows(actor, name):
            scope = InteractionScope(actor, Moment(number, "oracle"))
            page = await information.query(scope, "/conversation/" + name, Query(limit=100))
            assert page.next_cursor is None
            assert page.total == len(page.items)
            return page.items
        async def messages(actor, name):
            items = []
            for row in await rows(actor, name):
                part = await information.read(InteractionScope(actor, Moment(number, "oracle")),
                    "/conversation/content/" + str(row["id"]), offset=0, size=4096)
                assert part.next_offset is None
                items.append({"id": row["id"], "sender": row["sender"], "receiver": row["receiver"],
                              "round": row["round"], "content": part.data.decode()})
            return items
        for number in range(start, stop + 1):
            if number > 1:
                env.advance_round()
            pairs = env.start_round(number)["pairs"]
            action_results = []
            for actor in members:
                scope = InteractionScope(actor, Moment(number, "oracle"))
                target = Ref("conversation", "participants", actor)
                value = await actions.invoke(scope, "conversation.send_message_to_partner", target,
                                             {"content": f"r{number}:{actor}:私信全文"})
                assert value.status == "completed"
                action_results.append({"status": value.status, "value": {k: value.value[k] for k in ("sent_to", "message_id")}})
            value = await actions.invoke(InteractionScope("a", Moment(number, "oracle")),
                "conversation.broadcast_to_group", Ref("conversation", "participants", "a"),
                {"content": f"r{number}:广播全文"})
            assert value.status == "completed"
            action_results.append({"status": value.status, "value": {k: value.value[k] for k in ("round", "delivered")}})
            inbox, actors, history = {}, {}, []
            for actor in members:
                scope = InteractionScope(actor, Moment(number, "oracle"))
                value = await actions.invoke(scope, "conversation.mark_conversation_participant",
                    Ref("conversation", "participants", actor), {"marker": f"ready-{number}"})
                assert value.status == "completed"
                state = (await rows(actor, "participants"))[0]
                group = (await rows(actor, "group"))[0]
                actors[actor] = {
                    "partner": state["partner"], "history": [p["partner"] for p in await rows(actor, "partners")],
                    "upcoming": [{"round": p["round"], "partner": p["second"] if p["first"] == actor else p["first"]}
                                 for p in await rows(actor, "plan")],
                    "can_converse": bool(state["active"]), "round": state["round"],
                    "members": [m["id"] for m in await rows(actor, "members")],
                    "total_rounds": group["total_rounds"], "duration": group["duration"],
                    "marker": host.service("actors", "actors").get_record(actor).state["conversation_marker"],
                    "actor_record": {"persona": host.service("actors", "actors").get_record(actor).persona,
                                     "state": host.service("actors", "actors").get_record(actor).state},
                }
                inbox[actor] = [{k: v for k, v in m.items() if k != "id"} for m in await messages(actor, "messages")]
                history.extend(await messages(actor, "history"))
            history.sort(key=lambda m: m["id"])
            results.append({"round": number, "action_results": action_results, "pairs": [list(p) for p in pairs],
                "messages": [{k: v for k, v in m.items() if k != "id"} for m in history],
                "inbox": inbox, "actors": actors})
            host.service("storage", "store").complete(number)
    return results


async def record_plain(directory, source=None, stop=3):
    import sys
    from society0.kernel.runner import RunContract, RunPlan, run_plan
    from society0.kernel.runtime import DriverResult, Phase, runtime_plugin
    from society0.kernel.drivers import rule_driver_plugin
    from society0.kernel.schedule import SequenceSchedule, activate, schedule_plugin
    from society0.plugins.plain import plain_plugin
    observed = []
    members = list("abcd")
    plugin = plain_plugin()
    assert plugin.schema == () and plugin.initialize is None
    def rule(session):
        return DriverResult("completed", {"actor": session.actor.id}, "rule")
    async def phase(context):
        results = await activate(context, members)
        observed.append({"tick": context.moment.time, "state": {},
                         "actors": [item.result.value["actor"] for item in results]})
    plugins = [plugin, interaction_plugin(lambda *a: True), rule_driver_plugin(rule),
        actor_plugin({"rule": ("rule_driver", "factory")}, records=[ActorRecord(a, "rule") for a in members]),
        runtime_plugin(actor_service=("actors", "actors"), information=("interaction", "information"),
                       actions=("interaction", "actions"), store=("storage", "store"), max_activations=4),
        schedule_plugin(SequenceSchedule(range(stop), [Phase("plain", phase)]))]
    plan = RunPlan(plugins, RunContract(release={"line": "Next working tree"},
        dependencies={"python": sys.version}, configuration={"actors": members},
        time={"start": 0, "end": stop - 1}, budgets={"max_activations": 4}))
    await run_plan(directory, plan, source=source, step=1 if source else None)
    return observed


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--source", type=Path)
    parser.add_argument("--start", type=int, default=1)
    parser.add_argument("--stop", type=int, default=3)
    parser.add_argument("--kind", choices=["rr", "plain"], default="rr")
    args = parser.parse_args()
    value = (await record_plain(args.run, args.source, args.stop) if args.kind == "plain"
             else await record(args.run, args.source, args.start, args.stop))
    args.output.write_text(json.dumps(value, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
