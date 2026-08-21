# Solver contract

The agent must produce a single self-contained `solve.py` invoked as:

```
{python} solve.py {instance} {output} --seed {seed} --time-limit {time_limit} --threads {threads} [--progress-log {progress_log}]
```

Requirements:
- **Read** the instance from `argv[1]`, **write** a valid solution to `argv[2]` (the format in
  `inputs_outputs.md`). Always write *something* well-formed, even under time pressure.
- **Respect `--time-limit`** (seconds): stop searching and flush the best solution found so far
  before it elapses. Exceeding it risks a killed process and a missing solution file.
- **Determinism**: seed all RNGs from `--seed` so a rerun reproduces the result.
- **Feasibility first**: a feasible-but-mediocre solution beats an infeasible clever one — the
  scheduler repairs feasibility before optimizing components.
- **Libraries**: only what `problem.yaml` `libraries` allows.
- Must run start-to-finish on the gate instances within a few seconds (it is smoke-tested short
  before any full evaluation).
