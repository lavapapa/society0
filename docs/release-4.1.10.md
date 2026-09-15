# Society0 4.1.10

This patch release reduces redundant work in transparent-proxy persistence writes while retaining the Agent runtime and checkpoint-v4 format from 4.1.9.

## Persistence writes

Runtime mapping checks use `collections.abc.Mapping`. Exact immutable JSON scalar values avoid redundant deep copies and JSON serialization. Finite floats retain validation; containers, subclasses and larger integers retain the existing copy and serialization checks. Non-finite floats and integers rejected by Python's configured JSON limits still fail at the write boundary.

## Verification

The persistence API tests cover scalar and container ownership, numeric boundaries, proxy writes and checkpoint restoration. `benchmarks/checkpoint_growth_probe.py` creates fresh growing checkpoint data and separately measures store restoration and manager root publication; it accepts no historical experiment input. Agent tool loops, complete Thread history and memory semantics are unchanged.
