"""Single-instance campaign watchdog. Ensures exactly one evolution loop is running for a
problem, (re)launching scripts/run_evolution_step.py if none is alive. Idempotent: safe to
call from cron / Task Scheduler every few minutes -- a second invocation while the loop is
healthy is a no-op.

    python scripts/campaign_tick.py --problem toy --loop 20
    python scripts/campaign_tick.py --problem toy --loop 20 --skip-remote
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform == "win32":
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                             capture_output=True, text=True).stdout
        return str(pid) in out
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--problem", required=True)
    ap.add_argument("--loop", type=int, default=20)
    ap.add_argument("--model", default="opus")
    ap.add_argument("--skip-remote", action="store_true")
    args = ap.parse_args()

    run_root = ROOT / "runs" / args.problem
    run_root.mkdir(parents=True, exist_ok=True)
    lock = run_root / "campaign.pid"

    if lock.exists():
        try:
            pid = int(lock.read_text().strip() or "0")
        except ValueError:
            pid = 0
        if _pid_alive(pid):
            print(f"[tick] campaign already alive (pid {pid}); nothing to do.")
            return
        print(f"[tick] stale lock (pid {pid} dead); relaunching.")

    cmd = [sys.executable, str(ROOT / "scripts" / "run_evolution_step.py"),
           "--problem", args.problem, "--loop", str(args.loop), "--model", args.model]
    if args.skip_remote:
        cmd.append("--skip-remote")

    creationflags = 0x00000008 | 0x08000000 if sys.platform == "win32" else 0  # DETACHED | NO_WINDOW
    log = open(run_root / "campaign.out", "a", encoding="utf-8")
    proc = subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT,
                            cwd=str(ROOT), creationflags=creationflags)
    lock.write_text(str(proc.pid))
    print(f"[tick] launched campaign pid {proc.pid} ({args.loop} steps) -> {run_root/'campaign.out'}")
    time.sleep(2)
    if not _pid_alive(proc.pid):
        print("[tick] WARNING: process exited immediately; check campaign.out")


if __name__ == "__main__":
    main()
