"""RemoteSlurmBackend: deploy a solver to an SSH host and run a SLURM array over the
instance set, then copy solutions back. Network-resilience guarantees:

  * transport failures (ssh rc != 0) are never read as "queue empty" -- a controller hiccup
    used to return rc=0 + empty output (`|| true`) and trigger a PREMATURE collect of partial
    results, which were then judged as infeasible;
  * an empty queue is accepted only after the EXPECTED solution count is verified remotely
    (re-checked `collect_verify_retries` times);
  * the remote results dir is cleared before every submit, so stale files from a previous
    run under the same name can never satisfy the count;
  * `sbatch --time=` scales with the requested solver budget (a static #SBATCH --time sized
    for short runs SIGKILLs longer evaluations before they write solutions);
  * results persist remotely, so the copy-back rides out a multi-minute VPN drop.

All caps come from config/limits.yaml (`compute:`), overridable per problem. Host / remote
base / array size / script names / env-var names are read from problem.compute:

  compute:
    backend: remote_slurm
    host: user@cluster.example
    remote_base: "~/es_runs/{problem}"
    array_size: 30
    setup_script: automation/setup.sh          # relative to problems/<name>/
    submit_script: automation/submit.sh
    results_subdir: solutions                   # remote dir the array writes sol_*.<ext> into
    env: { seed_var: RUN_SEED, time_var: RUN_TIME }
    connect_timeout: 10
"""
from __future__ import annotations

import re
import subprocess
import time
from pathlib import Path
from typing import Callable, Optional

from .base import ComputeBackend

# Fallbacks only; the effective values come from problem.config.limits["compute"].
POLL_INTERVAL = 30
MAX_RUN_WAIT = 2 * 3600
MAX_POLL_FAILURES = 5
SCP_RETRIES = 20
SCP_RETRY_DELAY = 60
NET_RETRIES = 8
NET_RETRY_DELAY = 30


def _run(cmd: list[str], check: bool = True):
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if check and proc.returncode != 0:
        print(f"    [cmd] FAIL: {' '.join(cmd[:4])}...")
        print(f"           {(proc.stderr or '')[:300]}")
    return proc.returncode, proc.stdout or "", proc.stderr or ""


def _net_retry(make_call: Callable[[], tuple], label: str,
               attempts: int = NET_RETRIES, delay: int = NET_RETRY_DELAY):
    """Retry an IDEMPOTENT network command while it fails (mkdir/scp/setup). Never use for
    side-effecting commands like sbatch."""
    result = None
    for attempt in range(1, attempts + 1):
        result = make_call()
        if result[0] == 0:
            return result
        if attempt < attempts:
            print(f"    [net] {label}: rc={result[0]}, retry {attempt}/{attempts - 1} in {delay}s...")
            time.sleep(delay)
    return result


class RemoteSlurmBackend(ComputeBackend):
    def __init__(self, problem):
        super().__init__(problem)
        c = problem.config.compute
        self.host = c["host"]
        self.remote_base = c.get("remote_base", "~/es_runs/{problem}").format(problem=problem.name)
        self.array_size = int(c.get("array_size", len(problem.instances)))
        self.setup_script = c.get("setup_script")
        self.submit_script = c.get("submit_script")
        self.results_subdir = c.get("results_subdir", "solutions")
        self.env = c.get("env", {})
        self.connect_timeout = int(c.get("connect_timeout", 10))
        self.ext = problem.config.inst_ext
        lim = dict(problem.config.limits.get("compute", {}))
        self.poll_interval = int(lim.get("poll_interval_s", POLL_INTERVAL))
        self.max_run_wait = int(lim.get("max_run_wait_s", MAX_RUN_WAIT))
        self.max_poll_failures = int(lim.get("max_poll_failures", MAX_POLL_FAILURES))
        self.collect_verify_retries = int(lim.get("collect_verify_retries", 3))
        self.collect_verify_wait = int(lim.get("collect_verify_wait_s", 60))
        self.scp_retries = int(lim.get("scp_retries", SCP_RETRIES))
        self.scp_retry_delay = int(lim.get("scp_retry_delay_s", SCP_RETRY_DELAY))
        self.set_walltime = bool(lim.get("set_walltime", True))
        self.walltime_margin = int(lim.get("walltime_margin_s", 600))

    def _ssh(self, cmd: str):
        return _run(["ssh", self.host, cmd])[:2]

    def reachable(self) -> bool:
        rc, _, _ = _run(["ssh", "-o", f"ConnectTimeout={self.connect_timeout}", self.host, "echo ok"],
                        check=False)
        return rc == 0

    def deploy(self, name: str, solver_dir: Path) -> bool:
        solver_dir = Path(solver_dir)
        remote_dir = f"{self.remote_base}/{name}"
        solve_src = solver_dir / self.problem.config.solver_entry
        if not solve_src.exists():
            print(f"    [remote] no {solve_src}")
            return False
        print(f"    [remote] deploying {name} to {self.host}:{remote_dir}")
        rc, _ = _net_retry(lambda: self._ssh(
            f"mkdir -p {remote_dir}/instances {remote_dir}/{self.results_subdir} {remote_dir}/automation"),
            "mkdir")
        if rc != 0:
            return False
        rc, _, _ = _net_retry(lambda: _run(["scp", str(solve_src), f"{self.host}:{remote_dir}/"]),
                              "scp solver")
        if rc != 0:
            return False
        for stem in self.problem.instances:
            f = self.problem.instance_path(stem)
            rc, _, _ = _net_retry(lambda f=f: _run(["scp", str(f), f"{self.host}:{remote_dir}/instances/"]),
                                  f"scp {f.name}")
            if rc != 0:
                return False
        for key in (self.setup_script, self.submit_script):
            if key:
                src = self.problem.config.root / key
                if src.exists():
                    _net_retry(lambda s=src: _run(["scp", str(s), f"{self.host}:{remote_dir}/automation/"]),
                               f"scp {src.name}")
        _net_retry(lambda: self._ssh(f"chmod +x {remote_dir}/automation/* 2>/dev/null || true"), "chmod")
        print("    [remote] deploy done.")
        return True

    def run(self, name: str, solver_dir: Path, *, instances, seed, time_limit, threads) -> Optional[Path]:
        solver_dir = Path(solver_dir)
        remote_dir = f"{self.remote_base}/{name}"
        local_out = solver_dir / "eval_solutions"
        local_out.mkdir(parents=True, exist_ok=True)

        if self.setup_script:
            print("    [remote] running setup...")
            rc, _ = _net_retry(lambda: self._ssh(
                f"cd {remote_dir} && bash automation/{Path(self.setup_script).name}"), "setup")
            if rc != 0:
                return None

        seed_var = self.env.get("seed_var", "RUN_SEED")
        time_var = self.env.get("time_var", "RUN_TIME")
        submit = f"automation/{Path(self.submit_script).name}" if self.submit_script else "automation/submit.sh"
        expected = len(list(instances))
        results = f"{remote_dir}/{self.results_subdir}"

        # Clear stale results (the same name is reused step after step) so the count check
        # below can only be satisfied by THIS run's files.
        _net_retry(lambda: self._ssh(f"mkdir -p {results} && rm -f {results}/sol_*.{self.ext}"),
                   "clear results")

        wall_s = int(time_limit) + self.walltime_margin
        walltime = f"{wall_s // 3600:02d}:{(wall_s % 3600) // 60:02d}:{wall_s % 60:02d}"
        time_opt = f"--time={walltime} " if self.set_walltime else ""
        print(f"    [remote] submitting SLURM array (size={self.array_size}, seed={seed}, {time_limit}s"
              + (f", walltime {walltime}" if self.set_walltime else "") + ")...")
        rc, out = self._ssh(f"cd {remote_dir} && {seed_var}={seed} {time_var}={time_limit} "
                            f"sbatch {time_opt}{submit}")
        m = re.search(r"Submitted batch job (\d+)", out)
        if not m:
            print(f"    [remote] ERROR: no job id from: {out.strip()}")
            return None
        job_id = m.group(1)
        print(f"    [remote] job {job_id}")

        deadline = time.monotonic() + max(self.max_run_wait, wall_s + 1800)
        fails = 0
        verify_left = self.collect_verify_retries
        count_cmd = f"ls {results}/ 2>/dev/null | grep -c '^sol_.*\\.{self.ext}$'"
        while True:
            time.sleep(self.poll_interval)
            if time.monotonic() > deadline:
                print(f"    [remote] job {job_id} still active past the wait cap; giving up "
                      f"(results may persist remotely).")
                return None
            # NO `|| true`: a transport / controller failure must surface as rc != 0 and go
            # through the retry path -- never be read as "queue empty".
            rc, out = self._ssh(f"squeue -j {job_id} --noheader 2>/dev/null")
            if rc != 0:
                fails += 1
                if fails >= self.max_poll_failures:
                    print(f"    [remote] lost contact ({self.max_poll_failures} polls); aborting.")
                    return None
                print(f"    [remote] poll failed (rc={rc}), retry {fails}/{self.max_poll_failures}...")
                continue
            fails = 0
            if out.strip():
                continue
            # Queue empty: accept only once the expected number of solution files exists.
            rc, cnt = self._ssh(count_cmd)
            n = int(cnt.strip() or 0) if rc in (0, 1) else -1   # grep -c exits 1 on zero matches
            if n >= expected:
                print(f"    [remote] all tasks finished ({n}/{expected} solutions verified).")
                break
            if verify_left <= 0:
                print(f"    [remote] queue empty but only {n}/{expected} solutions present after "
                      f"{self.collect_verify_retries} re-checks -- tasks died or were killed; aborting.")
                return None
            verify_left -= 1
            print(f"    [remote] queue empty but {n}/{expected} solutions present; re-verifying "
                  f"in {self.collect_verify_wait}s ({verify_left} left)...")
            time.sleep(max(0, self.collect_verify_wait - self.poll_interval))

        # Results persist on the cluster: ride out a transport outage instead of discarding a
        # finished array.
        for f in local_out.glob(f"sol_*.{self.ext}"):
            f.unlink()
        for attempt in range(1, self.scp_retries + 1):
            rc, _, _ = _run(["scp", "-r", f"{self.host}:{results}/.", str(local_out)], check=False)
            if rc == 0:
                break
            print(f"    [remote] result copy failed (rc={rc}), attempt {attempt}/{self.scp_retries}; "
                  f"the finished job's results persist remotely -- retrying in {self.scp_retry_delay}s...")
            if attempt < self.scp_retries:
                time.sleep(self.scp_retry_delay)
        else:
            print(f"    [remote] could not copy results after {self.scp_retries} attempts; "
                  f"they remain at {self.host}:{results} for manual collection.")
            return None
        got = len(list(local_out.glob(f"sol_*.{self.ext}")))
        if got < expected:
            print(f"    [remote] copied {got}/{expected} solutions -- incomplete; aborting.")
            return None
        print(f"    [remote] results in {local_out} ({got}/{expected})")
        return local_out
