# Society0 Core Development Principles

This repository contains the standalone `society0` simulation library. Keep research mechanisms domain independent and preserve the information, actions and recorded facts needed for each study.

## Public runtime

Use Plugin, compose, Actor/Driver, Information/Actions and Schedule through RunPlan/run_plan. All actors share an environment; mechanism plugins are internal components of that environment. Schema initialization precedes service installation. Declare actual service dependencies and resource ownership explicitly; data schema prerequisites and service installation order have distinct meanings.

Rule runs should remain a lightweight path. Optional LLM, memory, social, datasets and shell dependencies are imported by their actual consumers. The public package stays provider neutral; credentials and service addresses belong to the explicit runtime environment, never source or public artifacts.

## Agent integrity

LLM behavior uses the complete LLMDriver loop. Retain action discovery, parameter schemas, required actions, success-based completion, action budgets, reasoning guidance and original provider fields. Interview mode is a structured measurement path. Accepted actions, completed actions, waiting and incomplete activations remain distinct. Budget exhaustion or length truncation is incomplete, with original diagnostics preserved.

Keep the full Thread. Repeated activation in one Moment retains its interaction identity and incremental perception cursor. Paging improves discovery while preserving totals and access to all original content. Memory writing, recall and active tools are independent activation policies. Preserve explicit experience extraction when the research protocol requires it, within the original Thread lifecycle.

## State and concurrency

Trusted mechanism writers update authority, current projection and necessary indexes together using native SQL transactions. Immutable data and workspace files have explicit references covered by complete steps. Hot queries use current indexes rather than replaying history. Large bodies use range reads and declared references; requesting a full object retains its actual materialization cost.

Serial phases preserve business order. Independent phases use explicit Phase.capacity or Runtime.capacity; endpoint and shared external-request limits separately control physical requests. Resource cleanup must drain owned tasks before closing dependencies. Complete steps publish after required work and artifacts have settled; recovery creates a new run from a trustworthy complete point.

## Changes and evidence

Use specifications and failure-first tests for substantial work, reuse mature libraries and existing code, and keep interfaces tied to real consumers. Breaking interface changes do not require compatibility layers. Preserve semantic comparison fixtures under tests/reference with their source commit identity. Historical research and old format evidence retain their version context.

Run focused deterministic tests followed by the full deterministic suite. Independent review must challenge actual consumers and failure boundaries. Performance evidence should separate compute, service waiting, encoding, storage, Python/native memory and cold/hot behavior. Compare identical information and business semantics; fewer messages or tools are not sufficient evidence of improvement.

Real endpoint tests use the explicit SOCIETY0_REAL_* environment contract in tests/e2e/core_next_real_support.py. They validate provider parameters, tool execution, memory and process recovery; deterministic fixtures do not substitute for live evidence. Maintain the capability matrix and task checklist with actual evidence and remaining scope. Publish only when release metadata, source identity, dependencies and validation agree.
