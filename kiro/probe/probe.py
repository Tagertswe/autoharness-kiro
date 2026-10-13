"""Kiro hook probe: log what a hook receives, and emit markers that show what the model gets back.

Throwaway diagnostics for docs/kiro-port-spec.md section 7, not part of the autoharness pipeline.
One script serves every hook; the agent config passes the event name as argv[1]:

    python3 probe.py <event> <agent-name>

Each call appends one JSON line to <log dir>/log.jsonl (default ~/autoharness-probe, override with
AHPROBE_DIR). What it prints is deliberate: a marker word on stdout for the events whose output is
supposed to reach the model, so asking the model which AHPROBE words it can see answers the
injection question. Runs on any Python 3.6+, and logs the interpreter version because autoharness
itself needs 3.10.
"""
import json
import os
import sys
import time
from pathlib import Path

BLOCK_TOKEN = "ahprobe-block-test"   # a preToolUse call mentioning this is denied with exit 2
MARK = "AHPROBE"
_ENV_HINTS = ("KIRO", "AMAZON_Q", "Q_", "AUTOHARNESS", "AHPROBE")


def log_dir():
    return Path(os.environ.get("AHPROBE_DIR") or Path.home() / "autoharness-probe")


def _parent():
    """Who launched the hook: the pid chain is the fallback session key if the payload has no id."""
    info = {"ppid": os.getppid()}
    try:
        raw = Path(f"/proc/{info['ppid']}/cmdline").read_bytes()
        info["parent_cmd"] = raw.split(b"\0")[0].decode("utf-8", "replace")
    except OSError:
        pass  # not Linux, or the parent is gone: the pid alone still says something
    return info


def _bump(name):
    """Per-event call counter, so a marker says which firing it came from."""
    path = log_dir() / f"count-{name}"
    try:
        count = int(path.read_text()) + 1
    except (OSError, ValueError):
        count = 1
    path.write_text(str(count))
    return count


def record(event, agent, raw):
    try:
        payload = json.loads(raw) if raw.strip() else None
        parse_error = None
    except ValueError as exc:
        payload, parse_error = None, str(exc)
    entry = {
        "ts": time.time(),
        "event": event,
        "agent": agent,
        "payload": payload,
        "raw": raw if payload is None else None,
        "parse_error": parse_error,
        "cwd": os.getcwd(),
        "python": sys.version.split()[0],
        # names only: values can be credentials, and this file may get shared
        "env_names": sorted(k for k in os.environ if any(h in k.upper() for h in _ENV_HINTS)),
    }
    entry.update(_parent())
    with open(log_dir() / "log.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")
    return payload


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    event = argv[0] if argv else "unknown"
    agent = argv[1] if len(argv) > 1 else "unknown"
    log_dir().mkdir(parents=True, exist_ok=True)
    raw = "" if sys.stdin is None or sys.stdin.isatty() else sys.stdin.read()
    payload = record(event, agent, raw)
    count = _bump(event)

    if event == "agentSpawn":
        print(f"{MARK}-SPAWN-{count} (autoharness probe: this line came from the agentSpawn hook)")
    elif event == "userPromptSubmit":
        print(f"{MARK}-PROMPT-{count} (autoharness probe: this line came from the userPromptSubmit hook)")
    elif event == "preToolUse":
        if BLOCK_TOKEN in json.dumps(payload if payload is not None else raw):
            print(f"{MARK}-DENIED: the autoharness probe blocked this tool call on purpose. "
                  "Do not retry; tell the user it was blocked.", file=sys.stderr)
            return 2
    elif event == "postToolUse":
        pass  # logged only: printing here would attach a marker to every tool result
    elif event == "stop":
        # Asked once, never again: if this build honours a stop block the agent takes one more
        # turn and says the word; if not, nothing happens. The flag file is what prevents a loop.
        flag = log_dir() / "stop-block-sent"
        if not flag.exists():
            flag.write_text("1")
            print(json.dumps({"decision": "block",
                              "reason": f"Reply with exactly {MARK}-STOP-CONTINUED and nothing else."}))
        else:
            print(f"{MARK}-STOP-{count} (autoharness probe: this line came from the stop hook)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
