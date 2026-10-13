"""Instruction text for the Kiro host: what a hook asks the working agent to do.

docs/kiro-port-spec.md 4.2, 4.3, 4.10. On Kiro there is no child session and no API key to start
one with, so the agent that did the work reflects in its own conversation: a hook hands it one of
these instructions (as a stop-block reason, or as prompt context) and the agent stages intents
through stage_skill. Deterministic assembly only — the same index and format spec the Claude
carrier feeds its reflector, addressed to a reader that already holds the conversation.
"""
from autoharness import config
from autoharness.hook import spawn
from autoharness.lib import layer, publish, usage

_INDEX_DESC_MAX = 240  # the index rides inside a stop-block reason: a scan surface, not the full trigger text


def _index(roots, *, agent_only=False):
    lines = []
    for line in spawn.description_index(roots, agent_only=agent_only).splitlines():
        lines.append(line if len(line) <= _INDEX_DESC_MAX else line[:_INDEX_DESC_MAX - 3] + "...")
    return "\n".join(lines)


def _where(roots):
    lines = []
    for lyr in config.active_layers():
        lines.append(f"- [{lyr}] {layer.skills_dir(lyr, roots.get(lyr))}/<name>/SKILL.md")
    return "\n".join(lines)


def _routing():
    if config.DISABLE_PROJECT:
        return ("Every skill goes to the shared global library: stage with level \"global\". A global "
                "skill must be repository-agnostic — no absolute paths and no project names in it.\n")
    return ""


def reflection(roots, *, forced=False, then_answer=False):
    """The reflection pass. forced: the user asked for it (`autoharness learn`). then_answer: the
    instruction rides on a real prompt (prompt mode), so the agent must still answer it afterwards."""
    opening = ("The user asked for an autoharness reflection pass now." if forced
               else "Autoharness reflection pass, triggered automatically by the amount of work in this session.")
    closing = ("When you are done, say in one line what you staged, then handle the user's request below as normal."
               if then_answer else
               "When you are done, say in one line what you staged, or that nothing was worth keeping. "
               "Do not resume or redo the previous task.")
    return (
        f"{opening}\n"
        "The conversation so far is your evidence source.\n"
        "Compare-first against the skill index below and against every other skill you have loaded: "
        "prefer patching an existing autoharness skill over creating a new one; distill only "
        "class-level reusable lessons, never session narratives. When you distill a new rule or "
        "preference, look for overlapping or contradicting statements in the autoharness skills and "
        "stage updates so the new supersedes the old. Skills that are not in the index below are not "
        "yours to change.\n"
        f"Propose every change exclusively through the stage_skill tool ({config.STAGE_TOOL_KIRO}) — "
        "never write skill files directly; a direct write is blocked. Evidence must quote this "
        "session verbatim. If a call is rejected, fix the intent from the returned errors and stage "
        "it again. If nothing is worth keeping, stage nothing.\n"
        f"{_routing()}"
        f"Before staging a create or update, read the authoring and format spec: {config.FORMAT_SPEC}\n"
        f"{closing}\n"
        "\n# Existing autoharness skills (compare-first: dedupe / patch / where)\n\n"
        f"{_index(roots)}\n"
        "\n# Where their files are\n\n"
        f"{_where(roots)}\n"
    )


def curation(roots):
    return (
        "Autoharness consolidation pass, triggered automatically.\n"
        f"Delegate it to the `{config.KIRO_CURATOR_AGENT}` agent with the subagent tool, giving it the "
        "task text between the markers below unchanged. If that agent or the subagent tool is not "
        "available, skip the pass and say so; do not attempt it yourself.\n"
        "When it returns, say in one line what it staged. Do not resume or redo the previous task.\n"
        "\n----- task for the curator -----\n"
        "# Agent-authored skills (consolidate: merge narrow siblings into class-level umbrellas)\n\n"
        f"{_index(roots, agent_only=True)}\n"
        "\n# Where their files are\n\n"
        f"{_where(roots)}\n"
        f"\n# Authoring + format spec (merged skills must satisfy this): read {config.FORMAT_SPEC}\n"
        f"{_routing()}"
        "----- end of task -----\n"
    )


PUBLISH_NOTE_PREFIX = "autoharness: opened a merge request for self-learned skills:"


def publish_request(lines, groot=None, *, auto=False, then_answer=False):
    skill = (f"Follow the merge-request skill at {config.PUBLISH_SKILL} to do it."
             if config.PUBLISH_SKILL.endswith(".md") else
             f"The repository's own skills are under {config.PUBLISH_SKILL}: follow the ones that cover "
             "its conventions and merge-request creation."
             if config.PUBLISH_SKILL else
             "Use the repository's usual merge-request procedure; if a skill for it is loaded, follow that skill.")
    why = ("Autoharness publishes self-learned skill changes automatically (the user turned this on), "
           "and some are not in a merge request yet.\n"
           if auto else
           "The user asked autoharness to publish the self-learned skills to the shared skills repository.\n")
    after = ("After that, answer the user's prompt as usual and repeat the note line at the end of your answer.\n"
             if then_answer else
             "Do not resume or redo the previous task.\n" if auto else "")
    return (
        why +
        f"Open one merge request from the clone at {publish.repo_dir(groot) or '(not configured)'} that "
        f"contains only the changes under {publish.skills_folder(groot)}. {skill}\n"
        "Work through git commands; do not edit the skill files. Commit nothing outside that folder "
        "and do not push to the default branch directly.\n"
        "Use this as the merge request description:\n\n"
        "Self-learned skill changes (autoharness):\n"
        + "\n".join(f"- {line}" for line in lines)
        + "\n\nThen tell the user with a note in exactly this form, the link complete and unshortened:\n"
          f"{PUBLISH_NOTE_PREFIX} <the changes> -> <full merge request URL>\n"
          "If no merge request could be opened, say why in one line and give no URL.\n"
        + after
    )


def publish_unavailable(reason):
    return f"The user asked autoharness to publish, but there is nothing to do: {reason} Tell the user that in one line."


def note(rows):
    return (
        "Autoharness note for the user. Pass this on in your own reply, briefly and with the link(s) "
        "unchanged, then carry on: a merge request with self-learned skill changes was opened.\n"
        + "\n".join(publish.describe(r) for r in rows) + "\n"
    )


def status(rows, pairs):
    lines = ["The user asked for the autoharness status. Show them the following, unchanged:", ""]
    lines.append("Merge requests opened for self-learned skills:")
    lines += [publish.describe(r) for r in rows] or ["- none recorded"]
    lines.append("")
    if pairs is None:
        lines.append("Shared skills repository: not configured, or not a git clone.")
    else:
        lines.append(f"Skill changes not yet in a merge request: {len(pairs)}")
        lines += [f"- {line}" for line in publish.summary(pairs)]
        if pairs:
            lines.append("Say `autoharness publish` to open a merge request for them.")
    return "\n".join(lines) + "\n"


def usage_report(pairs, days, folder):
    """`autoharness usage`: what the turns autoharness asked for added up to (lib/usage)."""
    lines = ["The user asked what autoharness itself has cost. Show them the following, unchanged:", "",
             f"Turns autoharness asked for in the last {days} days:"]
    for kind, t in usage.totals(pairs):
        lines.append(f"- {kind}: {t['turns']} turn(s), {round(t['seconds'])} s, {t['tool_calls']} tool call(s), "
                     f"{t['chars']} characters in and out")
    if not pairs:
        lines.append("- none recorded")
    lines += ["", f"One log file per day: {folder}",
              "Kiro does not tell a hook how many credits a turn used, so credits are not in the log. "
              "Each entry has the time and session of the turn, to match against Kiro's own credit figures."]
    return "\n".join(lines) + "\n"
