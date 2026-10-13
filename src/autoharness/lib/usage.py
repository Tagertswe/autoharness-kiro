"""What autoharness itself costs: one line per turn it asked the agent for, in a dated log file.

Kiro CLI gives a hook no credit or token figure (checked on 2.29.1: the stop payload carries the
reply text and nothing about usage), so this cannot record credits. It records what a hook can
measure and what a credit figure can be matched against afterwards: when the turn ran, in which
session, what it was for, how long it took, how many tool calls it made and how much text went in
and out. If a later Kiro puts a usage field in the payload, host/kiro.py passes it through and it
is stored verbatim under "reported".

One file per local day, `<global state>/usage/YYYY-MM-DD.jsonl`, under the home directory and never
in a repository: a record names a session and may hold an internal merge-request URL.
"""
import json
import time

from autoharness.lib import layer, lock

DIR = "usage"
KINDS = ("reflect", "learn", "curate", "publish", "note")


def directory(groot=None):
    return layer.state_dir(layer.GLOBAL, groot) / DIR


def _day(ts):
    return time.strftime("%Y-%m-%d", time.localtime(ts))


def append(record, groot=None, *, now=None):
    now = time.time() if now is None else now
    row = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(now)), **record}
    path = directory(groot) / f"{_day(now)}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with lock.file_lock(path.with_suffix(path.suffix + ".lock")):
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return path


def read(days, groot=None, *, now=None):
    """Records of the last `days` local days, today included, oldest first, as (day, row) pairs."""
    now = time.time() if now is None else now
    wanted = {_day(now - n * 86400) for n in range(max(1, days))}
    out = []
    folder = directory(groot)
    if not folder.is_dir():
        return out
    for path in sorted(folder.glob("*.jsonl")):
        if path.stem not in wanted:
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict):
                out.append((path.stem, row))
    return out


def totals(pairs):
    """Per kind: turns, seconds, tool calls and characters in/out, in KINDS order then any others."""
    acc = {}
    for _, row in pairs:
        t = acc.setdefault(str(row.get("kind") or "other"), {"turns": 0, "seconds": 0.0, "tool_calls": 0, "chars": 0})
        t["turns"] += 1
        t["seconds"] += row.get("seconds") or 0
        t["tool_calls"] += row.get("tool_calls") or 0
        t["chars"] += (row.get("instruction_chars") or 0) + (row.get("response_chars") or 0)
    order = [k for k in KINDS if k in acc] + sorted(k for k in acc if k not in KINDS)
    return [(k, acc[k]) for k in order]
