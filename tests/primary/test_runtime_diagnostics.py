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
    assert "`llm`: 8 calls, 10.500s total, bottleneck `provider`." in report
    assert "Slowest call: 3.200s (step_name=browse_once" in report
    assert "### instruct / browse_round" in report
    assert "Max in-flight agents observed: 4 within configured concurrency 4." in report
    assert "Concurrency source counts: llm_model=1." in report
    assert "Progress diagnostics: max started 4, max pending 2, progress events 4, heartbeat events 1." in report
    assert "Runtime phase bottleneck: `agent_loop`." in report
    assert "Slowest action family: `recommended_feed`" in report
    assert "Memory: retrieved 4/4, saved 4, extractive enabled 4, extractive success 3." in report
    assert "do not disable memory, FoVs, or actions" in report


def test_runtime_diagnostic_report_accepts_summary_mapping():
    report = render_runtime_diagnostic_report(
        {
            "failed": True,
            "failure": {"error_type": "RuntimeError"},
            "events": {},
        }
    )

    assert "Status: failed" in report
    assert "The run failed with `RuntimeError`" in report


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
