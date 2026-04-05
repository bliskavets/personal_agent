# Personal Agent

Multi-agent software development team powered by Claude via OpenRouter, running in Docker.

## How it works

```
orchestrator.py
    │
    ├── Phase 1 ── [architect]              designs system → design.md
    ├── Phase 2 ── [backend] [frontend] [tester]   parallel implementation
    ├── Phase 3 ── [reviewer]               reviews all code → review.md
    └── Phase 4 ── [devops]                 Dockerfile, README, CI
```

Each agent runs in an isolated Docker container with access to a shared `/workspace` volume.

## Setup

```bash
# 1. Clone and enter the repo
git clone https://github.com/bliskavets/personal_agent
cd personal_agent

# 2. Configure
cp .env.example .env
# Edit .env — add your OpenRouter API key

# 3. Build the Docker image
make build
# or: docker build -t personal-agent-worker .

# 4. Run a project
make run TASK="Build a REST API for managing books"
# or: python3 orchestrator.py "Build a REST API for managing books"
```

## Usage

### Full pipeline
```bash
python3 orchestrator.py "Build a Telegram bot that summarizes news"
```

### Selected phases only
```bash
python3 orchestrator.py --phases architect,backend "Build a CLI todo app in Python"
```

### Seed with your own design doc
```bash
python3 orchestrator.py --design my_design.md "Implement this"
```

### Single agent interactively
```bash
docker run --rm -it \
  -v $(pwd)/workspace:/workspace \
  -e OPENROUTER_API_KEY=sk-or-... \
  -e AGENT_ROLE=generalist \
  personal-agent-worker \
  "Refactor the code in /workspace to use async/await"
```

## Available roles

| Role | Tools | Responsibility |
|---|---|---|
| `architect` | read, write, list | System design → `design.md` |
| `backend` | read, write, edit, bash, list, glob | Server-side implementation |
| `frontend` | read, write, edit, list | Client-side implementation |
| `tester` | read, write, bash, list, glob, grep | Tests + running them |
| `reviewer` | read, write, bash, list, glob, grep | Code review → `review.md` |
| `devops` | read, write, list, glob | Dockerfile, CI, README |
| `generalist` | all tools | Any task |

## Adding a new role

Edit `agents/roles.py` and add an entry to the `ROLES` dict:

```python
"my_role": {
    "tools": ["read_file", "write_file", "bash"],
    "system": "You are a ... Your job is to ...",
},
```

Then reference it in `orchestrator.py`'s `DEFAULT_PHASES` or via `--phases my_role`.

## Configuration

| Variable | Default | Description |
|---|---|---|
| `OPENROUTER_API_KEY` | — | Required. Your OpenRouter key |
| `CLAUDE_MODEL` | `anthropic/claude-sonnet-4.6` | Any Claude model on OpenRouter |
| `DOCKER_IMAGE` | `personal-agent-worker` | Docker image name |

## Project structure

```
├── Dockerfile            Worker image definition
├── entrypoint.sh         Container entrypoint (wires OpenRouter auth)
├── docker-compose.yml    Single-agent convenience runner
├── orchestrator.py       Multi-agent pipeline runner (runs on host)
├── Makefile              Convenience commands
├── requirements.txt      Host-side Python deps (orchestrator)
├── agents/
│   ├── worker.py         Agent main loop (runs inside container)
│   ├── tools.py          Tool implementations (file I/O, bash, grep)
│   └── roles.py          Role definitions (system prompts + tool sets)
└── workspace/            Shared volume — all agents read/write here
```
