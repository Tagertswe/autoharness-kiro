"""Kiro CLI 2.x event routing: the Kiro counterpart of dispatch.dispatch().

docs/kiro-port-spec.md section 4. Four hooks, all pointing at hook/dispatch.py with
AUTOHARNESS_HOST=kiro, which hands the payload here:

- agentSpawn        lifecycle pass + skill index + last-run line; merge requests from the last days.
- preToolUse        write guard (deny a direct write into an autoharness-managed path); count the
                    tool call (the reflection numerator); count a read into a managed skill as
                    use (SKILL.md) or view (any other file).
- stop              the turn boundary, as on Claude: request counters, drain the intent queue, then
                    at most one job — reflection, else consolidation, else (AUTO_PUBLISH) a merge
                    request for unpublished skill changes — plus any merge-request note, handed to
                    the agent as a stop-block reason so it takes one more turn.
- userPromptSubmit  the `autoharness learn | publish | status` prompts; in prompt mode, the job the
                    previous stop found due.

Loop guard: a stop that follows a block fires like any other and nothing in its payload says so,
so a block leaves a per-session mark and a stop that finds the mark never blocks — with one bounded
exception: the stop ending a learning turn may ask for the automatic publish of what just landed,
and the stop ending a publish turn never asks for anything. The mark also
tells preToolUse not to count the reflection's own tool calls and reads as work or skill use. It
is cleared at the next prompt and at session start, so it cannot outlive its turn.

Everything is fail-safe: any error ends in an empty verdict and exit 0, because a hook must never
break the session it rides in.
"""
import json
import os
import sys

from autoharness import config
from autoharness.hook import (
    on_session_start,
    on_skill_call,
    on_stop,
    promoter,
    reflect,
    spawn,
)
from autoharness.host import kiro
from autoharness.lib import atomic, counters, layer, publish, sidecar, skill_store

COMMAND_PREFIX = "autoharness"
COMMANDS = ("learn", "publish", "status")
_FALLBACK_SESSION = "nosession"  # a payload without a usable session id still gets one counter, not a crash
DENY_MESSAGE = (
    "autoharness manages this path and blocked the write. Do not retry and do not work around it "
    f"with a shell command. To change a learned skill, propose it with the stage_skill tool "
    f"({config.STAGE_TOOL_KIRO}); otherwise tell the user to edit the file by hand."
)


def _roots(cwd):
    return {lyr: layer.default_root(lyr, cwd) if lyr == layer.PROJECT else layer.default_root(lyr)
            for lyr in layer.LAYERS}


def _session(ev):
    for candidate in (ev["session_id"], os.environ.get("KIRO_SESSION_ID", "")):
        if candidate and counters._SAFE_SESSION.match(candidate):
            return candidate
    return _FALLBACK_SESSION


# -- per-session marks -------------------------------------------------------------------------

def _mark_path(kind, session_id, proot):
    return layer.state_dir(layer.PROJECT, proot) / kind / f"{session_id}.json"


def _set(kind, session_id, proot, data):
    atomic.write_text(_mark_path(kind, session_id, proot), json.dumps(data))


def _peek(kind, session_id, proot):
    try:
        return json.loads(_mark_path(kind, session_id, proot).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _pop(kind, session_id, proot):
    data = _peek(kind, session_id, proot)
    try:
        _mark_path(kind, session_id, proot).unlink()
    except OSError:
        pass
    return data


_JOB = "job"   # this session is in a turn autoharness asked for (or is about to end one)
_DUE = "due"   # prompt mode: the job the last stop found due, waiting for the next prompt


# -- write guard -------------------------------------------------------------------------------

def guarded(path, roots):
    """What autoharness-managed thing a path is inside (for the deny message), or None."""
    try:
        target = path.resolve()
    except OSError:
        return None
    for lyr in layer.LAYERS:
        if target.is_relative_to(layer.state_dir(lyr, roots.get(lyr)).resolve()):
            return "state"
    for lyr in config.active_layers():
        root = roots.get(lyr)
        skills = layer.skills_dir(lyr, root).resolve()
        if not target.is_relative_to(skills):
            continue
        parts = target.relative_to(skills).parts
        if not parts:
            continue
        if parts[0] == layer.archive_dir(lyr, root).name:
            return "archive"
        try:
            if sidecar.is_agent_created(lyr, parts[0], root):
                return parts[0]
        except (ValueError, OSError):
            continue
    return None


# -- handlers ----------------------------------------------------------------------------------

def _unpublished_line(groot):
    pairs = publish.changes(groot)
    if not pairs:
        return None
    return (f"autoharness: {len(pairs)} self-learned skill change(s) are not in a merge request yet. "
            + ("One is opened automatically after the next change; to do it now the user can say"
               if config.AUTO_PUBLISH else "The user can say")
            + " `autoharness publish` to open one, or `autoharness status` to list them.")


def _read_hint(roots):
    dirs = [str(layer.skills_dir(lyr, roots.get(lyr))) for lyr in config.active_layers()]
    return "To use a skill listed above, open <skills dir>/<name>/SKILL.md with the read tool. Skills dirs: " \
        + ", ".join(dirs)


def _on_spawn(ev, sid, roots):
    proot, groot = roots[layer.PROJECT], roots[layer.GLOBAL]
    _pop(_JOB, sid, proot)
    result = on_session_start.on_session_start({"cwd": ev["cwd"]}, roots=roots)
    parts = []
    if result.get("context"):
        text = result["context"]
        if on_session_start.INDEX_HEADER in text:
            text += "\n\n" + _read_hint(roots)
        parts.append(text)
    line = _unpublished_line(groot)
    if line:
        parts.append(line)
    rows = publish.pending(sid, groot)
    if rows:
        parts.append(reflect.note(rows))
        publish.mark_announced(sid, rows, groot)
    return {"context": "\n\n".join(parts)} if parts else {}


def _on_tool(ev, sid, roots):
    proot = roots[layer.PROJECT]
    if ev["kind"] == "write":
        for path in ev["paths"]:
            if guarded(path, roots):
                return {"deny": DENY_MESSAGE}
    if _peek(_JOB, sid, proot):
        return {}  # the reflection's own calls are neither work to reflect on nor skill use
    counters.bump_session(sid, proot)
    if ev["kind"] == "read":
        for path in ev["paths"]:
            name = on_skill_call._name_from_read_path({"tool_input": {"file_path": str(path)}}, roots)
            if name:
                kind = "use" if path.name == skill_store.SKILL_FILE else "view"
                on_skill_call._count(name, roots, kind)
    return {}


def _record_publish(ev, sid, job, groot):
    url = publish.find_url(ev["response"])
    now = publish.changes(groot)
    landed = job.get("before") and now is not None and not now  # the tree went clean: it was committed
    if not url and not landed:
        return
    record = publish.add_record(url, job.get("lines") or [], groot)
    _auto_path(groot).unlink(missing_ok=True)  # published: the same change set may come up again later
    if url:  # this session's own reply already showed the link; without one, the note follows at the next prompt
        publish.mark_announced(sid, [record], groot)


def _auto_path(groot):
    return layer.state_dir(layer.GLOBAL, groot) / "auto_publish.json"


def _auto_publish(groot):
    """The publish job for this turn boundary, or None. One attempt per change set: a publish that
    failed (no access, the user declined the git commands) must not be asked for again every turn."""
    if not config.AUTO_PUBLISH:
        return None
    pairs = publish.changes(groot)
    if not pairs:
        return None
    lines = publish.summary(pairs)
    try:
        if json.loads(_auto_path(groot).read_text(encoding="utf-8")) == lines:
            return None
    except (OSError, ValueError):
        pass
    atomic.write_text(_auto_path(groot), json.dumps(lines))
    return {"kind": "publish", "before": len(pairs), "lines": lines}


def _due_job(sid, pcount, roots):
    """reflect, curate or None for this turn boundary. A consolidation that falls due on the same
    turn as a reflection is remembered, not dropped: its cadence only hits one exact count."""
    proot = roots[layer.PROJECT]
    flag = layer.state_dir(layer.PROJECT, proot) / "curate_due"
    if config.CONSOLIDATE_EVERY_N and pcount % config.CONSOLIDATE_EVERY_N == 0:
        atomic.write_text(flag, "1")
    if on_stop.on_stop({"session_id": sid}, root=proot).get("triggered"):
        return "reflect"
    if flag.exists():
        flag.unlink()
        agent_made = spawn.description_index(roots, agent_only=True).count("\n") + 1
        return "curate" if agent_made >= 2 else None  # fewer than two skills: nothing to fold
    return None


def _job_text(kind, sid, pcount, roots, *, then_answer=False):
    if kind == "reflect":
        return reflect.reflection(roots, then_answer=then_answer)
    spawn._snapshot_skills(f"{sid[:32]}-c{pcount}", roots)  # the one risk atomic landing cannot cover
    return reflect.curation(roots)


def _on_stop(ev, sid, roots):
    proot, groot = roots[layer.PROJECT], roots[layer.GLOBAL]
    job = _pop(_JOB, sid, proot)
    if job:  # the turn autoharness asked for just ended: land what it staged, never ask again
        promoter.drain(config.INTERACTIVE_RUN_ID, roots=roots)
        if job.get("kind") == "publish":
            _record_publish(ev, sid, job, groot)
            return {}
        auto = _auto_publish(groot)  # what the learning turn just landed
        if not auto:
            return {}
        if config.REFLECT_MODE == "prompt":
            _set(_DUE, sid, proot, auto)
            return {}
        _set(_JOB, sid, proot, auto)
        return {"block": reflect.publish_request(auto["lines"], groot, auto=True)}
    if not config.DISABLE_GLOBAL:
        counters.bump_request(layer.GLOBAL, groot)
    pcount = counters.bump_request(layer.PROJECT, proot)
    promoter.drain(config.INTERACTIVE_RUN_ID, roots=roots)
    if config.REFLECT_MODE == "off":
        kind = None
    else:
        kind = _due_job(sid, pcount, roots)
    auto = None if kind else _auto_publish(groot)
    if config.REFLECT_MODE == "prompt":
        if kind or auto:
            _set(_DUE, sid, proot, auto or {"kind": kind, "pcount": pcount})
        return {}  # notes also wait for the next prompt: this mode never relies on a stop block
    parts = [_job_text(kind, sid, pcount, roots)] if kind else []
    if auto:
        parts.append(reflect.publish_request(auto["lines"], groot, auto=True))
    rows = publish.pending(sid, groot)
    if rows:
        parts.append(reflect.note(rows))
        publish.mark_announced(sid, rows, groot)
    if not parts:
        return {}
    _set(_JOB, sid, proot, auto or {"kind": kind or "note"})
    return {"block": "\n\n".join(parts)}


def _command(prompt):
    words = prompt.strip().lower().split()
    if len(words) >= 2 and words[0] == COMMAND_PREFIX and words[1].strip(".!:,") in COMMANDS:
        return words[1].strip(".!:,")
    return None


def _on_prompt(ev, sid, roots):
    proot, groot = roots[layer.PROJECT], roots[layer.GLOBAL]
    _pop(_JOB, sid, proot)  # a mark still here means its stop never came: do not let it stick
    command = _command(ev["prompt"])
    if command == "learn":
        _pop(_DUE, sid, proot)
        counters.reset_session(sid, proot)
        _set(_JOB, sid, proot, {"kind": "learn"})
        return {"context": reflect.reflection(roots, forced=True)}
    if command == "publish":
        pairs = publish.changes(groot)
        if pairs is None:
            return {"context": reflect.publish_unavailable(
                "no shared skills repository is configured, or the skills folder is not in a git clone.")}
        if not pairs:
            return {"context": reflect.publish_unavailable("there are no unpublished skill changes.")}
        lines = publish.summary(pairs)
        _set(_JOB, sid, proot, {"kind": "publish", "before": len(pairs), "lines": lines})
        return {"context": reflect.publish_request(lines, groot)}
    if command == "status":
        return {"context": reflect.status(publish.records(groot), publish.changes(groot))}
    parts = []
    due = _pop(_DUE, sid, proot)
    if due and config.REFLECT_MODE == "prompt":
        kind = due.get("kind")
        if kind == "publish":
            _set(_JOB, sid, proot, due)
            parts.append(reflect.publish_request(due.get("lines") or [], groot, auto=True, then_answer=True))
        else:
            _set(_JOB, sid, proot, {"kind": kind})
            parts.append(_job_text(kind, sid, due.get("pcount", 0), roots, then_answer=True))
    rows = publish.pending(sid, groot)
    if rows:
        parts.append(reflect.note(rows))
        publish.mark_announced(sid, rows, groot)
    return {"context": "\n\n".join(parts)} if parts else {}


_HANDLERS = {kiro.AGENT_SPAWN: _on_spawn, kiro.PRE_TOOL: _on_tool,
             kiro.STOP: _on_stop, kiro.USER_PROMPT: _on_prompt}


def handle(payload, *, roots=None):
    """Route one Kiro hook payload to a verdict: {} | {"context"} | {"block"} | {"deny"}."""
    ev = kiro.normalize(payload)
    handler = _HANDLERS.get(ev["event"])
    if handler is None:
        return {}
    try:
        return handler(ev, _session(ev), roots or _roots(ev["cwd"]))
    except Exception as exc:  # fail-safe: a buggy handler must never crash the host hook
        return {"error": f"{type(exc).__name__}: {exc}"}


def run(payload):
    if os.environ.get(config.CHILD_SESSION_ENV):
        return 0
    return kiro.emit(handle(payload), stdout=sys.stdout, stderr=sys.stderr)
