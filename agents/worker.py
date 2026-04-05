#!/usr/bin/env python3
"""
Agent worker — runs inside a Docker container.

Usage:
    docker run ... -e AGENT_ROLE=backend worker.py "Implement the API"

Environment:
    OPENROUTER_API_KEY   Required. Set by entrypoint.sh into ANTHROPIC_API_KEY.
    AGENT_ROLE           One of the roles defined in roles.py (default: generalist).
    CLAUDE_MODEL         OpenRouter model ID (default: anthropic/claude-sonnet-4.6).
    WORKSPACE            Absolute path to workspace dir (default: /workspace).
"""
import os
import sys
import anthropic

from tools import TOOL_SCHEMAS, TOOL_IMPLS
from roles import ROLES

TASK      = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else os.environ.get("AGENT_TASK", "")
ROLE      = os.environ.get("AGENT_ROLE", "generalist")
MODEL     = os.environ.get("CLAUDE_MODEL", "anthropic/claude-sonnet-4.6")
WORKSPACE = os.environ.get("WORKSPACE", "/workspace")
MAX_TURNS = int(os.environ.get("MAX_TURNS", "30"))

if not TASK:
    print("Error: no task provided. Pass as CLI argument or AGENT_TASK env var.")
    sys.exit(1)

cfg = ROLES.get(ROLE, ROLES["generalist"])
allowed_tools = [s for s in TOOL_SCHEMAS if s["name"] in cfg["tools"]]


def run():
    client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY + ANTHROPIC_BASE_URL from env
    messages = [{"role": "user", "content": TASK}]

    print(f"[{ROLE.upper()}] Starting | model={MODEL}")
    print(f"[{ROLE.upper()}] Task: {TASK[:120]}")

    for turn in range(MAX_TURNS):
        response = client.messages.create(
            model=MODEL,
            max_tokens=8192,
            system=cfg["system"],
            tools=allowed_tools,
            messages=messages,
        )

        # Print assistant text
        for block in response.content:
            if block.type == "text" and block.text.strip():
                preview = block.text[:400].replace("\n", " ")
                print(f"[{ROLE.upper()}] {preview}")

        if response.stop_reason == "end_turn":
            print(f"[{ROLE.upper()}] Finished in {turn + 1} turn(s)")
            break

        if response.stop_reason != "tool_use":
            print(f"[{ROLE.upper()}] Stopped: {response.stop_reason}")
            break

        # Execute tool calls
        messages.append({"role": "assistant", "content": response.content})
        tool_results = []
        for block in response.content:
            if block.type == "tool_use":
                arg_summary = ", ".join(
                    f"{k}={str(v)[:40]!r}" for k, v in block.input.items()
                )
                print(f"[{ROLE.upper()}] → {block.name}({arg_summary})")
                impl = TOOL_IMPLS.get(block.name)
                result = impl(block.input) if impl else f"Unknown tool: {block.name}"
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": str(result)[:4000],
                })
        messages.append({"role": "user", "content": tool_results})

    else:
        print(f"[{ROLE.upper()}] Reached max turns ({MAX_TURNS})")

    # Save result summary
    last_text = next(
        (b.text for b in reversed(response.content) if b.type == "text"),
        "(no text output)",
    )
    result_path = os.path.join(WORKSPACE, f"result_{ROLE}.md")
    with open(result_path, "w") as f:
        f.write(f"# Agent: {ROLE}\n\n## Task\n{TASK}\n\n## Summary\n{last_text}\n")
    print(f"[{ROLE.upper()}] Saved {result_path}")


if __name__ == "__main__":
    run()
