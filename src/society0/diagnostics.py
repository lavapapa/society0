"""Runtime diagnostics helpers for completed Society0 runs."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional


def load_run_summary(run_dir: str | Path) -> Dict[str, Any]:
    """Load ``summary.json`` from a completed Society0 run directory."""
    summary_path = Path(run_dir) / "summary.json"
    return json.loads(summary_path.read_text(encoding="utf-8"))


def render_runtime_diagnostic_report(run_dir_or_summary: str | Path | Mapping[str, Any]) -> str:
    """Render a researcher-facing Markdown diagnostic report for one run.

    The report is intentionally read-only. It summarizes runtime evidence from
    ``summary.json`` without changing concurrency, memory, action, or model
    behavior.
    """
    summary = (
        dict(run_dir_or_summary)
        if isinstance(run_dir_or_summary, Mapping)
        else load_run_summary(run_dir_or_summary)
    )

    lines: List[str] = ["# Society0 Runtime Diagnostic Report", ""]
    lines.extend(_render_run_overview(summary))
    lines.extend(_render_resource_bottlenecks(summary.get("resources") or {}))
    lines.extend(_render_agent_batches(summary.get("events", {}).get("agent_batches") or {}))
    lines.extend(_render_diagnostic_notes(summary))
    return "\n".join(lines).rstrip() + "\n"


def _render_run_overview(summary: Mapping[str, Any]) -> List[str]:
    failed = bool(summary.get("failed"))
    runtime = summary.get("runtime") if isinstance(summary.get("runtime"), dict) else {}
    outputs = summary.get("outputs") if isinstance(summary.get("outputs"), dict) else {}
    files = outputs.get("files") if isinstance(outputs.get("files"), dict) else {}
    output_bytes = outputs.get("total_bytes")
    if not isinstance(output_bytes, (int, float)):
        output_bytes = sum(
            int(info.get("bytes") or 0)
            for info in files.values()
            if isinstance(info, dict)
        )

    lines = ["## Run Overview", ""]
    lines.append(f"- Status: {'failed' if failed else 'completed'}")
    if summary.get("final_step") is not None:
        lines.append(f"- Final step: {summary.get('final_step')}")
    if summary.get("total_time") is not None:
        lines.append(f"- Total runtime: {_fmt_seconds(summary.get('total_time'))}")
    if runtime.get("agent_concurrency") is not None:
        source = runtime.get("agent_concurrency_source") or "unknown"
        lines.append(f"- Agent concurrency: {runtime.get('agent_concurrency')} ({source})")
    if output_bytes:
        lines.append(f"- Run artifact size: {int(output_bytes)} bytes")
    lines.append("")
    return lines


def _render_resource_bottlenecks(resources: Mapping[str, Any]) -> List[str]:
    if not resources:
        return []

    lines = ["## Model And Embedding Resources", ""]
    for resource_name, bucket in sorted(resources.items()):
        if not isinstance(bucket, Mapping):
            continue
        timing = bucket.get("timing_breakdown") if isinstance(bucket.get("timing_breakdown"), dict) else {}
        call_count = bucket.get("call_count", 0)
        total = bucket.get("total_duration_sec", bucket.get("duration_sec_total", 0))
        bottleneck = timing.get("bottleneck") or "unknown"
        lines.append(
            f"- `{resource_name}`: {call_count} calls, {_fmt_seconds(total)} total, bottleneck `{bottleneck}`."
        )
        slowest = bucket.get("slowest_calls") if isinstance(bucket.get("slowest_calls"), list) else []
        if slowest:
            sample = slowest[0]
            if isinstance(sample, Mapping):
                context = _compact_context(
                    sample,
                    keys=("step_name", "interaction_type", "interaction_name", "agent_id"),
                )
                lines.append(
                    f"  Slowest call: {_fmt_seconds(sample.get('duration_sec'))}"
                    + (f" ({context})" if context else "")
                    + "."
                )
    lines.append("")
    return lines


def _render_agent_batches(agent_batches: Mapping[str, Any]) -> List[str]:
    if not agent_batches:
        return []

    lines = ["## Agent Batches", ""]
    for name, batch in sorted(agent_batches.items()):
        if not isinstance(batch, Mapping):
            continue
        lines.append(f"### {name}")
        lines.append("")
        lines.append(
            "- Agents: "
            f"{batch.get('agent_count', 'unknown')}; "
            f"concurrency {batch.get('concurrency', 'unknown')} "
            f"({batch.get('concurrency_source', 'unknown')}); "
            f"success/error total {batch.get('success_count_total', 0)}/{batch.get('error_count_total', 0)}."
        )
        if batch.get("duration_sec_total") is not None:
            lines.append(f"- Batch runtime total: {_fmt_seconds(batch.get('duration_sec_total'))}.")
        concurrency_lines = _render_batch_concurrency(batch)
        lines.extend(concurrency_lines)

        phase = batch.get("phase_timing_summary") if isinstance(batch.get("phase_timing_summary"), dict) else {}
        if phase:
            lines.append(f"- Runtime phase bottleneck: `{phase.get('bottleneck', 'unknown')}`.")
            lines.extend(_render_top_phase_rows(phase.get("phases") or {}))

        action_duration = (
            batch.get("action_duration_summary")
            if isinstance(batch.get("action_duration_summary"), dict)
            else {}
        )
        if action_duration:
            lines.append(
                f"- Slowest action family: `{action_duration.get('bottleneck_action', 'unknown')}` "
                f"across {action_duration.get('record_count', 0)} action attempts."
            )

        memory = batch.get("memory_summary") if isinstance(batch.get("memory_summary"), dict) else {}
        if memory:
            lines.append(
                "- Memory: "
                f"retrieved {memory.get('retrieve_enabled_count', 0)}/"
                f"{memory.get('record_count', 0)}, "
                f"saved {memory.get('save_enabled_count', 0)}, "
                f"extractive enabled {memory.get('extraction_enabled_count', 0)}, "
                f"extractive success {memory.get('extraction_success_count', 0)}."
            )

        action_errors = batch.get("action_error_samples")
        if isinstance(action_errors, list) and action_errors:
            lines.append(f"- Action error samples: {len(action_errors)}; inspect tool arguments before weakening actions.")

        resources = batch.get("resources") if isinstance(batch.get("resources"), dict) else {}
        if resources:
            for resource_name, bucket in sorted(resources.items()):
                if not isinstance(bucket, Mapping):
                    continue
                timing = bucket.get("timing_breakdown") if isinstance(bucket.get("timing_breakdown"), dict) else {}
                lines.append(
                    f"- `{resource_name}` resource bottleneck: `{timing.get('bottleneck', 'unknown')}`; "
                    f"{bucket.get('call_count', 0)} direct calls."
                )
        lines.append("")
    return lines


def _render_batch_concurrency(batch: Mapping[str, Any]) -> List[str]:
    lines: List[str] = []
    concurrency = batch.get("concurrency")
    max_in_flight = batch.get("max_in_flight_count")
    max_started = batch.get("max_started_count")
    max_pending = batch.get("max_pending_count")
    progress_events = batch.get("progress_event_count")
    heartbeat_events = batch.get("heartbeat_event_count")

    if max_in_flight is not None:
        line = f"- Max in-flight agents observed: {max_in_flight}"
        if isinstance(concurrency, (int, float)) and isinstance(max_in_flight, (int, float)):
            if float(max_in_flight) <= float(concurrency):
                line += f" within configured concurrency {concurrency}."
            else:
                line += f" above configured concurrency {concurrency}; inspect for unexpected fan-out."
        else:
            line += "."
        lines.append(line)

    source_counts = batch.get("concurrency_source_counts")
    if isinstance(source_counts, Mapping) and source_counts:
        rendered = ", ".join(f"{key}={source_counts[key]}" for key in sorted(source_counts))
        lines.append(f"- Concurrency source counts: {rendered}.")

    progress_parts = []
    if max_started is not None:
        progress_parts.append(f"max started {max_started}")
    if max_pending is not None:
        progress_parts.append(f"max pending {max_pending}")
    if progress_events is not None:
        progress_parts.append(f"progress events {progress_events}")
    if heartbeat_events is not None:
        progress_parts.append(f"heartbeat events {heartbeat_events}")
    if progress_parts:
        lines.append(f"- Progress diagnostics: {', '.join(progress_parts)}.")

    return lines


def _render_top_phase_rows(phases: Mapping[str, Any], *, limit: int = 3) -> List[str]:
    rows = []
    phase_items = [
        (name, data)
        for name, data in phases.items()
        if isinstance(data, Mapping) and isinstance(data.get("total_sec"), (int, float))
    ]
    phase_items.sort(key=lambda item: float(item[1].get("total_sec") or 0.0), reverse=True)
    for name, data in phase_items[:limit]:
        rows.append(
            f"  - `{name}`: {_fmt_seconds(data.get('total_sec'))} total, "
            f"{_fmt_seconds(data.get('mean_sec'))} mean."
        )
    return rows


def _render_diagnostic_notes(summary: Mapping[str, Any]) -> List[str]:
    lines = ["## Interpretation Notes", ""]
    lines.append(
        "- Treat these diagnostics as evidence for where to inspect next; do not disable memory, FoVs, or actions just to make a run faster."
    )
    lines.append(
        "- If provider or queue time dominates, change provider capacity or concurrency; if action or FoV time dominates, inspect environment code and caches."
    )
    lines.append(
        "- If extractive memory is enabled, memory extraction and memory write costs are part of the faithful simulation path."
    )
    if summary.get("failed"):
        failure = summary.get("failure") if isinstance(summary.get("failure"), dict) else {}
        error_type = failure.get("error_type") or "unknown"
        lines.append(f"- The run failed with `{error_type}`; inspect `events.jsonl` before interpreting metrics.")
    lines.append("")
    return lines


def _fmt_seconds(value: Any) -> str:
    if not isinstance(value, (int, float)):
        return "unknown"
    return f"{float(value):.3f}s"


def _compact_context(row: Mapping[str, Any], *, keys: Iterable[str]) -> str:
    parts = []
    for key in keys:
        value = row.get(key)
        if value is not None:
            parts.append(f"{key}={value}")
    return ", ".join(parts)
