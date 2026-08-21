---
name: init-evaluator
description: >
  Write or fix the evaluator.py::validate() function for an evolve-solver problem — the code that
  decides feasibility and computes the named cost components. Use when the user wants to implement
  the objective / feasibility check / scoring, "write the evaluator", parse a solver's output, or
  wrap an external checker binary. Enforces the {feasible, cost, components} contract.
---

# init-evaluator — implement `validate()`

`problems/<name>/evaluator.py::validate(instance_path, solution_path)` is the single place the
framework learns the problem's feasibility rules and objective. Everything else is blind to it.

## The contract (return exactly this shape)
```python
{
  "feasible": bool,                 # True iff EVERY hard constraint holds
  "cost": float | None,             # scalar objective; None when infeasible
  "components": {name: float, ...}, # one entry per problem.yaml `components` (lower = better)
  # optional: "violations": [str, ...], "raw": <anything>
}
```
Rules that matter:
- **Fail closed.** Unreadable/garbage/partial solution → return `feasible=False`, never raise.
- **Component keys must match** `problem.yaml` `components` exactly when feasible.
- Components should relate to `cost` (typically `cost == sum(components.values())`, or a documented
  weighting). Keep them **separable** so a solver good at one phase shows a real per-component edge.
- Deterministic: same inputs → same output.

## Steps
1. Open `problems/<name>/evaluator.py` (scaffolded from the template).
2. Implement **hard constraints** first, collecting human-readable `violations`. If any, return the
   infeasible dict immediately (`cost=None`, `components={}`).
3. Implement **soft costs**: compute each named component, then `cost = sum(...)` (or the documented
   objective). Make sure keys equal `problem.yaml` `components`.
4. **External-checker problems**: shell out to the binary here (`subprocess.run`), parse its
   stdout/exit code, and translate into the contract dict. Do the parsing in `validate`, not in the
   framework — the core must stay blind.
5. **Verify**:
   - `python scripts/smoke_evaluator.py --problem <name> --instance <stem> --empty`
     → must print an infeasible, non-crashing result.
   - On a real or hand-made solution: `... --solution <path>` → must print a valid contract dict with
     all component keys present.

## Common mistakes
- Raising on bad input instead of returning `feasible=False`.
- `components` keys drifting from `problem.yaml` (the scheduler then can't target them).
- Returning a non-None `cost` for an infeasible solution.
- Silently treating a missing solution file as feasible.
