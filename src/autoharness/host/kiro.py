"""Kiro CLI 2.x: hook payload translation and output protocol.

Measured on kiro-cli 2.29.1 with kiro/probe (docs/kiro-port-spec.md section 7):
- every payload carries hook_event_name, cwd, session_id; userPromptSubmit adds prompt; tool events
  add tool_name, tool_input, tool_tags; stop adds assistant_response. No transcript path.
- tool names in the TUI are read / write / shell / subagent, an MCP tool is "@<server>/<tool>", and
  tool_tags repeats the name plus "@builtin" or "@<server>", "@mcp". The docs give fs_read /
  fs_write / execute_bash for non-interactive runs, accepted here as the same kinds.
- read takes {"operations": [{"mode", "path"}, ...]}; write takes {"command", "content", "path"}.
- output: stdout of agentSpawn / userPromptSubmit is added to the model's context; a preToolUse
  exit 2 blocks the call and the agent reads stderr; stop stdout {"decision": "block", "reason"}
  makes the agent take another turn acting on the reason, while plain stop stdout reaches nobody.
"""
import json
import sys
from pathlib import Path

AGENT_SPAWN = "agentSpawn"
USER_PROMPT = "userPromptSubmit"
PRE_TOOL = "preToolUse"
POST_TOOL = "postToolUse"
STOP = "stop"

_KINDS = {"read": "read", "fs_read": "read", "write": "write", "fs_write": "write",
          "shell": "shell", "execute_bash": "shell", "subagent": "subagent", "use_subagent": "subagent"}


def _tool_kind(name, tags):
    if isinstance(name, str) and name in _KINDS:
        return _KINDS[name]
    if "@mcp" in tags or (isinstance(name, str) and name.startswith("@")):
        return "mcp"
    for tag in tags:
        if tag in _KINDS:
            return _KINDS[tag]
    return "other"


def _tool_paths(tool_input, kind, cwd):
    raw = []
    if kind == "read":
        operations = tool_input.get("operations")
        for op in operations if isinstance(operations, list) else []:
            if isinstance(op, dict):
                raw.append(op.get("path"))
    raw.append(tool_input.get("path"))
    paths = []
    for value in raw:
        if not isinstance(value, str) or not value.strip():
            continue
        path = Path(value).expanduser()
        if not path.is_absolute() and cwd:
            path = Path(cwd) / path  # Kiro resolves a relative tool path against the session cwd
        if path not in paths:
            paths.append(path)
    return paths


def _text(value):
    return value if isinstance(value, str) else ""


def normalize(payload):
    """The host payload reduced to what the routing reads. Unknown or missing fields become empty
    values, never errors: a Kiro update that renames a field must degrade a feature, not a session."""
    payload = payload if isinstance(payload, dict) else {}
    cwd = _text(payload.get("cwd")) or None
    tool_input = payload.get("tool_input") if isinstance(payload.get("tool_input"), dict) else {}
    tags = [t for t in payload.get("tool_tags") or [] if isinstance(t, str)] \
        if isinstance(payload.get("tool_tags"), list) else []
    kind = _tool_kind(payload.get("tool_name"), tags)
    return {
        "event": _text(payload.get("hook_event_name")),
        "session_id": _text(payload.get("session_id")),
        "cwd": cwd,
        "prompt": _text(payload.get("prompt")),
        "response": _text(payload.get("assistant_response")),
        "tool": _text(payload.get("tool_name")),
        "kind": kind,
        "paths": _tool_paths(tool_input, kind, cwd),
    }


def emit(verdict, *, stdout=None, stderr=None):
    """Write a routing verdict in Kiro's protocol and return the process exit code. Hook stdout
    becomes agent context, so nothing but an intended injection is ever printed there."""
    stdout = stdout or sys.stdout
    stderr = stderr or sys.stderr
    if verdict.get("deny"):
        print(verdict["deny"], file=stderr)
        return 2
    if verdict.get("block"):
        print(json.dumps({"decision": "block", "reason": verdict["block"]}, ensure_ascii=False), file=stdout)
    elif verdict.get("context"):
        print(verdict["context"], file=stdout)
    return 0
