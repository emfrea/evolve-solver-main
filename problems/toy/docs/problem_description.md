# toy-binpack — problem description

A tiny, pure-Python, decomposable optimization problem used to exercise the whole evolve-solver loop
(feasibility repair + three separable cost components + evolve/crossover/new_species) with no external
binary and no HPC. It runs in seconds.

## What we are solving
Assign a set of **items** to a fixed number of **bins**. Each item has a `size`, a `color`, and a
`weight`. Each bin has a shared `capacity`. This is weighted bin-packing with a per-bin color limit
and load-balancing preferences.

## A solution
A mapping `assignment: {item_id -> bin_index}` placing every item into exactly one bin.

## Hard constraints (feasibility)
- Every item is assigned to a valid bin index in `[0, bins)`.
- No item id outside the instance appears in the assignment.
- **No bin's total item size exceeds `capacity`** (this is genuinely at risk on the tight instances —
  a naive packer will overflow).

An assignment breaking any of these is **infeasible** (cost undefined).

## Objective (soft costs, minimize) — three separable components
- **Overfill** — for each bin, load above a soft target (`0.75 * capacity`) is penalized. Produced by
  *how items are placed during construction*. Owned by the `construction` gene.
- **ColorConflict** — each bin exceeding `max_color_per_bin` distinct colors is penalized (heavily).
  Fixed by *a conflict-repair / search phase*. Owned by the `search_paradigm` gene.
- **Imbalance** — variance of bin loads; lower means more evenly filled bins. Produced by *the
  load-balancing / objective phase*. Owned by the `objective_strategy` gene.

Total `cost = Overfill + ColorConflict + Imbalance`. Because each component is owned by a different
genome unit, a species strong on one component makes a real crossover donor for the others.

## Notes
- Instances range from loose (t01) to exactly-full (t05, sizes sum to total capacity — packing must be
  near-perfect to stay feasible). t01/t03 are the fast gate.
- A good first solver: sort items descending by size, first/best-fit into bins, then a few local moves
  to relieve color conflicts and even out loads before the time limit.
