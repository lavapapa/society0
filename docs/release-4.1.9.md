# Society0 4.1.9

Society0 4.1.9 consolidates the checkpoint-v4 persistence line and the later Agent runtime fixes into one supported baseline.

- Keeps `transparent_proxy` as the default state-access mode and retains `explicit_transactions` as an optional, separately selected mode.
- Persists replaceable state and append-only facts incrementally, with World, Agent Thread, and memory paired at the complete-step boundary.
- Batches independent embedding inputs without merging their semantic content.
- Preserves the full Agent loop for repeated read-only actions; only explicit semantic endpoints and real Provider or runtime boundaries end an activation.
- Preserves Provider and tool-call failure boundaries as activation failures rather than treating truncated or exhausted output as intentional inaction.

The deterministic suite and downstream environment integration must both pass before a dependent environment updates its pinned commit.
