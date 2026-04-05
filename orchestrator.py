#!/usr/bin/env python3
"""
Multi-agent orchestrator.

Runs agent teams in phased Docker containers:
  Phase 1: architect   (sequential — others depend on design.md)
  Phase 2: backend + frontend + tester  (parallel)
  Phase 3: reviewer    (sequential — reviews all output)
  Phase 4: devops      (sequential — wraps everything up)

Usage:
    python3 orchestrator.py "Build a REST API for managing books"
    python3 orchestrator.py --phases architect,backend "Only design + implement backend"
    python3 orchestrator.py --design design.md "Use existing design file"

Options:
    --phases  Comma-separated roles to run (default: full pipeline)
    --design  Path to a pre-written design.md to seed the workspace
    --timeout Per-container timeout in seconds (default: 600)
    --image   Docker image name (default: personal-agent-worker)
"""
import argparse
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from dotenv import load_dotenv  # type: ignore[import]  # optional

# Load .env if present
load_dotenv(dotenv_path=Path(__file__).parent / ".env")

# ── Config ─────────────────────────────────────────────────────────────────────

OPENROUTER_KEY = os.environ.get("OPENROUTER_API_KEY", "")
CLAUDE_MODEL   = os.environ.get("CLAUDE_MODEL", "anthropic/claude-sonnet-4.6")
IMAGE          = os.environ.get("DOCKER_IMAGE", "personal-agent-worker")
WORKSPACE      = Path(__file__).parent / "workspace"

DEFAULT_PHASES = [
    ["architect"],
    ["backend", "frontend", "tester"],
    ["reviewer"],
    ["devops"],
]

# Phase-specific task overrides (None = use project task)
PHASE_TASKS: dict[str, str | None] = {
    "architect": None,  # receives the raw project description
    "backend":   "Implement the backend based on /workspace/design.md",
    "frontend":  "Implement the frontend based on /workspace/design.md",
    "tester":    "Write and run tests for the code in /workspace based on /workspace/design.md",
    "reviewer":  "Review all code in /workspace and write /workspace/review.md",
    "devops":    "Create deployment files and README for the project in /workspace",
}


# ── Container runner ──────────────────────────────────────────────────────────

def run_container(role: str, task: str, timeout: int) -> tuple[str, bool]:
    """Run a single agent container. Returns (output, success)."""
    cmd = [
        "docker", "run", "--rm",
        "-v", f"{WORKSPACE}:/workspace",
        "-e", f"OPENROUTER_API_KEY={OPENROUTER_KEY}",
        "-e", f"CLAUDE_MODEL={CLAUDE_MODEL}",
        "-e", f"AGENT_ROLE={role}",
        IMAGE,
        task,
    ]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout,
        )
        output = result.stdout + result.stderr
        return output, result.returncode == 0
    except subprocess.TimeoutExpired:
        return f"[{role}] TIMEOUT after {timeout}s", False
    except Exception as e:
        return f"[{role}] ERROR: {e}", False


def run_phase(roles: list[str], project_task: str, timeout: int) -> dict[str, bool]:
    """Run roles in parallel. Returns {role: success}."""
    results: dict[str, tuple[str, bool]] = {}
    lock = threading.Lock()

    def worker(role: str):
        task = PHASE_TASKS.get(role) or project_task
        output, ok = run_container(role, task, timeout)
        with lock:
            results[role] = (output, ok)
            status = "OK" if ok else "FAILED"
            # Print output lines with role prefix
            for line in output.splitlines():
                print(f"  {line}")
            print(f"  [{role.upper()}] {status}")

    threads = [threading.Thread(target=worker, args=(r,)) for r in roles]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    return {role: ok for role, (_, ok) in results.items()}


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Multi-agent orchestrator")
    parser.add_argument("task", nargs="*", help="Project description")
    parser.add_argument("--phases",  help="Comma-separated roles, e.g. architect,backend")
    parser.add_argument("--design",  help="Seed workspace with this design.md")
    parser.add_argument("--timeout", type=int, default=600, help="Per-container timeout (s)")
    parser.add_argument("--image",   default=IMAGE, help="Docker image name")
    args = parser.parse_args()

    project_task = " ".join(args.task) if args.task else os.environ.get("AGENT_TASK", "")
    if not project_task:
        parser.error("Provide a project task as CLI argument or AGENT_TASK env var")

    if not OPENROUTER_KEY:
        parser.error("OPENROUTER_API_KEY not set — copy .env.example to .env and fill it in")

    # Prepare workspace
    WORKSPACE.mkdir(exist_ok=True)

    if args.design:
        shutil.copy(args.design, WORKSPACE / "design.md")
        print(f"Seeded workspace with {args.design}")

    # Build phases
    if args.phases:
        phases = [[r.strip()] for r in args.phases.split(",")]
    else:
        phases = DEFAULT_PHASES

    print(f"\n{'='*60}")
    print(f"PROJECT: {project_task}")
    print(f"IMAGE:   {args.image}")
    print(f"MODEL:   {CLAUDE_MODEL}")
    print(f"WORKSPACE: {WORKSPACE}")
    print(f"{'='*60}\n")

    failed_roles = []
    total_start = time.time()

    for phase_num, roles in enumerate(phases, 1):
        print(f"─── Phase {phase_num}: {' + '.join(r.upper() for r in roles)} ───")
        t0 = time.time()
        statuses = run_phase(roles, project_task, args.timeout)
        elapsed = time.time() - t0
        print(f"    Phase {phase_num} done in {elapsed:.0f}s\n")
        failed_roles.extend(r for r, ok in statuses.items() if not ok)

    total = time.time() - total_start
    print(f"{'='*60}")
    print(f"DONE in {total:.0f}s total")
    print(f"\nWorkspace contents:")
    for p in sorted(WORKSPACE.rglob("*")):
        if p.is_file():
            rel = p.relative_to(WORKSPACE)
            print(f"  {rel}  ({p.stat().st_size} bytes)")

    if failed_roles:
        print(f"\nFailed agents: {', '.join(failed_roles)}")
        sys.exit(1)


if __name__ == "__main__":
    main()
