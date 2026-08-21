# Architecture

## The method
An **island/speciation genetic algorithm** where the "genome" is a whole solver program and the
mutation/crossover operators are **Claude agents**. Each *species* is a distinct algorithmic approach;
its champion is a `solve.py`. A deterministic **scheduler** decides what to do next; agents carry it
out; results feed back as diagnostics.

## The loop (`scripts/run_evolution_step.py`)
```
load population (diagnostics.json per species) + history (evolution_log.json)
  → scheduler.select_next → Decision(species, operation, target_component, donor?)
  → agent executes:
        evolve      : mutate the species' champion toward the target component
        crossover   : splice the donor's phase (splice map) into the species
        new_species : ideator invents a fresh, distinct approach
  → local gate (fast feasibility check on gate instances)
  → compute backend full eval → diagnostics (feasible_count, component_totals, per_instance)
  → pluggable fitness compares candidate vs champion within the population
  → promote-or-reject; append a timestamped record to evolution_log.json
```

## The escalation ladder (`framework/scheduler.py`)
Per species, in order:
1. **Feasibility first** (with a give-up valve after `feas_repair_giveup` failed repairs).
2. **Hopeless rule** — worst-ranked, never promoted, ≥ `hopeless_attempts` real attempts → replaced
   (self-cull → new_species).
3. **Instance-targeted crossover** — a same-family donor (`crossover.family_key`) strictly better on
   ≥ `instance_xover_min_wins` instances → graft the phase owning the component where it is furthest
   ahead; the prompt names the instances to capture. Capped per (species, donor).
4. **Lever rotation** — each component has an ORDERED lever list (`levers:`, else the `splice:` owner).
   Mutation targets the first live lever; a lever dies after `evolve_max_attempts` failed REAL
   attempts or ONE deliberate no-op. Evidence expires at each promotion.
5. **Structural crossover** once every lever of the bottleneck is spent — from any cheaper donor
   (`crossover.exhausted_any_donor`; `donor_gap` gates early grafts only).
6. **Wildcard** — `wildcard_cap` free-form evolves (no prescribed lever) when every component is
   exhausted, before spending a spawn.
7. **New species** — bounded by `max_species` + `new_species_cooldown`; at the cap the worst species
   is culled (parked under `runs/<p>/species_parked/`, never deleted).
8. Otherwise a low-priority re-push flagged `exhausted`.

**Target choice** = attractiveness = `component_priors[c]` × online affinity (Laplace ratio of
total-cost-down vs -up outcomes when this species targeted `c`) × (`abs_base` + donor gap).
**Island selection** = `leader_bias^(N − rank)` (never below `explore_floor`; capped at
`exhausted_weight` for exhausted species) × `diversity_decay^(recent ops)`; feasibility repairs
always dominate. All constants: `config/limits.yaml`.

## Structural phases (`framework/phases.py`)
A problem may declare `phases:` (id, name, responsibility, boundary, owned components). Solvers
then wrap each phase in `# ==== PHASE <id> <name> START/END ====` markers; crossover extracts the
donor's block by its markers and the evolver transplants the logic, reconciling the boundary.

## The blindness boundary (`framework/problem.py`)
`load_problem(name)` merges `problems/<name>/{problem.yaml, evaluator.py, genome.yaml, docs/}` into a
`Problem`. Every generic module takes a `Problem` and reads problem facts off it — none imports a
problem constant. Instances and solutions are **opaque files** the framework only shuttles between the
solver and the evaluator; only `evaluator.py::validate` understands them. That is what makes the core
reusable across problems.

## Pluggability
- **Genome** — `StructuredGenome` (gene catalog) or `FreeformGenome` (prose phases); one protocol,
  `prompts.genome_block` is the only place style branches.
- **Fitness** — `rank_sum` or `normalized_cost`; the promotion check reads `problem.fitness_metric`.
- **Compute** — `LocalBackend` (default) or `RemoteSlurmBackend`; same `reachable/deploy/run` ABC, so
  the loop (including its reachability-wait) is backend-agnostic.

## State layout
- **Committed, immutable:** `problems/<name>/` (the definition).
- **Gitignored, mutable:** `runs/<name>/` — `species/<sp>/{solve.py, diagnostics.json, results.json,
  archive/}`, `proposals/`, `evolution_log.json`, `campaign.pid/out`. Regenerable; multiple problems
  coexist without collision.

## Integrity guards (`config/limits.yaml`)
- **Promotion gate** (`scripts/run_evolution_step.py`): candidate beats cached champion →
  *noise guard* re-evaluates the champion fresh → *collapse detector* voids the step if the
  champion's own code came back >`collapse_ratio` worse (environment fault, `eval_failed` +
  `env_collapsed`) → *ratchet*: bar = better of (cached, fresh), baseline written only upward →
  verdict under the problem's fitness metric.
- **Evidence lifetime** (`framework/scheduler.py`): attempt caps count only real attempts
  (`NON_ATTEMPT_STATUSES` never consume; one deliberate `agent_noop` retires a lever) and only
  since the species' last promotion (`_species_window`).
- **Compute** (`framework/compute/remote_slurm.py`): no `|| true` on the queue poll; empty queue
  accepted only after the expected solution count is verified (`collect_verify_retries`); remote
  results cleared before each submit; `sbatch --time` scales with the solver budget; copy-back
  retries ride out transport outages.
- **Agents** (`framework/agents/base.py`, `prompts.py`): process-tree kill on timeout; STRICT
  SCOPE clause in every prompt; `agent_elapsed_s` lets the step classify deliberate no-ops.
