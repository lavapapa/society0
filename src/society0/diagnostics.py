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
    lines.extend(_render_environment_capabilities(summary.get("capabilities") or {}))
    lines.extend(_render_env_hooks(summary.get("events", {}).get("env_hooks") or {}))
    lines.extend(_render_social_recommendations(summary.get("events", {}).get("social_recommendations") or {}))
    lines.extend(_render_logic_executions(summary.get("events", {}).get("logic_executions") or {}))
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


def _render_environment_capabilities(capabilities: Mapping[str, Any]) -> List[str]:
    if not capabilities:
        return []

    lines = ["## Environment And Capabilities", ""]
    environment_type = capabilities.get("environment_type")
    if environment_type:
        lines.append(f"- Environment: `{environment_type}`.")

    counts = _count_mapping(capabilities.get("counts"))
    if counts:
        lines.append(f"- Capability counts: {_format_counts(counts)}.")

    by_source = capabilities.get("by_source")
    if isinstance(by_source, Mapping) and by_source:
        rendered_sources = []
        for source in sorted(by_source):
            source_counts = _count_mapping(by_source.get(source))
            if source_counts:
                rendered_sources.append(f"{source} {_format_counts(source_counts)}")
        if rendered_sources:
            lines.append(f"- By source: {'; '.join(rendered_sources)}.")

    by_kind = capabilities.get("by_kind")
    if isinstance(by_kind, Mapping) and by_kind:
        for kind in ("fovs", "actions", "rules", "behaviors"):
            names = _capability_names(by_kind.get(kind))
            if names:
                lines.append(f"- Sample {kind}: {_format_name_sample(names)}.")

    lines.append("")
    return lines


def _render_env_hooks(env_hooks: Mapping[str, Any]) -> List[str]:
    if not env_hooks:
        return []

    lines = ["## Environment Hooks", ""]
    for name, hook in sorted(env_hooks.items()):
        if not isinstance(hook, Mapping):
            continue
        lines.append(f"### {name}")
        lines.append("")
        environment_type = hook.get("environment_type")
        prefix = f"- Environment: `{environment_type}`; " if environment_type else "- "
        lines.append(
            prefix
            + "started/completed/failed "
            f"{hook.get('started_count', 0)}/{hook.get('completed_count', 0)}/{hook.get('failed_count', 0)}; "
            f"total {_fmt_seconds(hook.get('duration_sec_total'))}."
        )
        by_tick = hook.get("by_tick")
        if isinstance(by_tick, Mapping) and by_tick:
            ticks = ", ".join(str(tick) for tick in sorted(by_tick, key=str))
            lines.append(f"- Tick coverage: {ticks}.")
        error_samples = hook.get("error_samples")
        if isinstance(error_samples, list) and error_samples:
            sample = error_samples[0]
            if isinstance(sample, Mapping):
                step = sample.get("step")
                suffix = f" at step {step}" if step is not None else ""
                lines.append(f"- Error sample: {_format_error_sample(sample)}{suffix}.")
        lines.append("")
    return lines


def _render_social_recommendations(recommendations: Mapping[str, Any]) -> List[str]:
    if not recommendations:
        return []

    lines = ["## Social Recommendation Diagnostics", ""]
    trace_count = recommendations.get("trace_count", 0)
    flush_count = recommendations.get("flush_count", 0)
    unique_agents = recommendations.get("unique_agent_count", 0)

    if trace_count:
        lines.append(
            "- Recommendation traces: "
            f"{trace_count}; agents {unique_agents}; "
            f"raw candidates avg/max {recommendations.get('raw_candidate_count_avg', 0)}/"
            f"{recommendations.get('raw_candidate_count_max', 0)}; "
            f"active pool avg/max {recommendations.get('active_pool_count_avg', 0)}/"
            f"{recommendations.get('active_pool_count_max', 0)}; "
            f"returned avg/max {recommendations.get('returned_count_avg', 0)}/"
            f"{recommendations.get('returned_count_max', 0)}."
        )
        lines.append(
            "- Recommendation side effects requested by traces: "
            f"impression traces {recommendations.get('record_impression_count', 0)}, "
            f"recommended-state traces {recommendations.get('record_recommended_state_count', 0)}, "
            f"preview traces {recommendations.get('preview_count', 0)}."
        )
        lines.append(
            "- Recommendation cache during rendering: "
            f"rebuilds {recommendations.get('cache_rebuilds_total', 0)}; "
            f"ranking {_fmt_seconds(recommendations.get('rank_duration_sec_total'))}; "
            f"rendering {_fmt_seconds(recommendations.get('duration_sec_total'))}; "
            f"max output chars {recommendations.get('output_characters_max', 0)}."
        )

    if flush_count:
        lines.append(
            "- Deferred recommendation flushes: "
            f"{flush_count}; impression delta total {recommendations.get('impression_delta_total', 0)}; "
            f"posts touched {recommendations.get('impression_post_count_total', 0)}; "
            f"agent recommendation updates {recommendations.get('recommended_agent_update_count', 0)}; "
            f"state patches {recommendations.get('state_patch_count', 0)}."
        )

    by_tick = recommendations.get("by_tick")
    if isinstance(by_tick, Mapping) and by_tick:
        ticks = ", ".join(str(tick) for tick in sorted(by_tick, key=str))
        lines.append(f"- Tick coverage: {ticks}.")

    score_samples = recommendations.get("score_samples")
    if isinstance(score_samples, list) and score_samples:
        sample = score_samples[0]
        if isinstance(sample, Mapping):
            context = _compact_context(sample, keys=("tick", "agent_id", "rank", "post_id"))
            lines.append(
                "- Top score sample: "
                + (f"{context}; " if context else "")
                + f"total={sample.get('total_score', 'unknown')}, "
                f"engagement={sample.get('engagement_contribution', 'unknown')}, "
                f"time={sample.get('time_contribution', 'unknown')}, "
                f"network={sample.get('network_contribution', 'unknown')}, "
                f"semantic={sample.get('semantic_contribution', 'unknown')}."
            )

    lines.append("")
    return lines


def _render_logic_executions(logic_executions: Mapping[str, Any]) -> List[str]:
    if not logic_executions:
        return []

    lines = ["## Rules And Behaviors", ""]
    for name, execution in sorted(logic_executions.items()):
        if not isinstance(execution, Mapping):
            continue
        lines.append(f"### {name}")
        lines.append("")
        logic_kind = execution.get("logic_kind")
        prefix = f"- Kind: `{logic_kind}`; " if logic_kind else "- "
        line = (
            prefix
            + "started/completed/failed "
            f"{execution.get('started_count', 0)}/{execution.get('completed_count', 0)}/{execution.get('failed_count', 0)}; "
            f"success/error {execution.get('success_count', 0)}/{execution.get('error_count', 0)}"
        )
        agent_count = execution.get("agent_count_total")
        if isinstance(agent_count, int) and agent_count:
            line += f"; agents {agent_count}"
        line += f"; total {_fmt_seconds(execution.get('duration_sec_total'))}."
        lines.append(line)

        param_keys = execution.get("param_keys")
        if isinstance(param_keys, list) and param_keys:
            lines.append(f"- Params: {', '.join(str(key) for key in param_keys)}.")

        by_tick = execution.get("by_tick")
        if isinstance(by_tick, Mapping) and by_tick:
            ticks = ", ".join(str(tick) for tick in sorted(by_tick, key=str))
            lines.append(f"- Tick coverage: {ticks}.")

        error_samples = execution.get("error_samples")
        if isinstance(error_samples, list) and error_samples:
            sample = error_samples[0]
            if isinstance(sample, Mapping):
                lines.append(f"- Error sample: {_format_error_sample(sample)}.")
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
        lines.extend(_render_batch_action_semantics(batch))

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
            lines.extend(_render_action_error_samples(action_errors))

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


def _render_batch_action_semantics(batch: Mapping[str, Any]) -> List[str]:
    lines: List[str] = []
    action_counts = _count_mapping(batch.get("action_counts"))
    successful_action_counts = _count_mapping(batch.get("successful_action_counts"))
    failed_action_counts = _count_mapping(batch.get("failed_action_counts"))
    action_tag_counts = _count_mapping(batch.get("action_tag_counts"))
    termination_reason_counts = _count_mapping(batch.get("termination_reason_counts"))

    if action_counts or successful_action_counts or failed_action_counts:
        lines.append(
            "- Actions: "
            f"attempted {_format_counts(action_counts)}; "
            f"successful {_format_counts(successful_action_counts)}; "
            f"failed {_format_counts(failed_action_counts)}."
        )
    if action_tag_counts:
        lines.append(f"- Successful action tags: {_format_counts(action_tag_counts)}.")
    if termination_reason_counts:
        lines.append(f"- Termination reasons: {_format_counts(termination_reason_counts)}.")

    action_semantics = batch.get("action_semantics")
    if isinstance(action_semantics, Mapping) and action_semantics:
        rendered_groups = []
        for name in sorted(action_semantics):
            group = action_semantics.get(name)
            if not isinstance(group, Mapping):
                continue
            configured = group.get("configured")
            observed_counts = _count_mapping(group.get("observed_counts"))
            if not configured and not observed_counts:
                continue
            configured_text = _format_list(configured) if isinstance(configured, list) else "[]"
            rendered_groups.append(
                f"{name} configured {configured_text}, observed {_format_counts(observed_counts)}"
            )
        if rendered_groups:
            lines.append(f"- Action semantics: {'; '.join(rendered_groups)}.")

    return lines


def _render_action_error_samples(action_errors: List[Any], *, limit: int = 3) -> List[str]:
    lines: List[str] = []
    for sample in action_errors[:limit]:
        if not isinstance(sample, Mapping):
            continue
        context = _compact_context(sample, keys=("agent_id", "action_name", "status"))
        error = _compact_text(sample.get("error") or sample.get("result") or "unknown")
        line = f"  - Sample: {context}; error={error}." if context else f"  - Sample: error={error}."
        arguments = sample.get("arguments")
        if isinstance(arguments, Mapping) and arguments:
            line += f" Arguments: {_format_mapping_sample(arguments)}."
        lines.append(line)
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
        details = f"`{error_type}`"
        if failure.get("failed_step") is not None:
            details += f" at step {failure.get('failed_step')}"
        if failure.get("error"):
            details += f": {failure.get('error')}"
        lines.append(f"- The run failed with {details}; inspect `events.jsonl` before interpreting metrics.")
    lines.append("")
    return lines


def _fmt_seconds(value: Any) -> str:
    if not isinstance(value, (int, float)):
        return "unknown"
    return f"{float(value):.3f}s"


def _count_mapping(value: Any) -> Dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    return {str(key): count for key, count in sorted(value.items(), key=lambda item: str(item[0]))}


def _format_counts(counts: Mapping[str, Any]) -> str:
    if not counts:
        return "none"
    return ", ".join(f"{key}={counts[key]}" for key in sorted(counts))


def _format_list(value: List[Any]) -> str:
    return "[" + ", ".join(str(item) for item in value) + "]"


def _format_mapping_sample(value: Mapping[str, Any], *, limit: int = 4) -> str:
    parts = []
    for key in sorted(value, key=str)[:limit]:
        parts.append(f"{key}={_compact_text(value.get(key))}")
    remaining = len(value) - limit
    if remaining > 0:
        parts.append(f"+{remaining} more")
    return ", ".join(parts)


def _compact_text(value: Any, *, limit: int = 120) -> str:
    text = str(value)
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def _format_error_sample(sample: Mapping[str, Any]) -> str:
    error_type = sample.get("error_type") or "Error"
    error = sample.get("error") or "unknown"
    agent_id = sample.get("agent_id")
    suffix = f" (agent_id={agent_id})" if agent_id is not None else ""
    return f"{error_type}: {error}{suffix}"


def _capability_names(value: Any) -> List[str]:
    if not isinstance(value, list):
        return []
    names = []
    for entry in value:
        if isinstance(entry, Mapping) and entry.get("name"):
            names.append(str(entry.get("name")))
    return sorted(dict.fromkeys(names))


def _format_name_sample(names: List[str], *, limit: int = 8) -> str:
    if len(names) <= limit:
        return ", ".join(names)
    shown = ", ".join(names[:limit])
    return f"{shown}, +{len(names) - limit} more"


def _compact_context(row: Mapping[str, Any], *, keys: Iterable[str]) -> str:
    parts = []
    for key in keys:
        value = row.get(key)
        if value is not None:
            parts.append(f"{key}={value}")
    return ", ".join(parts)
