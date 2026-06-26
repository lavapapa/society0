import json

import pytest

from society0.diagnostics import load_run_summary, render_runtime_diagnostic_report

pytestmark = pytest.mark.primary


def test_runtime_diagnostic_report_renders_key_bottlenecks(tmp_path):
    summary = {
        "failed": False,
        "final_step": 3,
        "total_time": 12.3456,
        "runtime": {"agent_concurrency": 4, "agent_concurrency_source": "llm_model"},
        "outputs": {"total_bytes": 9876},
        "capabilities": {
            "environment_type": "social_network",
            "counts": {"fovs": 4, "actions": 7, "rules": 3, "behaviors": 2},
            "by_source": {
                "environment": {"fovs": 4, "actions": 6, "rules": 3, "behaviors": 2},
                "experiment": {"fovs": 0, "actions": 1, "rules": 0, "behaviors": 0},
            },
            "by_kind": {
                "fovs": [{"name": "recommended_feed"}, {"name": "profile"}],
                "actions": [{"name": "comment"}, {"name": "publish_post"}],
                "rules": [{"name": "refresh_recommendation_cache"}],
                "behaviors": [{"name": "update_trust"}],
            },
        },
        "resources": {
            "llm": {
                "call_count": 8,
                "total_duration_sec": 10.5,
                "timing_breakdown": {"bottleneck": "provider"},
                "slowest_calls": [
                    {
                        "duration_sec": 3.2,
                        "step_name": "browse_once",
                        "interaction_type": "instruct",
                        "interaction_name": "browse_round",
                        "agent_id": "alice",
                    }
                ],
            },
            "embedding": {
                "call_count": 3,
                "total_duration_sec": 1.25,
                "timing_breakdown": {"bottleneck": "queue"},
            },
        },
        "events": {
            "env_hooks": {
                "before_tick": {
                    "hook_name": "before_tick",
                    "environment_type": "social_network",
                    "started_count": 3,
                    "completed_count": 3,
                    "failed_count": 0,
                    "duration_sec_total": 0.3,
                    "by_tick": {"0": {}, "1": {}, "2": {}},
                },
                "after_tick": {
                    "hook_name": "after_tick",
                    "environment_type": "social_network",
                    "started_count": 3,
                    "completed_count": 2,
                    "failed_count": 1,
                    "duration_sec_total": 0.45,
                    "by_tick": {"0": {}, "1": {}, "2": {}},
                    "error_samples": [
                        {"step": 2, "error": "cache flush failed", "error_type": "RuntimeError"}
                    ],
                },
            },
            "logic_executions": {
                "rule / refresh_recommendation_cache": {
                    "logic_kind": "rule",
                    "logic_name": "refresh_recommendation_cache",
                    "started_count": 3,
                    "completed_count": 3,
                    "failed_count": 0,
                    "success_count": 3,
                    "error_count": 0,
                    "duration_sec_total": 0.18,
                    "param_keys": ["pool_size"],
                    "by_tick": {"0": {}, "1": {}, "2": {}},
                },
                "behavior / update_trust": {
                    "logic_kind": "behavior",
                    "logic_name": "update_trust",
                    "started_count": 2,
                    "completed_count": 2,
                    "failed_count": 0,
                    "success_count": 6,
                    "error_count": 1,
                    "agent_count_total": 7,
                    "duration_sec_total": 0.24,
                    "param_keys": ["delta"],
                    "by_tick": {"0": {}, "1": {}},
                    "error_samples": [
                        {
                            "agent_id": "bob",
                            "status": "error",
                            "error": "bob rejected deterministic behavior",
                        }
                    ],
                },
            },
            "social_recommendations": {
                "trace_count": 2,
                "flush_count": 1,
                "unique_agent_count": 2,
                "raw_candidate_count_avg": 1000.0,
                "raw_candidate_count_max": 1000,
                "active_pool_count_avg": 1000.0,
                "active_pool_count_max": 1000,
                "returned_count_avg": 8.0,
                "returned_count_max": 8,
                "record_impression_count": 2,
                "record_recommended_state_count": 2,
                "preview_count": 0,
                "cache_rebuilds_total": 1,
                "rank_duration_sec_total": 0.12,
                "duration_sec_total": 0.8,
                "output_characters_max": 2400,
                "impression_delta_total": 16,
                "impression_post_count_total": 8,
                "recommended_agent_update_count": 2,
                "state_patch_count": 10,
                "by_tick": {"0": {"trace_count": 2, "flush_count": 1}},
                "score_samples": [
                    {
                        "tick": "0",
                        "agent_id": "alice",
                        "rank": 1,
                        "post_id": "old_high",
                        "total_score": 42.0,
                        "engagement_contribution": 40.0,
                        "time_contribution": 0.1,
                        "network_contribution": 0.0,
                        "semantic_contribution": 1.9,
                    }
                ],
            },
            "agent_batches": {
                "instruct / browse_round": {
                    "agent_count": 4,
                    "concurrency": 4,
                    "concurrency_source": "llm_model",
                    "success_count_total": 4,
                    "error_count_total": 0,
                    "duration_sec_total": 11.0,
                    "max_in_flight_count": 4,
                    "max_started_count": 4,
                    "max_pending_count": 2,
                    "progress_event_count": 4,
                    "heartbeat_event_count": 1,
                    "concurrency_source_counts": {"llm_model": 1},
                    "phase_timing_summary": {
                        "bottleneck": "agent_loop",
                        "phases": {
                            "agent_loop": {"record_count": 4, "total_sec": 8.0, "mean_sec": 2.0},
                            "fov_collection": {"record_count": 4, "total_sec": 1.0, "mean_sec": 0.25},
                        },
                    },
                    "action_duration_summary": {
                        "record_count": 6,
                        "bottleneck_action": "recommended_feed",
                        "by_action": {
                            "recommended_feed": {
                                "record_count": 4,
                                "total_sec": 1.8,
                                "mean_sec": 0.45,
                                "max_sec": 0.7,
                            }
                        },
                    },
                    "action_counts": {"get_trending_posts": 1, "comment": 2},
                    "successful_action_counts": {"get_trending_posts": 1, "comment": 1},
                    "failed_action_counts": {"comment": 1},
                    "action_tag_counts": {"social_read": 1, "social_write": 1, "comment": 1},
                    "termination_reason_counts": {"completion_action_tag": 4},
                    "action_semantics": {
                        "completion_action_tags": {
                            "configured": ["social_write"],
                            "observed_counts": {"social_write": 1},
                        },
                        "required_action_tags": {
                            "configured": ["social_write"],
                            "observed_counts": {"social_write": 1},
                        },
                    },
                    "action_error_samples": [
                        {
                            "agent_id": "alice",
                            "action_name": "comment",
                            "status": "error",
                            "error": "Post user_7 not found",
                            "arguments": {
                                "post_id": "user_7",
                                "content": "I used the author id by mistake.",
                            },
                        }
                    ],
                    "memory_summary": {
                        "record_count": 4,
                        "retrieve_enabled_count": 4,
                        "save_enabled_count": 4,
                        "extraction_enabled_count": 4,
                        "extraction_success_count": 3,
                    },
                    "resources": {
                        "llm": {
                            "call_count": 4,
                            "timing_breakdown": {"bottleneck": "provider"},
                        }
                    },
                }
            }
        },
    }
    (tmp_path / "summary.json").write_text(json.dumps(summary), encoding="utf-8")

    loaded = load_run_summary(tmp_path)
    report = render_runtime_diagnostic_report(tmp_path)

    assert loaded["final_step"] == 3
    assert "# Society0 Runtime Diagnostic Report" in report
    assert "Status: completed" in report
    assert "Agent concurrency: 4 (llm_model)" in report
    assert "Environment: `social_network`." in report
    assert "Capability counts: actions=7, behaviors=2, fovs=4, rules=3." in report
    assert "By source: environment actions=6, behaviors=2, fovs=4, rules=3; experiment actions=1, behaviors=0, fovs=0, rules=0." in report
    assert "Sample actions: comment, publish_post." in report
    assert "### before_tick" in report
    assert "started/completed/failed 3/3/0" in report
    assert "### after_tick" in report
    assert "started/completed/failed 3/2/1" in report
    assert "RuntimeError: cache flush failed" in report
    assert "## Rules And Behaviors" in report
    assert "### behavior / update_trust" in report
    assert "success/error 6/1; agents 7" in report
    assert "Params: delta." in report
    assert "bob rejected deterministic behavior" in report
    assert "### rule / refresh_recommendation_cache" in report
    assert "success/error 3/0" in report
    assert "Params: pool_size." in report
    assert "## Social Recommendation Diagnostics" in report
    assert "Recommendation traces: 2; agents 2; raw candidates avg/max 1000.0/1000" in report
    assert "active pool avg/max 1000.0/1000; returned avg/max 8.0/8" in report
    assert "Deferred recommendation flushes: 1; impression delta total 16" in report
    assert "Top score sample: tick=0, agent_id=alice, rank=1, post_id=old_high" in report
    assert "`llm`: 8 calls, 10.500s total, bottleneck `provider`." in report
    assert "Slowest call: 3.200s (step_name=browse_once" in report
    assert "### instruct / browse_round" in report
    assert "Max in-flight agents observed: 4 within configured concurrency 4." in report
    assert "Concurrency source counts: llm_model=1." in report
    assert "Progress diagnostics: max started 4, max pending 2, progress events 4, heartbeat events 1." in report
    assert "Runtime phase bottleneck: `agent_loop`." in report
    assert "Slowest action family: `recommended_feed`" in report
    assert "Actions: attempted comment=2, get_trending_posts=1; successful comment=1, get_trending_posts=1; failed comment=1." in report
    assert "Successful action tags: comment=1, social_read=1, social_write=1." in report
    assert "Termination reasons: completion_action_tag=4." in report
    assert "Action semantics: completion_action_tags configured [social_write], observed social_write=1; required_action_tags configured [social_write], observed social_write=1." in report
    assert "Action error samples: 1; inspect tool arguments before weakening actions." in report
    assert "Sample: agent_id=alice, action_name=comment, status=error; error=Post user_7 not found." in report
    assert "Arguments: content=I used the author id by mistake., post_id=user_7." in report
    assert "Memory: retrieved 4/4, saved 4, extractive enabled 4, extractive success 3." in report
    assert "do not disable memory, FoVs, or actions" in report


def test_runtime_diagnostic_report_accepts_summary_mapping():
    report = render_runtime_diagnostic_report(
        {
            "failed": True,
            "failure": {"error_type": "RuntimeError", "failed_step": 2, "error": "boom"},
            "events": {},
        }
    )

    assert "Status: failed" in report
    assert "The run failed with `RuntimeError` at step 2: boom" in report


def test_runtime_diagnostic_report_flags_concurrency_fanout_mismatch():
    report = render_runtime_diagnostic_report(
        {
            "events": {
                "agent_batches": {
                    "instruct / suspicious_round": {
                        "agent_count": 8,
                        "concurrency": 3,
                        "concurrency_source": "explicit",
                        "success_count_total": 8,
                        "error_count_total": 0,
                        "max_in_flight_count": 5,
                        "concurrency_source_counts": {"explicit": 1},
                    }
                }
            }
        }
    )

    assert "Max in-flight agents observed: 5 above configured concurrency 3" in report
    assert "unexpected fan-out" in report


def test_runtime_diagnostic_report_explains_action_preflight_configuration_errors():
    report = render_runtime_diagnostic_report(
        {
            "events": {
                "agent_batches": {
                    "instruct / unsatisfiable_required_action": {
                        "agent_count": 1,
                        "concurrency": 1,
                        "concurrency_source": "explicit",
                        "success_count_total": 0,
                        "error_count_total": 1,
                        "error_samples": [
                            {
                                "agent_id": "viewer",
                                "status": "error",
                                "error": (
                                    "Required action(s) 'publish_post' are not available after "
                                    "applying actions=['comment']. Align required_actions/"
                                    "required_action_tags with the actions exposed to the LLM tool loop."
                                ),
                            }
                        ],
                    }
                }
            }
        }
    )

    assert "Agent error samples: 1; inspect configuration and per-agent logs." in report
    assert "Sample: agent_id=viewer, status=error; error=Required action(s) 'publish_post'" in report
    assert "Configuration preflight: required_actions or required_action_tags cannot be satisfied" in report
    assert "instead of weakening the tool/action loop" in report
