---
name: init-genome
description: >
  Define the design space (genome) for an evolve-solver problem and wire the crossover splice map.
  Use when the user wants to set up genome.yaml, choose structured (gene catalog) vs free-form
  (phase list) genome style, define species-defining genes/alleles, or connect cost components to
  the genome units that produce them so crossover can splice phases between species.
---

# init-genome — define the design space + splice map

The genome is the space the agents explore. Two styles; pick one and set `genome.style` in
`problem.yaml` to match.

## Structured (a gene catalog) — default, best when the design space is enumerable
Edit `problems/<name>/genome.yaml`. Genes live under a top-level `catalog:` key; each gene has
`species_defining` (bool), a `role` string, and `alleles` as a **dict** of name → description:
```yaml
catalog:
  search_paradigm:              # a gene
    species_defining: true      # different alleles here => different species
    role: "top-level algorithm family"
    alleles:
      greedy_local_search: "construct greedily, then hill-climb"
      simulated_annealing: "accept worse moves with a cooling probability"
      random_restart: "multi-start; keep the best"
  construction:
    species_defining: false
    role: "how an initial solution is built"
    alleles:
      first_fit: "first bin that fits"
      best_fit: "fullest bin that still fits"
```
- Mark ≥1 gene `species_defining: true` — these partition the population into species.
- `unit` names used in the `splice` map refer to **gene keys** under `catalog:`.
- `alleles` must be a **dict** (name → description), not a bare list.

## Free-form (prose phases) — best when solvers are better described than enumerated
Set `genome.style: freeform`. A species is `{name, summary, phases: [...], notes?}`; the ideator
invents them. In `genome.yaml`, list the canonical `phases:` so the `splice` map has stable unit
names to reference.

## Wire the splice map (in `problem.yaml`)
For each cost component you want crossover to be able to fix, add:
```yaml
splice:
  ColorConflict: { unit: search_paradigm, phase: "conflict-repair move phase" }
```
- `unit` = the genome unit (gene key or phase name) that **owns/produces** that component.
- `phase` = a human description the crossover prompt uses to tell the agent what to graft.
- Each component should map to a **distinct** unit — that's what makes a donor's advantage
  splice-able and keeps evolve→crossover meaningful.

## Verify
`python scripts/validate_problem_yaml.py --problem <name>` — no `[FAIL]`; the warning
"components with no splice entry" is only OK for components you deliberately never crossover.

## Guidance
- Keep the catalog small and orthogonal; every allele should be something a solver could plausibly
  do. Don't add alleles the evaluator can't distinguish.
- Structured is easier to reason about and debug; free-form gives the ideator more room. When unsure,
  start structured.
