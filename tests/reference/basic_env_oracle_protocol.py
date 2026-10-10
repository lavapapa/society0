"""固定旧源码的隔离启动器与严格、按顺序比较的共同协议。"""
from __future__ import annotations
import copy
import io
import json
import os
import re
from pathlib import Path
import subprocess
import sys
import tarfile

from tests.reference.basic_env_v4114_oracle import SOURCE_COMMIT

ROOT = Path(__file__).resolve().parents[2]
PROXIES = {p for key in ("ALL_PROXY", "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY") for p in (key, key.lower())}


def clean_env(source):
    return {**{k: v for k, v in os.environ.items() if k not in PROXIES},
            "PYTHONPATH": str(source), "PYTHONHASHSEED": "0"}


def run_legacy(directory, python=sys.executable, *, resume_source=None, steps=3):
    directory = Path(directory).resolve()
    directory.mkdir(parents=True)
    source = directory / "source"
    source.mkdir()
    archive = subprocess.check_output(["git", "archive", SOURCE_COMMIT, "src"], cwd=ROOT)
    with tarfile.open(fileobj=io.BytesIO(archive)) as bundle:
        bundle.extractall(source, filter="data")
    # 原字节核对；不使用哈希，不信任临时目录内的版本自述。
    for kind in ("plain", "round_robin", "social_network"):
        relative = f"src/society0/env/{kind}/env.py"
        expected = subprocess.check_output(["git", "show", f"{SOURCE_COMMIT}:{relative}"], cwd=ROOT)
        assert (source / relative).read_bytes() == expected
    output = directory / "legacy.json"
    command = [str(python), str(ROOT / "tests/reference/basic_env_v4114_oracle.py"),
               "--output", str(output), "--runs", str(directory / "runs"), "--steps", str(steps)]
    if resume_source:
        command += ["--source", str(resume_source)]
    process = subprocess.run(command, cwd=directory, env=clean_env(source / "src"),
                             capture_output=True, text=True, timeout=120)
    assert process.returncode == 0, process.stdout + process.stderr
    result = json.loads(output.read_text())
    assert Path(result["loaded_package"]).resolve() == source / "src/society0/__init__.py"
    assert result["source_commit"] == SOURCE_COMMIT
    return result


def normalized_record(record):
    """仅标准化加载绝对路径和无业务意义的随机 reply UUID；其余字段严格保留。"""
    aliases = {}
    def walk(value):
        if isinstance(value, dict):
            result = {}
            for key, item in value.items():
                if key == "loaded_package":
                    continue
                if key == "reply_id":
                    item = aliases.setdefault(item, f"reply-{len(aliases) + 1}")
                result[key] = walk(item)
            return result
        if isinstance(value, list):
            return [walk(item) for item in value]
        return value
    return walk(record)


def assert_equal(expected, actual, path="$"):
    """第一处差异给出完整路径；不排序列表、不裁剪字段、不放宽浮点误差。"""
    assert type(expected) is type(actual), f"{path}: type {type(expected).__name__} != {type(actual).__name__}"
    if isinstance(expected, dict):
        assert expected.keys() == actual.keys(), f"{path}: keys {expected.keys()} != {actual.keys()}"
        for key in expected:
            assert_equal(expected[key], actual[key], f"{path}.{key}")
    elif isinstance(expected, list):
        assert len(expected) == len(actual), f"{path}: length {len(expected)} != {len(actual)}"
        for index, (left, right) in enumerate(zip(expected, actual)):
            assert_equal(left, right, f"{path}[{index}]")
    else:
        assert expected == actual, f"{path}: {expected!r} != {actual!r}"


def rr_semantics(legacy):
    """公开API/SQL存储差异映射；保留全部消息、配对、参与标记、主体资料。"""
    rr = legacy["scenarios"]["round_robin_conversation"]
    rounds = []
    for step in rr["steps"]:
        number = step["round"]
        rounds.append({
            "round": number,
            "action_results": [{"status": "completed" if entry["result"]["status"] == "success" else entry["result"]["status"],
                "value": ({k: entry["result"][k] for k in ("sent_to", "message_id")} if entry["name"] == "send_message_to_partner"
                          else {k: entry["result"][k] for k in ("round", "delivered")})}
                for entry in rr["actions"] if entry["tick"] == number - 1],
            "pairs": [[p["agent1"], p["agent2"]] for p in step["pairing"]["pairing_details"]],
            "messages": step["messages"], "inbox": step["inbox"],
            "actors": {a: {
                "partner": status["current_partner"], "history": status["partner_history"],
                "upcoming": [{"round": int(n), "partner": p} for n, p in re.findall(
                    r"^第 (\d+) 轮：(.*)$", step["group_fov"][a], flags=re.MULTILINE)],
                "can_converse": status["can_converse"], "round": status["current_round"],
                "members": rr["final_state"]["groups"][0],
                "total_rounds": rr["final_state"]["pairing_total_rounds"],
                "duration": rr["final_state"]["config"]["session_duration_minutes"],
                "marker": next(m["marker"] for m in step["markers"] if m["agent_id"] == a),
                "actor_record": step["actor_records"][a],
            } for a, status in step["pairing_status"].items()},
        })
    return rounds


def social_action_result(action, *, legacy):
    """解析本固定脚本的真实返回；未知/失败文本保留诊断，绝不视作成功。"""
    name, value, args = action["name"], action["result"], action["arguments"]
    if legacy:
        patterns = {
            "follow": r"Successfully followed (\S+)",
            "unfollow": r"Successfully unfollowed (\S+)",
            "publish_post": r"Successfully published post (\S+)",
            "like_post": r"Successfully liked post (\S+)",
            "comment": r"Successfully commented on post (\S+)",
            "repost": r"Reposted (\S+): Successfully published post (\S+)",
        }
        if name == "get_trending_posts":
            pattern = r"\d+\. 帖子 ID: (\S+) \| 作者用户 ID: (\S+)\n内容: (.*?)\n互动: 👍(\d+) 💬(\d+) 👁️(\d+)"
            entries = re.findall(pattern, value, flags=re.DOTALL)
            if not value.startswith("热门动态（本动作会记录曝光）\n") or not entries:
                return {"status": "rejected", "diagnostic": value}
            return {"status": "completed", "posts": [{"post_id": p, "author_id": a,
                "content": text, "like_count": int(likes), "reply_count": int(replies), "view_count": int(views)}
                for p, a, text, likes, replies, views in entries]}
        match = re.fullmatch(patterns[name], value)
        if not match:
            return {"status": "rejected", "diagnostic": value}
        if name in ("follow", "unfollow"):
            return {"status": "completed", "changed": True, "target": match[1]}
        if name == "repost":
            return {"status": "completed", "target": match[1], "post_id": match[2]}
        return {"status": "completed", "post_id": match[1], **({"changed": True} if name == "like_post" else {})}
    status = action["result_status"]
    if status != "completed":
        return {"status": status, "diagnostic": value}
    if name in ("follow", "unfollow"):
        return {"status": status, "changed": value["changed"], "target": args["target_agent_id"]}
    if name == "publish_post":
        return {"status": status, "post_id": value["post_id"]}
    if name == "like_post":
        return {"status": status, "post_id": args["post_id"], "changed": value["changed"]}
    if name == "comment":
        # 旧返回未暴露 reply id；Next 返回的物理 id 必须指向刚创建的同一条评论。
        reply = action["state"]["posts"][args["post_id"]]["replies"][-1]
        assert value["reply_id"] == reply["reply_id"], "comment result points to another reply"
        return {"status": status, "post_id": args["post_id"]}
    if name == "repost":
        return {"status": status, "target": args["post_id"], "post_id": value["post_id"]}
    if name == "get_trending_posts":
        posts = []
        for p in value["posts"]:
            # 此固定脚本正文均小于旧预览120字；正文截断合同另行测试。
            text = p["content"]
            assert len(text) <= 120
            if p["special_tags"]:
                text += " [系统标记: " + ", ".join(p["special_tags"]) + "]"
            posts.append({**{k: p[k] for k in ("post_id", "author_id", "like_count", "reply_count", "view_count")}, "content": text})
        return {"status": status, "posts": posts}
    raise ValueError(name)


def social_semantics(social, *, legacy):
    """逐动作事实共同合同。UUID/通知存储id、附加引用及呈现排版单独留原始录制。"""
    def state(raw):
        posts = {}
        for identifier, post in raw["posts"].items():
            item = {k: post[k] for k in ("post_id", "author_id", "content", "tags", "created_tick",
                    "reply_to", "likes", "like_events", "special_tags", "view_count")}
            item["replies"] = [{k: reply[k] for k in ("author_id", "content", "created_tick")}
                               for reply in post["replies"]]
            item["like_count"] = len(post["likes"]) if legacy else post["like_count"]
            item["reply_count"] = len(post["replies"]) if legacy else post["reply_count"]
            item["repost_count"] = (sum(p["reply_to"] == identifier for p in raw["posts"].values())
                                    if legacy else post["repost_count"])
            posts[identifier] = item
        notices = []
        for actor, entries in raw["notifications"].items():
            if legacy:
                entries = entries["notifications"]
            for n in entries:
                # Next 新增 reply_id/repost_id 导航引用，旧通知无这些字段；其余载荷逐项保留。
                data = {k: v for k, v in n["data"].items() if k not in ("reply_id", "repost_id")}
                notices.append({"ordinal": int(n["id"].removeprefix("notif_")) if legacy else n["id"],
                    "actor": actor, "type": n["type"], "tick": n["created_tick"], "data": data})
        notices.sort(key=lambda n: n["ordinal"])
        return {"posts": posts, "edges": raw["edges"], "notifications": notices}
    steps = [{"tick": s["tick"], **state(s), "ranking": s["ranking"],
              # 旧 active_pool_ids 是集合；集合成员比较与最终排名顺序比较分开。
              "active_pool_members": sorted(s["active_pool"]), "trending_ids": s["trending_ids"],
              "pending_exposures": s["pending_exposures"]} for s in social["steps"]]
    if legacy:
        raw_final = copy.deepcopy(social["steps"][-1])
        for identifier, projection in social["final_state"]["post_projection"].items():
            raw_final["posts"][identifier]["view_count"] = projection["view_count"]
        recommended = social["final_state"]["recommended_posts"]
    else:
        raw_final, recommended = social["final"], social["recommended"]
    return {"steps": steps, "final": state(raw_final), "recommended": recommended,
        "intervention": social["intervention"],
        "actions": [{**{k: action[k] for k in ("tick", "actor", "name", "arguments")},
                     "result": social_action_result(action, legacy=legacy),
                     "state": state(action["state"])} for action in social["actions"]]}
