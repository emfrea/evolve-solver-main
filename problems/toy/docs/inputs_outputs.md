# toy-binpack — inputs & outputs

## Instance file  (`instances/<stem>.json`)
```json
{
  "bins": 2,
  "capacity": 10,
  "max_color_per_bin": 2,
  "items": [
    {"id": "x1", "size": 3, "color": "r", "weight": 1.0},
    {"id": "x2", "size": 3, "color": "b", "weight": 1.0},
    {"id": "x3", "size": 4, "color": "r", "weight": 1.0},
    {"id": "x4", "size": 4, "color": "b", "weight": 1.0},
    {"id": "x5", "size": 3, "color": "g", "weight": 1.0},
    {"id": "x6", "size": 3, "color": "g", "weight": 1.0}
  ]
}
```
- `bins` — number of available bins (indices `0 .. bins-1`).
- `capacity` — max total item `size` per bin (hard).
- `max_color_per_bin` — soft: exceeding it accrues `ColorConflict`.
- `items[].size` counts toward capacity; `color` toward the color limit; `weight` is available for a
  solver's heuristics (not scored directly).

## Solution file  (written by the solver, read by the evaluator)
```json
{ "assignment": { "x1": 0, "x2": 1, "x3": 0, "x4": 1, "x5": 0, "x6": 1 } }
```
Every item id must appear, mapped to an integer bin index in `[0, bins)`.

## Evaluator output
```json
{ "feasible": true, "cost": 12.5,
  "components": { "Overfill": 0.0, "ColorConflict": 0.0, "Imbalance": 12.5 } }
```
Infeasible solutions return `{"feasible": false, "cost": null, "components": {}}`.
