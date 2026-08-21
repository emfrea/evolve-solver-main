# toy-binpack — solver contract

Produce a single self-contained `solve.py` invoked as:

```
python solve.py <instance.json> <output.json> --seed S --time-limit T --threads N [--progress-log P]
```

Requirements:
- Read the instance from `argv[1]`; write `{"assignment": {item_id: bin_index}}` to `argv[2]`.
- **Always write a feasible-if-possible solution.** Assign every item to a bin in `[0, bins)` without
  exceeding `capacity`. On the tight instances (t04/t05) this needs care — pack largest items first.
- **Respect `--time-limit`** (seconds): flush the best assignment found so far before it elapses.
- **Determinism**: seed Python's `random` from `--seed`.
- **Standard library only** (`json`, `random`, `math`, `heapq`, ...). No third-party packages.
- Must finish on the gate instances (t01, t03) within a couple of seconds — it is smoke-tested short
  before any full evaluation.

Optimization priorities once feasible (lower is better): reduce `Overfill` (don't cram bins past
`0.75*capacity` when avoidable), eliminate `ColorConflict` (keep ≤ `max_color_per_bin` distinct colors
per bin), then minimize `Imbalance` (even out bin loads). Which of these a given species emphasizes is
governed by its genome (`construction`, `search_paradigm`, `objective_strategy`).
