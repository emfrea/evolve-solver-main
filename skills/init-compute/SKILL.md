---
name: init-compute
description: >
  Configure the compute backend for an evolve-solver problem — where candidate solvers are evaluated.
  Use when the user wants to choose local vs remote SLURM execution, set up cluster evaluation, fill
  the compute block (host, remote_base, array_size, setup/submit scripts), or verify get_backend()
  builds the backend for a problem.
---

# init-compute — choose & configure the evaluation backend

The `compute:` block in `problem.yaml` decides where candidate `solve.py` programs run for a full
evaluation. Two backends implement the same `ComputeBackend` ABC (`reachable / deploy / run`), so the
rest of the framework is identical either way.

## Local (default — no cluster, runs in seconds for the toy)
```yaml
compute:
  backend: local
  time_limit: 60     # seconds per instance
  threads: 4
  seed: 57
```
`LocalBackend.reachable()` is always True and it evaluates over `problem.instances` on this machine,
writing `sol_<stem>.<ext>` into `runs/<name>/species/<sp>/eval_solutions/`. Pick this unless
evaluations are too heavy for one box.

## Remote SLURM (a cluster over SSH)
```yaml
compute:
  backend: remote_slurm
  host: user@cluster.example.edu     # must be reachable via passwordless ssh
  remote_base: /scratch/user/evolve  # working area on the cluster
  array_size: 30                     # SLURM array width (>= n_instances)
  setup_script: setup.sh             # provisions the remote env (in problems/<name>/)
  submit_script: submit.sh           # sbatch script that runs the solver over the array
  results_subdir: solutions          # where solutions land, relative to the job dir
  env: {}                            # extra env vars for the job
  time_limit: 600
  threads: 8
  seed: 57
```
It shells out to the system `ssh`/`scp` (no extra Python deps) and preserves the resilient network
retry / poll machinery. Provide `setup.sh` + `submit.sh` under `problems/<name>/` that match your
cluster's scheduler.

## Verify it constructs
```bash
python -c "import sys; sys.path.insert(0,'.'); from framework.problem import load_problem; \
from framework.compute import get_backend; b=get_backend(load_problem('<name>')); \
print(type(b).__name__, 'reachable=', b.reachable())"
```
- `local` → prints `LocalBackend reachable= True`.
- `remote_slurm` → prints `RemoteSlurmBackend ...`; `reachable=` will actually probe SSH, so run it
  from a host that can reach the cluster (or expect False when offline — construction still succeeds).

## Notes
- The autonomous loop waits (budget-preserving) when a backend is unreachable, then resumes — so a
  flaky cluster pauses rather than corrupts the campaign.
- Start `local` while designing the problem; switch to `remote_slurm` only when evaluations outgrow
  one machine.
