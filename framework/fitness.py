"""Pluggable population fitness. Two metrics, same row shape, selected per problem.

  rank_sum         -- population RANK-SUM (infeasible = last on that instance). Weights every
                      instance equally; a cheaper near-feasible solver can out-rank a feasible
                      mediocre one. (Ported verbatim from gene-search.)
  normalized_cost  -- mean over instances of cost_i / best_i, + penalty per infeasible instance.
                      Mirrors competition metrics (e.g. ROADEF average normalized score), where
                      cost MAGNITUDE matters. Use this when your ranking is normalized cost.

score_population(named_results, metric, opts) dispatches and returns rows sorted best-first:
    {"name", "score", "feasible_count", "total_cost", "rank"}
"""
from __future__ import annotations

from typing import Optional, Sequence


def best_per_instance(all_results: Sequence[Sequence[Optional[float]]]) -> list[Optional[float]]:
    """Cheapest feasible cost per instance across the population (None if all infeasible)."""
    if not all_results:
        return []
    n = max(len(r) for r in all_results)
    best: list[Optional[float]] = [None] * n
    for i in range(n):
        feas = [r[i] for r in all_results if i < len(r) and r[i] is not None]
        if feas:
            best[i] = float(min(feas))
    return best


def feasible_count(results: Sequence[Optional[float]]) -> int:
    return sum(1 for r in results if r is not None)


def _rank_sum(named_results: dict[str, Sequence[Optional[float]]], **_) -> dict[str, float]:
    names = list(named_results)
    n_inst = max((len(named_results[m]) for m in names), default=0)
    score: dict[str, float] = {m: 0 for m in names}
    for i in range(n_inst):
        col = [(m, (named_results[m][i] if i < len(named_results[m]) else None)) for m in names]
        col = [(m, float("inf") if c is None else float(c)) for m, c in col]
        col.sort(key=lambda mc: mc[1])
        rank = 0
        prev: Optional[float] = None
        for pos, (m, c) in enumerate(col, start=1):
            if prev is None or c != prev:   # competition (min) ranking: ties share a rank
                rank = pos
                prev = c
            score[m] += rank
    return score


def _normalized_cost(named_results: dict[str, Sequence[Optional[float]]],
                     infeasible_penalty: float = 1000.0, **_) -> dict[str, float]:
    names = list(named_results)
    best = best_per_instance([named_results[m] for m in names])
    score: dict[str, float] = {}
    for m in names:
        res = named_results[m]
        total = 0.0
        for i in range(len(best)):
            val = res[i] if i < len(res) else None
            b = best[i]
            total += (float(val) / b) if (val is not None and b) else infeasible_penalty
        score[m] = total
    return score


_METRICS = {"rank_sum": _rank_sum, "normalized_cost": _normalized_cost}


def score_population(named_results: dict[str, Sequence[Optional[float]]],
                     metric: str = "rank_sum", opts: dict | None = None) -> list[dict]:
    """Rank solvers by the chosen metric (ascending; lower is better). Returns rows best-first."""
    fn = _METRICS[metric]
    scores = fn(named_results, **(opts or {}))
    rows = []
    for name, res in named_results.items():
        rows.append({
            "name": name,
            "score": round(scores[name], 4),
            "feasible_count": feasible_count(res),
            "total_cost": sum(r for r in res if r is not None),
        })
    rows.sort(key=lambda r: (r["score"], -r["feasible_count"]))
    for pos, row in enumerate(rows, start=1):
        row["rank"] = pos
    return rows


# Back-compat alias (some callers/tests refer to rank_population).
def rank_population(named_results):
    return score_population(named_results, "rank_sum")
