---
name: init-problem
description: >
  Scaffold a new problems/<name>/ folder and fill in problem.yaml for the evolve-solver framework.
  Use when the user wants to create the problem definition / config, "set up problem.yaml", declare
  instances, components, the splice map, fitness metric, or scheduler tunables for a new problem.
  Copies from problems/_template/ and validates the result.
---

# init-problem — scaffold & configure `problem.yaml`

Create and configure the declarative half of a problem. (The evaluator, genome, and compute each
have their own skill — this one owns the folder + `problem.yaml`.)

## Steps
1. **Pick a name** (`<name>`, kebab or snake, no spaces). Refuse if `problems/<name>/` already exists
   unless the user confirms overwrite.
2. **Copy the template**: duplicate `problems/_template/` to `problems/<name>/` (keep `evaluator.py`,
   `genome.yaml`, `docs/`, `instances/`, `seeds/` — later skills fill them).
3. **Fill `problems/<name>/problem.yaml`**, replacing every `TODO`:
   - `name`, `description`.
   - `instances`: `all` (every stem), `ext`, `gate` (1–3 fast stems ⊂ `all`), `dir` (default
     `instances`). If instance files don't exist yet, still list the intended stems and note they're
     pending.
   - `components`: 2–4 **named, separable** soft-cost terms. Each should be something a distinct
     algorithm phase influences (this is what gives donors a real edge and makes crossover meaningful).
   - `splice`: map each component you want crossover to target → `{ unit, phase }`. `unit` names a
     genome unit (a structured gene key or a freeform phase); `phase` is a human description used in
     crossover prompts. Keys must be ⊂ `components`.
   - `solver.cli`: keep the template unless the solver needs different flags; always keep
     `{instance} {output} {seed} {time_limit} {threads}`.
   - `fitness.metric`: `rank_sum` by default; `normalized_cost` if the real competition ranks by
     normalized cost (then optionally `opts.infeasible_penalty`).
   - `scheduler`: set `n_instances` = `len(instances.all)`, `full_feasible` (usually =
     `n_instances`), `donor_min_feasible` (how feasible a species must be to donate). Leave the
     attempt caps at defaults unless the user has a reason.
   - `genome.style` + `genome.file`, `compute.backend`, `libraries` — placeholders are fine here;
     the `init-genome` / `init-compute` skills finalize them.
4. **Validate**: `python scripts/validate_problem_yaml.py --problem <name>`. Fix every `[FAIL]`.
   Warnings about missing instance files / docs are acceptable at this stage.

## Tips
- `n_instances != len(instances.all)` is the most common failure — they must match exactly.
- Prefer few, strongly-separable components over many overlapping ones.
- Don't invent instances or components the user didn't describe; ask.
