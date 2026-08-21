# evolve-solver

A **problem-blind**, LLM-driven **island/speciation genetic algorithm** that evolves whole solver
programs (`solve.py`). Claude agents act as the mutation and crossover operators; a deterministic
escalation scheduler decides, per species, whether to *evolve*, *crossover* (splice a rival's phase),
or spawn a *new species* — always feasibility-first, always attacking the current bottleneck component.

The framework core knows **nothing** about any specific problem. Every problem fact — instances,
feasibility rules, cost components, the design space, the compute backend — lives behind one interface:
a declarative `problems/<name>/problem.yaml` plus one `evaluator.py::validate()`. Point it at a new
problem and the same machinery evolves solvers for it.

```
                 ┌──────────────── scheduler ───────────────┐
   population →  │ feasibility-first · bottleneck component  │ → Decision
   (diagnostics) │ evolve → crossover → new_species ladder   │   (species, op, target, donor)
                 └───────────────────────────────────────────┘
                                    │
                     Claude agent (evolve / crossover / ideate)
                                    │  writes solve.py
                     local gate → compute backend eval → diagnostics
                                    │
                     pluggable fitness → promote-or-reject → evolution_log.json
```

## Why "blind"

`framework/` never imports a problem constant — grep it for any problem name and nothing leaks. The
coupling that a normal solver framework bakes in (the objective, the feasibility check, the benchmark
set) is pushed entirely behind `problems/<name>/`. Adding a problem is a **data + one function** job,
not a code change to the engine.

## Layout

```
config/limits.yaml    every cap / timeout / guard threshold, in one place (per-problem override: `limits:`)
framework/            generic engine (problem-agnostic)
  problem.py          the boundary: load_problem(name) -> Problem
  scheduler.py        evolve/crossover/new_species escalation policy
  genome.py           dual design-space: structured catalog OR free-form phases
  fitness.py          pluggable: rank_sum | normalized_cost
  runner.py           solver CLI + evaluate over instances
  diagnostics.py      per-species feasibility + component totals
  compute/            LocalBackend (default) · RemoteSlurmBackend (SSH/SLURM)
  agents/             base (claude wrapper) · prompts · builder · evolver · ideator
problems/
  _template/          copy this to start a new problem
  toy/                toy-binpack: pure-Python, runs in seconds, no HPC
runs/<problem>/       mutable campaign state (gitignored): species/, evolution_log.json
scripts/              run_evolution_step · seed_species · build_and_eval · campaign_tick · ...
skills/               Claude skills that scaffold each component
```

## Quick start (the toy problem, no HPC)

```bash
pip install -e .
cp .env.example .env          # put your CLAUDE_CODE_OAUTH_TOKEN in .env

# 1. Create an initial population (agentic; needs the claude CLI):
python scripts/seed_species.py --problem toy --count 2 --skip-remote

# 2. Run the evolution loop locally:
python scripts/run_evolution_step.py --problem toy --skip-remote --loop 8

# Inspect:
python scripts/run_scheduler.py --problem toy        # what would it do next?
python scripts/productive_time.py --problem toy      # agent-working time summary
```

Two hand-written seed solvers ship under `runs/toy/species/` so you can exercise the scheduler,
diagnostics, and fitness end-to-end **without spending any agent quota** (`run_scheduler.py`,
`build_and_eval.py --skip-build`).

## Guards that keep a campaign honest

- **Verified collects** — a SLURM array counts as finished only after the expected number of
  solution files is verified remotely (transport failures are never read as "queue empty").
- **Noise guard + ratchet** — before a promotion the champion is re-evaluated fresh; the bar
  is the better of its cached and fresh results and only ever moves up.
- **Environment-collapse detector** — a fresh champion eval far worse than its cache voids the
  step (licence tokens, node faults) instead of judging a poisoned verdict.
- **Status taxonomy** — deliberate agent no-ops count as attempts; agent crashes and
  infrastructure failures are journaled but never exhaust a lever; evidence expires at each
  promotion.
- **Sandbox scope** — every prompt ends with a STRICT SCOPE clause and timed-out agents are
  killed as a process tree.

All thresholds live in `config/limits.yaml`.

## Add your own problem

Use the Claude skills (they scaffold from `problems/_template/` and validate as they go):

- **`init-framework`** — guided master skill; walks you through all of the below.
- **`init-problem`** — scaffold `problems/<name>/` and `problem.yaml`.
- **`init-evaluator`** — write `validate()` (feasibility + component costs).
- **`init-genome`** — choose structured vs free-form; wire the splice map.
- **`init-compute`** — local vs remote SLURM.
- **`seed-species`** — invent / hand-write the initial island population.

Or do it by hand: copy `problems/_template/`, fill in `problem.yaml`, implement `evaluator.py::validate`,
define `genome.yaml`, drop instances into `instances/`, then `seed_species.py` + `run_evolution_step.py`.

The one contract your `validate(instance_path, solution_path)` must honor:

```python
{
  "feasible": bool,                 # did the solution satisfy all hard constraints?
  "cost": float | None,             # scalar objective (None if infeasible)
  "components": {name: float, ...}, # per named soft-cost term (keys == problem.yaml `components`)
}
```

## Security

`.env` (with your OAuth token) and everything matching `*token*` / `*.oat` are gitignored. Never commit
credentials. `runs/` is gitignored too — it is regenerable campaign state.
