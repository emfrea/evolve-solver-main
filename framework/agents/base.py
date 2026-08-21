"""Generic Claude Code agent plumbing -- no problem knowledge.

- call_claude(): one-shot `claude -p` via stdin (Windows-safe), with retries.
- run_agent(): streaming agentic run with --dangerously-skip-permissions, a wall-clock
  timeout, and detection of a GENUINE account/session rate limit vs a transient server 429.
- the rate-limit regexes live here so all agents share them.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Optional


class AgentError(RuntimeError):
    pass


# Signatures of a GENUINE account/session quota exhaustion (the CLI exits, cannot recover).
RATE_LIMIT_RE = re.compile(
    r"(reached|hit|exceeded)\b.{0,24}(usage|session|account)\b.{0,16}limit"
    r"|session limit reached|usage limit reached|limit will reset|resets? (at|in)\b"
    r"|/upgrade|upgrade to (increase|claude)",
    re.IGNORECASE,
)
# Transient SERVER throttles (429 / overload) which the CLI retries internally -- do NOT
# trigger the long account-quota backoff.
TRANSIENT_RE = re.compile(
    r"not your usage limit|temporarily limiting|server is busy|overloaded|try again",
    re.IGNORECASE,
)


def _claude() -> str:
    exe = shutil.which("claude")
    if not exe:
        raise AgentError("claude CLI not found on PATH -- re-login or reinstall.")
    return exe


def call_claude(prompt: str, *, model: str = "sonnet", cwd: Optional[Path] = None,
                attempts: int = 3) -> str:
    """One-shot claude -p, prompt via stdin. Returns stdout text."""
    claude = _claude()
    last = ""
    for attempt in range(1, attempts + 1):
        proc = subprocess.run([claude, "--model", model, "-p"], input=prompt,
                              cwd=str(cwd) if cwd else None, capture_output=True,
                              text=True, encoding="utf-8", errors="replace")
        if proc.returncode == 0:
            return (proc.stdout or "").strip()
        last = (proc.stderr or proc.stdout or "")[:300]
        if attempt < attempts:
            time.sleep(5 * attempt)
    raise AgentError(f"claude call failed after {attempts} attempts.\n{last}")


def parse_json_response(raw: str, who: str) -> dict[str, Any]:
    text = raw.strip()
    a, b = text.find("{"), text.rfind("}")
    if a != -1 and b > a:
        text = text[a:b + 1]
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise AgentError(f"{who}: could not parse JSON.\nRaw(first 400): {raw[:400]}") from exc


def _kill_tree(proc: subprocess.Popen) -> None:
    """Terminate the agent AND everything it spawned (solver runs, shells). A bare
    proc.kill() only reaps the CLI: on Windows its children survive as orphans and keep
    burning CPU/licences past the timeout; on POSIX the agent runs in its own session so
    the whole process group can be signalled."""
    if proc.poll() is not None:
        return
    try:
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                           capture_output=True, text=True, timeout=30)
        else:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            try:
                proc.wait(10)
            except subprocess.TimeoutExpired:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError, subprocess.TimeoutExpired):
        pass
    try:
        proc.wait(10)
    except subprocess.TimeoutExpired:
        proc.kill()


def run_agent(ws: Path, prompt: str, *, model: str, timeout: int) -> tuple[int, bool, float]:
    """Run claude as an agent inside `ws`. Streams output, enforces `timeout` with a
    process-TREE kill. Returns (returncode, rate_limited, elapsed_s); rate_limited is a
    GENUINE account limit; elapsed_s lets the caller tell a deliberate no-op (the agent
    worked, exited 0, changed nothing) from an instant CLI failure."""
    claude = _claude()
    popen_kw = {}
    if sys.platform != "win32":
        popen_kw["start_new_session"] = True   # own process group -> killable as a tree
    proc = subprocess.Popen(
        [claude, "-p", "--dangerously-skip-permissions", "--model", model], cwd=str(ws),
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace", bufsize=1, **popen_kw)
    try:
        if proc.stdin:
            proc.stdin.write(prompt)
            proc.stdin.close()
    except (BrokenPipeError, OSError):
        pass

    rate_limited = [False]

    def stream():
        if not proc.stdout:
            return
        for line in iter(proc.stdout.readline, ""):
            if not rate_limited[0] and RATE_LIMIT_RE.search(line) and not TRANSIENT_RE.search(line):
                rate_limited[0] = True
            print(f"      [agent] {line.rstrip()[:140]}", flush=True)

    t = threading.Thread(target=stream, daemon=True)
    t.start()
    start = time.time()
    try:
        while proc.poll() is None:
            if time.time() - start > timeout:
                print(f"      [agent] TIMEOUT after {timeout}s; terminating the process tree.", flush=True)
                _kill_tree(proc)
                break
            time.sleep(2)
    except KeyboardInterrupt:
        _kill_tree(proc)
        raise
    t.join(timeout=5)
    elapsed = time.time() - start
    return (proc.returncode if proc.returncode is not None else -1), rate_limited[0], elapsed
