"""实际 v4.1.14 引擎录制、跨进程 Next 对照和比较器主动损坏验收。"""
import copy
import json
import subprocess
import sys

import pytest
from tests.reference.basic_env_oracle_protocol import (
    ROOT, assert_equal, clean_env, normalized_record, rr_semantics, run_legacy, social_semantics,
)


@pytest.fixture(scope="module")
def legacy(tmp_path_factory):
    return run_legacy(tmp_path_factory.mktemp("legacy-oracle") / "first")


def test_fixed_legacy_engine_runs_complete_scenarios(legacy):
    scenarios = legacy["scenarios"]
    assert [s["state"] for s in scenarios["plain"]["steps"]] == [{}, {}, {}]
    rr = scenarios["round_robin_conversation"]
    assert len(rr["final_state"]["message_facts"]) == 21
    assert len(rr["final_state"]["pairing_completed_pairs"]) == 6
    social = scenarios["social_network"]
    assert social["steps"][0]["trending_ids"] == ["post_1", "post_2"]
    assert "post_2" not in social["steps"][0]["active_pool"]
    assert social["final_state"]["post_counter"] == 5
    assert social["final_state"]["notification_counter"] == 10
    # 真实 after_tick 完成后累计曝光，覆盖每次 feed 与热门 action。
    totals = {}
    for step in social["steps"]:
        for post, count in step["pending_exposures"].items():
            totals[post] = totals.get(post, 0) + count
    for post, count in totals.items():
        assert social["final_state"]["post_projection"][post]["view_count"] == count


def test_legacy_repeated_process_is_deterministic(legacy, tmp_path):
    other = run_legacy(tmp_path / "other")
    assert_equal(normalized_record(legacy), normalized_record(other))


def test_legacy_complete_point_separate_process_restore(legacy, tmp_path):
    first = run_legacy(tmp_path / "first", steps=1)
    restored = run_legacy(tmp_path / "restored", resume_source=tmp_path / "first/runs", steps=2)
    for name, scenario in restored["scenarios"].items():
        scenario["steps"] = first["scenarios"][name]["steps"] + scenario["steps"]
        scenario["actions"] = first["scenarios"][name]["actions"] + scenario["actions"]
    for name in ("plain", "round_robin_conversation"):
        assert_equal(normalized_record(legacy["scenarios"][name]), normalized_record(restored["scenarios"][name]))
    # 已知旧缺陷：实际恢复后的图丢失动态关注。精确保留反例，不放宽共同合同。
    old_social = legacy["scenarios"]["social_network"]
    restored_social = restored["scenarios"]["social_network"]
    assert old_social["actions"][14]["result"] == "Successfully unfollowed a"
    assert restored_social["actions"][14]["result"] == "Not following a"
    with pytest.raises(AssertionError, match=r"actions\[13\].state.edges"):
        assert_equal(normalized_record(old_social), normalized_record(restored_social))


@pytest.mark.parametrize("damage", ["message", "exposure", "pairing", "notification", "ranking"])
def test_comparator_rejects_single_semantic_corruption(legacy, damage):
    expected = normalized_record(legacy)
    damaged = copy.deepcopy(expected)
    rr = damaged["scenarios"]["round_robin_conversation"]
    social = damaged["scenarios"]["social_network"]
    if damage == "message":
        rr["steps"][0]["messages"][0]["content"] += "损坏"
    elif damage == "exposure":
        social["steps"][0]["pending_exposures"]["post_1"] += 1
    elif damage == "pairing":
        rr["steps"][0]["pairing"]["pairing_details"][0]["agent2"] = "b"
    elif damage == "notification":
        social["final_state"]["notification_facts"][0]["target_agent_id"] = "d"
    else:
        social["steps"][0]["trending_ids"].reverse()
    with pytest.raises(AssertionError, match=r"\$\.scenarios"):
        assert_equal(expected, damaged)


@pytest.mark.parametrize("damage", ["message", "pairing", "exposure"])
def test_common_protocol_rejects_single_corruption(legacy, damage):
    expected = {"rr": rr_semantics(legacy),
                "social": social_semantics(legacy["scenarios"]["social_network"], legacy=True)}
    damaged = copy.deepcopy(expected)
    if damage == "message":
        damaged["rr"][0]["messages"][0]["content"] += "损坏"
    elif damage == "pairing":
        damaged["rr"][0]["pairs"][0][1] = "b"
    else:
        damaged["social"]["steps"][0]["pending_exposures"]["post_1"] += 1
    with pytest.raises(AssertionError):
        assert_equal(expected, damaged)


@pytest.mark.parametrize("index,before,after", [
    (0, "Successfully followed b", "Not following b"),
    (4, "post_1", "post_wrong"), (11, "post_3", "post_wrong"),
    (12, "旧热帖 #topic 原文", "损坏正文"), (12, "作者用户 ID: a", "作者用户 ID: b"),
    (12, "👁️0", "👁️999"),
])
def test_legacy_result_corruption_survives_common_mapping(legacy, index, before, after):
    original = legacy["scenarios"]["social_network"]
    damaged = copy.deepcopy(original)
    assert before in damaged["actions"][index]["result"]
    damaged["actions"][index]["result"] = damaged["actions"][index]["result"].replace(before, after)
    with pytest.raises(AssertionError):
        assert_equal(social_semantics(original, legacy=True), social_semantics(damaged, legacy=True))


def test_next_result_corruption_survives_common_mapping(tmp_path):
    original = next_social(tmp_path / "next-results")
    expected = social_semantics(original, legacy=False)
    for index, key, value in [(0, "changed", False), (4, "post_id", "post_wrong"),
                              (11, "post_id", "post_wrong"), (10, "reply_id", 999)]:
        damaged = copy.deepcopy(original)
        damaged["actions"][index]["result"][key] = value
        with pytest.raises(AssertionError):
            assert_equal(expected, social_semantics(damaged, legacy=False))
    damaged = copy.deepcopy(original)
    damaged["actions"][12]["result"]["posts"][0]["content"] = "损坏正文"
    with pytest.raises(AssertionError):
        assert_equal(expected, social_semantics(damaged, legacy=False))
    damaged = copy.deepcopy(original)
    damaged["actions"][0]["result_status"] = "rejected"
    with pytest.raises(AssertionError):
        assert_equal(expected, social_semantics(damaged, legacy=False))


def next_rr(path, *, source=None, start=1, stop=3, kind="rr"):
    output = path.with_suffix(".json")
    command = [sys.executable, str(ROOT / "tests/reference/basic_env_next_oracle.py"),
               "--output", str(output), "--run", str(path), "--start", str(start), "--stop", str(stop), "--kind", kind]
    if source:
        command += ["--source", str(source)]
    subprocess.run(command, cwd=path.parent, env=clean_env(ROOT / "src"), check=True,
                   capture_output=True, text=True, timeout=120)
    return json.loads(output.read_text())


def test_next_plain_run_plan_and_new_process_restore(legacy, tmp_path):
    expected = legacy["scenarios"]["plain"]["steps"]
    assert_equal(expected, next_rr(tmp_path / "plain", kind="plain"))
    first = next_rr(tmp_path / "plain-first", kind="plain", stop=1)
    rest = next_rr(tmp_path / "plain-rest", kind="plain", source=tmp_path / "plain-first", start=2)
    assert_equal(expected, first + rest)


def next_social(path, *, source=None, start=0, stop=3):
    output = path.with_suffix(".json")
    command = [sys.executable, str(ROOT / "tests/reference/basic_env_next_social.py"),
               "--output", str(output), "--run", str(path), "--start", str(start), "--stop", str(stop)]
    if source:
        command += ["--source", str(source)]
    subprocess.run(command, cwd=path.parent, env=clean_env(ROOT / "src"), check=True,
                   capture_output=True, text=True, timeout=120)
    return json.loads(output.read_text())


def test_next_social_matches_actual_legacy_per_action_notifications_ranking_exposure(legacy, tmp_path):
    expected = social_semantics(legacy["scenarios"]["social_network"], legacy=True)
    actual = social_semantics(next_social(tmp_path / "next-social"), legacy=False)
    assert_equal(expected, actual)


def test_next_social_process_restore_preserves_continuous_legacy_semantics(legacy, tmp_path):
    first = next_social(tmp_path / "first-social", stop=1)
    rest = next_social(tmp_path / "rest-social", source=tmp_path / "first-social", start=1)
    rest["steps"] = first["steps"] + rest["steps"]
    rest["actions"] = first["actions"] + rest["actions"]
    assert_equal(social_semantics(legacy["scenarios"]["social_network"], legacy=True),
                 social_semantics(rest, legacy=False))


def test_next_rr_matches_actual_legacy_per_message_and_actor_information(legacy, tmp_path):
    assert_equal(rr_semantics(legacy), next_rr(tmp_path / "next"))


def test_next_rr_separate_process_restore_continues_same_legacy_script(legacy, tmp_path):
    first = next_rr(tmp_path / "first", stop=1)
    remaining = next_rr(tmp_path / "restored", source=tmp_path / "first", start=2)
    assert_equal(rr_semantics(legacy), first + remaining)
