# Society0 4.1.10

This release consolidates the Agent runtime fixes developed after the 4.1.9 tag and reduces redundant work in transparent-proxy persistence writes. The checkpoint-v4 storage format is retained.

## Agent runtime

The integrated runtime preserves incomplete and context-limited activations, maintains same-tick Thread continuity, provides receipts for repeated reads, and reuses provider KV caches within threads. It also carries the strict single-action request contract, proxy and retry-timing corrections, and preservation of model settings during Thread memory extraction. These changes are inherited from the validated runtime baseline; the persistence optimizations do not alter them.

## Persistence writes

Runtime mapping checks use `collections.abc.Mapping`. Exact immutable JSON scalar values avoid redundant deep copies and JSON serialization. Finite floats retain validation; containers, subclasses and larger integers retain the existing copy and serialization checks. Non-finite floats and integers rejected by Python's configured JSON limits still fail at the write boundary.

## Verification

The persistence API tests cover scalar and container ownership, numeric boundaries, proxy writes and checkpoint restoration. `benchmarks/checkpoint_growth_probe.py` creates fresh growing checkpoint data and separately measures store restoration and manager root publication; it accepts no historical experiment input. Agent tool loops, complete Thread history and memory semantics are unchanged.
