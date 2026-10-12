"""Summarise the Kiro probe log into something short enough to paste back.

    python3 kiro/probe/report.py

Reads <log dir>/log.jsonl written by probe.py and prints the shape of what the hooks received:
which events fired and how often, the payload keys per event, the tool names seen, the keys of
each tool's input, and whether any field looks like a session id. Values are reduced to their type
and length, apart from event and tool names, so prompts and file contents stay out of the report.
"""
import json
import sys
from collections import Counter, defaultdict

from probe import log_dir

_ID_HINTS = ("session", "conversation", "transcript", "agent")
MIN_PYTHON = (3, 10)  # the floor the Kiro port targets (docs/kiro-port-spec.md 4.9); upstream needs 3.11


def _meets_floor(version):
    try:
        return tuple(int(part) for part in version.split(".")[:2]) >= MIN_PYTHON
    except ValueError:
        return False


def _shape(value):
    if isinstance(value, dict):
        return "{" + ", ".join(sorted(value)) + "}"
    if isinstance(value, list):
        return f"list[{len(value)}]"
    if isinstance(value, str):
        return f"str[{len(value)}]"
    return type(value).__name__


def load(path):
    entries = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            entries.append(json.loads(line))
        except ValueError:
            continue
    return entries


def summarise(entries):
    lines = []
    if not entries:
        return ["no hook calls were logged: the hooks never ran"]
    versions = sorted({str(e.get("python")) for e in entries})
    lines.append(f"python (hook interpreter): {versions}")
    lines.append(f"autoharness needs Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]}+: "
                 + ("ok" if all(_meets_floor(v) for v in versions) else "TOO OLD"))
    lines.append(f"parent process: {sorted({str(e.get('parent_cmd')) for e in entries})}")
    lines.append(f"distinct parent pids: {len({e.get('ppid') for e in entries})}")
    lines.append(f"env var names: {sorted({n for e in entries for n in e.get('env_names', [])})}")

    fired = Counter((e["agent"], e["event"]) for e in entries)
    lines.append("\nevents fired (agent, event, count):")
    for (agent, event), count in sorted(fired.items()):
        lines.append(f"  {agent}  {event}  {count}")

    keys = defaultdict(set)
    tools = defaultdict(set)
    id_fields = set()
    unparsed = Counter()
    for e in entries:
        payload = e.get("payload")
        if not isinstance(payload, dict):
            unparsed[e["event"]] += 1
            continue
        for key, value in payload.items():
            keys[e["event"]].add(f"{key}: {_shape(value)}")
            if any(h in key.lower() for h in _ID_HINTS):
                id_fields.add(key)
        if "tool_name" in payload:
            tools[str(payload["tool_name"])].add(_shape(payload.get("tool_input")))

    lines.append("\npayload fields per event:")
    for event in sorted(keys):
        lines.append(f"  {event}:")
        for item in sorted(keys[event]):
            lines.append(f"    {item}")
    for event, count in sorted(unparsed.items()):
        lines.append(f"  {event}: {count} call(s) with empty or non-JSON stdin")

    hook_names = sorted({str(e["payload"].get("hook_event_name")) for e in entries
                         if isinstance(e.get("payload"), dict)})
    lines.append(f"\nhook_event_name values: {hook_names}")
    lines.append(f"fields that look like a session id: {sorted(id_fields) or 'none'}")

    lines.append("\ntools seen (name -> tool_input shape):")
    for name in sorted(tools):
        for shape in sorted(tools[name]):
            lines.append(f"  {name} -> {shape}")
    if not tools:
        lines.append("  none")
    return lines


def main():
    path = log_dir() / "log.jsonl"
    if not path.exists():
        print(f"no log at {path}: the hooks never ran")
        return 1
    print("\n".join(summarise(load(path))))
    return 0


if __name__ == "__main__":
    sys.exit(main())
