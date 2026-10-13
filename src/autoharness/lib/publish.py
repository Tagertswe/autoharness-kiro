"""Shared skills repository: what has changed in it, and the record of merge requests opened for it.

docs/kiro-port-spec.md 4.10. Learned skills live in a folder of a clone of a shared repository and
reach it through merge requests the agent opens with a skill that repository already has. This
module is the deterministic half: it reads `git status` to say what is unpublished, keeps a durable
local record of every merge request, and tracks which session has been told about which one.

Read-only by construction: the only git command run here is `status` (plus `rev-parse` to find the
clone). autoharness never commits, pushes or calls a forge API; the URL in a record is whatever the
agent wrote in its reply. Records hold an internal URL, so they live in the global state directory
under the home directory, never in a repository.
"""
import json
import re
import subprocess
import time
from pathlib import Path

from autoharness import config
from autoharness.lib import atomic, counters, layer, lock, skill_store

RECORDS = "publishes.jsonl"
# a merge request (GitLab) or pull request (GitHub, Gitea) link; trailing punctuation stays out
_URL = re.compile(r"https?://[^\s<>()\[\]\"'`]+/(?:merge_requests|pull|pulls)/\d+")
_GIT_TIMEOUT_S = 10


def _git(args, cwd):
    try:
        proc = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True,
                              timeout=_GIT_TIMEOUT_S, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    return proc.stdout if proc.returncode == 0 else None


def skills_folder(groot=None):
    return layer.skills_dir(layer.GLOBAL, groot)


def repo_dir(groot=None):
    """The clone the learned-skills folder sits in, or None when there is no such deployment."""
    if config.SHARED_SKILLS_REPO:
        path = Path(config.SHARED_SKILLS_REPO).expanduser()
        return path if path.is_dir() else None
    folder = skills_folder(groot)
    if not folder.is_dir():
        return None
    top = _git(["rev-parse", "--show-toplevel"], folder)
    return Path(top.strip()) if top and top.strip() else None


def changes(groot=None):
    """Uncommitted skill changes under the learned-skills folder as sorted (verb, skill) pairs, verb
    one of created / updated / removed. None when no shared repository is configured or git fails."""
    repo = repo_dir(groot)
    if repo is None:
        return None
    folder = skills_folder(groot)
    try:
        rel = folder.resolve().relative_to(repo.resolve())
    except (ValueError, OSError):
        return None
    out = _git(["status", "--porcelain=v1", "--untracked-files=all", "--", str(rel) or "."], repo)
    if out is None:
        return None
    seen = {}
    for line in out.splitlines():
        if len(line) < 4:
            continue
        status, path = line[:2], line[3:].split(" -> ")[-1].strip().strip('"')
        try:
            parts = Path(path).relative_to(rel).parts
        except ValueError:
            continue
        if len(parts) < 2 or parts[0].startswith("."):
            continue  # a loose file in the folder, or the archive: not a skill
        skill, is_body = parts[0], parts[1:] == (skill_store.SKILL_FILE,)
        verb = seen.get(skill, "updated")
        if is_body and ("?" in status or "A" in status):
            verb = "created"
        elif is_body and "D" in status:
            verb = "removed"
        seen[skill] = verb
    return sorted((verb, skill) for skill, verb in seen.items())


def summary(pairs):
    return [f"{verb} {skill}" for verb, skill in pairs]


def find_url(text):
    hits = _URL.findall(text or "")
    return hits[-1] if hits else None  # the agent is asked to end its reply with the link


def _records_path(groot=None):
    return layer.state_dir(layer.GLOBAL, groot) / RECORDS


def records(groot=None):
    path = _records_path(groot)
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict) and row.get("id"):
            out.append(row)
    return out


def add_record(url, lines, groot=None, *, now=None):
    now = time.time() if now is None else now
    record = {"id": f"{int(now * 1000)}", "ts": now, "url": url, "changes": list(lines)}
    path = _records_path(groot)
    path.parent.mkdir(parents=True, exist_ok=True)
    with lock.file_lock(path.with_suffix(path.suffix + ".lock")):
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    return record


def _announced_path(session_id, groot=None):
    if not isinstance(session_id, str) or not counters._SAFE_SESSION.match(session_id):
        raise ValueError(f"unsafe session id: {session_id!r}")
    return layer.state_dir(layer.GLOBAL, groot) / "announced" / f"{session_id}.json"


def _announced(session_id, groot=None):
    try:
        return set(json.loads(_announced_path(session_id, groot).read_text(encoding="utf-8")))
    except (OSError, ValueError, TypeError):
        return set()


def pending(session_id, groot=None, *, now=None):
    """Recent records this session has not been told about yet, oldest first."""
    now = time.time() if now is None else now
    horizon = now - config.PUBLISH_NOTE_DAYS * 86400
    try:
        told = _announced(session_id, groot)
    except ValueError:
        return []
    return [r for r in records(groot) if r["id"] not in told and r.get("ts", 0) >= horizon]


def mark_announced(session_id, rows, groot=None):
    if not rows:
        return
    try:
        path = _announced_path(session_id, groot)
    except ValueError:
        return
    told = _announced(session_id, groot) | {r["id"] for r in rows}
    atomic.write_text(path, json.dumps(sorted(told)))


def describe(record):
    when = time.strftime("%Y-%m-%d", time.localtime(record.get("ts", 0)))
    what = ", ".join(record.get("changes") or []) or "skill changes"
    link = record.get("url") or "no link was captured; look in the shared skills repository"
    return f"- {when}: {what} -> {link}"
