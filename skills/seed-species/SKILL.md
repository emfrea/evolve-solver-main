---
name: seed-species
description: >
  Create the initial island population for an evolve-solver problem — the first few species the
  evolution loop starts from. Use when the user wants to seed / bootstrap / cold-start the
  population, invent starting solvers with the ideator, hand-write seed genomes, or build and gate
  the first solve.py programs before running the campaign.
---

# seed-species — bootstrap the initial population

Evolution needs a starting population of ≥2 **algorithmically distinct** species (distinct so donor
gaps exist and crossover + new_species are reachable). Each species is built (an agent writes
`solve.py` from the genome + docs), locally gated, then fully evaluated so it lands in
`runs/<name>/species/<sp>/` with a `diagnostics.json` the scheduler reads.

## Two ways to seed (mix freely)
**A. Ideator invents them** (fastest, needs agent quota):
```bash
python scripts/seed_species.py --problem <name> --count 3
```
The ideator proposes N distinct genomes; each is built + gated + evaluated. It is told the names
already present so inventions diverge.

**B. Hand-written seeds** (deterministic, reproducible):
- Drop genome JSON files in `problems/<name>/seeds/<sp>.json`
  (structured: `{gene: allele, ...}`; freeform: `{name, summary, phases:[...]}`).
- Then: `python scripts/seed_species.py --problem <name> --names sp-a sp-b`
  (with no `--names`/`--count`, the seeder auto-picks every `seeds/*.json`).

Add `--skip-remote` to build + gate only (no full evaluation) — cheap while iterating.

## Procedure
1. Decide the starting species with the user: aim for 2–4 that differ on a **species-defining**
   genome unit (e.g. different `search_paradigm`), so their strengths land on *different* components.
2. Run the seeder (A and/or B). Watch each species pass its gate (`feasible >= 1/len(gate)`); a
   species that fails the gate is skipped — inspect its `solve.py` and rebuild if needed.
3. Confirm the population: `python scripts/run_scheduler.py --problem <name>` should list every
   seeded species with a sensible next decision (feasibility-first if any are infeasible).
4. When ≥2 species are evaluated and in the population, hand back to `run_evolution_step.py`
   (or the `init-framework` guided flow) to start evolving.

## Guidance
- Two near-identical seeds waste the crossover mechanism — enforce diversity up front.
- If the ideator keeps producing gate-failing solvers, the docs (`docs/*.md`) or genome are probably
  under-specified; fix those (via `init-genome` / `init-problem`) rather than re-rolling.
- Keep at least one deterministic hand-written seed so the population is reproducible without quota.
