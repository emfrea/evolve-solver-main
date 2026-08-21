# Inputs & outputs (the exact file formats)

<!-- The solver reads an instance file and writes a solution file. Both formats are defined
     HERE and enforced by evaluator.py::validate. Give a concrete, copy-pasteable example. -->

## Instance file  (`instances/<stem>.<ext>`)
TODO: describe the format. Example:

```json
{
  "TODO": "concrete example instance"
}
```

## Solution file  (written by the solver, read by the evaluator)
TODO: describe the format the solver must emit. Example:

```json
{
  "TODO": "concrete example solution for the instance above"
}
```

## Evaluator output
`validate(instance, solution)` returns:
```json
{ "feasible": true, "cost": 0.0, "components": { "TermA": 0.0, "TermB": 0.0 } }
```
