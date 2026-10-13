"""Claude Code: the host autoharness was written for. Its payload is the internal shape already, so
the only thing that lives here is the output protocol (hookSpecificOutput JSON on stdout)."""
import json
import sys


def emit(verdict):
    if verdict.get("deny"):
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": verdict.get("reason", "denied"),
        }}))
        return
    result = verdict.get("result") or {}
    if verdict.get("handled") == "SessionStart" and result.get("context"):
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": result["context"],
        }}))
        return
    handled = verdict.get("handled")
    if handled:
        print(json.dumps({"handled": handled, "result": result}), file=sys.stderr)
