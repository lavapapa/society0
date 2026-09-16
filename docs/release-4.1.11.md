# Society0 4.1.11

This release reduces repeated work in transparent-proxy persistence writes while retaining checkpoint-v4 storage and validation semantics.

Schema path navigation is compiled from declarations. Dynamic map keys share navigation nodes, so the navigation structure does not grow with historical record IDs. Exact properties, wildcard roots, array indices and slices retain their existing validation behavior. Write-rule resolution preserves deepest-declaration and exact-match precedence with fewer intermediate allocations.

Batch preflight checks prior append-only IDs by membership and retains only the IDs introduced within the current batch. Its work no longer scales with all facts already appended during the current tick. Duplicate IDs, including duplicates within one batch, still fail before canonical mutation.

Regression tests cover dynamic-key navigation, merged roots, required fields, array writes, rule precedence and batch validation without scanning previous writes. Agent tools, Thread history, memory and activation budgets are unchanged.
