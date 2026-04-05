"""
Built-in tool implementations for agent workers.
All file operations are sandboxed to WORKSPACE.
"""
import os
import subprocess
import glob as glob_module

WORKSPACE = os.environ.get("WORKSPACE", "/workspace")


def _safe_path(path: str) -> str:
    """Resolve path relative to workspace, prevent escaping."""
    full = os.path.realpath(os.path.join(WORKSPACE, path.lstrip("/")))
    if not full.startswith(os.path.realpath(WORKSPACE)):
        raise PermissionError(f"Path outside workspace: {path}")
    return full


# ── Tool implementations ──────────────────────────────────────────────────────

def read_file(path: str) -> str:
    try:
        with open(_safe_path(path)) as f:
            return f.read()
    except Exception as e:
        return f"Error: {e}"


def write_file(path: str, content: str) -> str:
    full = _safe_path(path)
    os.makedirs(os.path.dirname(full) or WORKSPACE, exist_ok=True)
    with open(full, "w") as f:
        f.write(content)
    return f"Written {len(content)} bytes to {path}"


def edit_file(path: str, old_string: str, new_string: str) -> str:
    full = _safe_path(path)
    try:
        with open(full) as f:
            content = f.read()
        if old_string not in content:
            return f"Error: string not found in {path}"
        updated = content.replace(old_string, new_string, 1)
        with open(full, "w") as f:
            f.write(updated)
        return f"Replaced 1 occurrence in {path}"
    except Exception as e:
        return f"Error: {e}"


def bash(command: str, timeout: int = 30) -> str:
    result = subprocess.run(
        command, shell=True, capture_output=True, text=True,
        cwd=WORKSPACE, timeout=timeout,
    )
    out = (result.stdout + result.stderr).strip()
    if result.returncode != 0:
        out = f"[exit {result.returncode}]\n{out}"
    return out[:4000] or "(no output)"


def list_files(path: str = ".") -> str:
    try:
        full = _safe_path(path)
        entries = sorted(os.listdir(full))
        return "\n".join(entries) or "(empty)"
    except Exception as e:
        return f"Error: {e}"


def glob(pattern: str) -> str:
    matches = glob_module.glob(
        os.path.join(WORKSPACE, pattern), recursive=True
    )
    relative = [os.path.relpath(m, WORKSPACE) for m in sorted(matches)]
    return "\n".join(relative) or "(no matches)"


def grep(pattern: str, path: str = ".") -> str:
    result = subprocess.run(
        ["grep", "-rn", "--include=*", pattern, path],
        capture_output=True, text=True, cwd=WORKSPACE,
    )
    return result.stdout[:4000] or "(no matches)"


# ── Schema definitions ────────────────────────────────────────────────────────

TOOL_SCHEMAS = [
    {
        "name": "read_file",
        "description": "Read a file from the workspace.",
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
    },
    {
        "name": "write_file",
        "description": "Write content to a file (creates directories as needed).",
        "input_schema": {
            "type": "object",
            "properties": {
                "path":    {"type": "string"},
                "content": {"type": "string"},
            },
            "required": ["path", "content"],
        },
    },
    {
        "name": "edit_file",
        "description": "Replace the first occurrence of old_string with new_string in a file.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path":       {"type": "string"},
                "old_string": {"type": "string"},
                "new_string": {"type": "string"},
            },
            "required": ["path", "old_string", "new_string"],
        },
    },
    {
        "name": "bash",
        "description": "Run a shell command in the workspace directory.",
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {"type": "string"},
                "timeout": {"type": "integer", "description": "Seconds, default 30"},
            },
            "required": ["command"],
        },
    },
    {
        "name": "list_files",
        "description": "List files in a workspace directory.",
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string", "description": "Default '.'"}},
        },
    },
    {
        "name": "glob",
        "description": "Find files matching a glob pattern (e.g. '**/*.py').",
        "input_schema": {
            "type": "object",
            "properties": {"pattern": {"type": "string"}},
            "required": ["pattern"],
        },
    },
    {
        "name": "grep",
        "description": "Search file contents with a regex pattern.",
        "input_schema": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string"},
                "path":    {"type": "string", "description": "Directory or file, default '.'"},
            },
            "required": ["pattern"],
        },
    },
]

TOOL_IMPLS = {
    "read_file":  lambda i: read_file(i["path"]),
    "write_file": lambda i: write_file(i["path"], i["content"]),
    "edit_file":  lambda i: edit_file(i["path"], i["old_string"], i["new_string"]),
    "bash":       lambda i: bash(i["command"], i.get("timeout", 30)),
    "list_files": lambda i: list_files(i.get("path", ".")),
    "glob":       lambda i: glob(i["pattern"]),
    "grep":       lambda i: grep(i["pattern"], i.get("path", ".")),
}
