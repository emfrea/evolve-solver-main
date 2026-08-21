---
name: init-framework
description: >
  Guided, end-to-end setup of a NEW optimization problem for the evolve-solver framework. Use this
  when the user wants to "add a problem", "onboard a problem", "set up evolve-solver for X", "start a
  new campaign", or asks how to make the LLM solver-evolution loop work on their own problem. Walks
  through problem.yaml, evaluator.py, genome.yaml, compute, and the seed population, validating each
  step, and ends with a green local dry-run.
---

# init-framework — guided problem onboarding

You are onboarding a brand-new problem into `evolve-solver` (a problem-blind, LLM-driven genetic
algorithm that evolves `solve.py` programs). Your job is to drive the whole setup as a checklist,
delegating each component to its granular skill and **verifying before moving on**. Do not write the
engine — `framework/` is generic and must stay untouched. Everything problem-specific goes under
`problems/<name>/`.

## The boundary you are filling in
A problem is fully defined by four things under `problems/<name>/`:
1. `problem.yaml` — declarative config (instances, components, splice map, solver CLI, fitness,
   scheduler tunables, genome style, compute backend).
2. `evaluator.py::validate(instance_path, solution_path)` — feasibility + component costs.
3. `genome.yaml` — the design space (structured catalog or free-form phases).
4. `docs/*.md` + `instances/*` — the spec the agents read and the benchmark files.

The framework core reads only these off a loaded `Problem`; it never learns your problem any other way.

## Procedure (do these in order, confirming each)

**0. Scope the problem.** Ask the user, if not already clear:
   - What is a solution, and what makes it *feasible* (hard constraints)?
   - What does the objective minimize, and can it be split into 2–4 **named cost components** that
     different algorithm phases are responsible for? (This is what makes crossover meaningful.)
   - How many benchmark instances, and do they already exist as files?
   - Pure-Python objective, or an external checker binary?
   - Run locally or on a SLURM cluster?

**1. Scaffold + `problem.yaml`.** Invoke the **`init-problem`** skill. It copies `problems/_template/`
   to `problems/<name>/` and fills `problem.yaml`. Then run:
   `python scripts/validate_problem_yaml.py --problem <name>` and resolve every error (warnings about
   missing instances/docs are OK to defer).

**2. Evaluator.** Invoke **`init-evaluator`**. Implement `validate()` so its `components` keys exactly
   match `problem.yaml` `components`. Verify with
   `python scripts/smoke_evaluator.py --problem <name> --instance <stem> --empty` (must fail closed)
   and again on a real/handmade solution if available.

**3. Genome.** Invoke **`init-genome`**. Choose structured vs free-form, define the catalog/phases,
   and wire the `splice` map (component → genome unit + phase). Re-run `validate_problem_yaml.py`.

**4. Compute.** Invoke **`init-compute`**. Default to `local`. For a cluster, fill the `remote_slurm`
   block. Confirm the backend constructs (the skill shows the one-liner).

**5. Instances + docs.** Make sure `instances/` holds every stem in `instances.all`, and the three
   `docs/*.md` are filled — the build agent sees only these. Re-run `validate_problem_yaml.py` until
   there are zero warnings.

**6. Seed the population.** Invoke **`seed-species`** to create ≥2 initial species (ideator-invented
   and/or hand-written seeds), each built + gated. `--skip-remote` keeps it local/cheap.

**7. Green dry-run (the finish line).** Run:
   `python scripts/run_scheduler.py --problem <name>` (should print sensible per-species decisions),
   then `python scripts/run_evolution_step.py --problem <name> --skip-remote --loop 1`
   (should complete one logged step with a `duration_s`). If both are green, onboarding is done;
   tell the user how to launch the real loop (`run_evolution_step.py --problem <name> --loop N`, or
   `campaign_tick.py` as a watchdog).

## Rules
- Never edit `framework/`. If something can't be expressed via the Problem interface, say so instead
  of hacking the core.
- Keep components genuinely separable and each owned by a distinct genome unit — otherwise the
  evolve→crossover→new_species escalation degenerates.
- Verify at every step with the bundled scripts; don't advance on unvalidated config.
