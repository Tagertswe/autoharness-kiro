"""Shared skills repository (docs/kiro-port-spec.md 4.10): what is unpublished, the merge-request
record, and how sessions are told. All against a throwaway git repository."""
import shutil
import subprocess

import pytest

from autoharness import config, install
from autoharness.hook import kiro_dispatch, promoter
from autoharness.lib import layer, publish, skill_store

SID = "0f8fad5b-d9cb-469f-a165-70867728950e"
OTHER = "7c9e6679-7425-40de-944b-e07fc1f90ae7"
THIRD = "3f2504e0-4f89-41d3-9a0c-0305e82c3301"
URL = "https://gitlab.example.com/group/shared-skills/-/merge_requests/42"
BODY = "---\nname: {name}\ndescription: Use when formatting a date as ISO.\ncategory: dates\n---\n# T\nUse strftime.\n"

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="needs git")


def _git(repo, *args):
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com", "-c", "commit.gpgsign=false",
                    *args], cwd=repo, check=True, capture_output=True)


@pytest.fixture
def roots(tmp_path, monkeypatch):
    """A clone whose `shared/skills` folder is the global layer's skills directory."""
    repo = tmp_path / "repo"
    (repo / "handwritten" / "mr-skill").mkdir(parents=True)
    (repo / "handwritten" / "mr-skill" / "SKILL.md").write_text("---\nname: mr-skill\ndescription: d\n---\n")
    _git(repo, "init", "-q")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "init")
    monkeypatch.delenv(config.CHILD_SESSION_ENV, raising=False)
    monkeypatch.delenv("KIRO_SESSION_ID", raising=False)
    monkeypatch.setattr(config, "SHARED_SKILLS_REPO", "")
    monkeypatch.setattr(config, "PUBLISH_SKILL", "")
    monkeypatch.setattr(config, "AUTO_PUBLISH", False)
    monkeypatch.setattr(config, "REFLECT_EVERY_N", 1000)
    monkeypatch.setattr(config, "CONSOLIDATE_EVERY_N", 0)
    monkeypatch.setattr(config, "REFLECT_MODE", "stop")
    monkeypatch.setattr(config, "DISABLE_PROJECT", True)
    roots = {layer.GLOBAL: repo / "shared", layer.PROJECT: tmp_path / "state"}
    folder = layer.skills_dir(layer.GLOBAL, roots[layer.GLOBAL])
    folder.mkdir(parents=True)
    (folder / ".gitignore").write_text(install.GITIGNORE)
    # state must not sit inside the clone in a real deployment; here it does, so keep it out of git status
    (repo / ".git" / "info" / "exclude").write_text("shared/autoharness/\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "folder")
    return roots


def _land(roots, name):
    v = promoter.promote({"action": "create", "name": name, "level": "global", "body": BODY.format(name=name),
                          "reason": "r", "evidence": "the user said so"}, roots=roots)
    assert v["ok"], v["findings"]


def _commit(roots, message="skills"):
    repo = roots[layer.GLOBAL].parent
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", message)


def _send(roots, event, sid=SID, **fields):
    return kiro_dispatch.handle({"hook_event_name": event, "cwd": str(roots[layer.PROJECT]),
                                 "session_id": sid, **fields}, roots=roots)


# -- what is unpublished -------------------------------------------------------------------------

def test_changes_classify_created_updated_and_removed(roots):
    groot = roots[layer.GLOBAL]
    assert publish.changes(groot) == []
    _land(roots, "iso-dates")
    _land(roots, "iso-times")
    assert publish.changes(groot) == [("created", "iso-dates"), ("created", "iso-times")]
    _commit(roots)
    assert publish.changes(groot) == []
    v = promoter.promote({"action": "patch", "name": "iso-dates", "old_string": "Use strftime.",
                          "new_string": "Use isoformat.", "reason": "r", "evidence": "e"}, roots=roots)
    assert v["ok"], v["findings"]
    skill_store.archive(layer.GLOBAL, "iso-times", groot)
    assert publish.changes(groot) == [("removed", "iso-times"), ("updated", "iso-dates")]


def test_bookkeeping_files_never_show_up_as_changes(roots):
    groot = roots[layer.GLOBAL]
    _land(roots, "iso-dates")
    sdir = layer.symbol_dir(layer.GLOBAL, "iso-dates", groot)
    assert (sdir / ".sidecar.json").exists() and (sdir / ".ledger.jsonl").exists()
    assert list((sdir / "references").glob("evidence-*.md"))
    _commit(roots)
    tracked = subprocess.run(["git", "ls-files", "shared/skills"], cwd=groot.parent,
                             capture_output=True, text=True).stdout.split()
    assert tracked == ["shared/skills/.gitignore", "shared/skills/iso-dates/SKILL.md"]


def test_changes_is_none_outside_a_git_clone(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SHARED_SKILLS_REPO", "")
    groot = tmp_path / "plain"
    (groot / "skills").mkdir(parents=True)
    assert publish.changes(groot) is None
    assert publish.changes(tmp_path / "missing") is None


@pytest.mark.parametrize("text, expected", [
    (f"Opened it.\n{URL}", URL),
    (f"See {URL}.", URL),
    (f"old https://gitlab.example.com/g/p/-/merge_requests/1 new {URL}\n", URL),
    ("https://github.com/o/r/pull/7 done", "https://github.com/o/r/pull/7"),
    ("no link here: https://gitlab.example.com/g/p/-/issues/3", None),
    ("", None),
])
def test_find_url(text, expected):
    assert publish.find_url(text) == expected


# -- the publish prompt --------------------------------------------------------------------------

def test_publish_prompt_injects_a_summary_and_the_stop_records_the_link(roots, monkeypatch):
    groot = roots[layer.GLOBAL]
    monkeypatch.setattr(config, "PUBLISH_SKILL", "/path/to/shared-skills-repo/mr-skill/SKILL.md")
    _land(roots, "iso-dates")
    text = _send(roots, "userPromptSubmit", prompt="autoharness publish")["context"]
    assert "- created iso-dates" in text
    assert "/path/to/shared-skills-repo/mr-skill/SKILL.md" in text
    assert str(layer.skills_dir(layer.GLOBAL, groot)) in text
    assert _send(roots, "stop", assistant_response=f"Merge request opened.\n{URL}") == {}
    (record,) = publish.records(groot)
    assert (record["url"], record["changes"]) == (URL, ["created iso-dates"])
    assert publish.pending(SID, groot) == []  # the session that opened it already showed the link


def test_publish_prompt_points_at_a_folder_of_skills_when_that_is_what_is_configured(roots, monkeypatch):
    monkeypatch.setattr(config, "PUBLISH_SKILL", "/path/to/shared-skills-repo/skills")
    _land(roots, "iso-dates")
    text = _send(roots, "userPromptSubmit", prompt="autoharness publish")["context"]
    assert "skills are under /path/to/shared-skills-repo/skills" in text
    assert "merge-request creation" in text


def test_publish_with_nothing_to_publish_says_so_and_records_nothing(roots):
    text = _send(roots, "userPromptSubmit", prompt="autoharness publish")["context"]
    assert "no unpublished skill changes" in text
    assert _send(roots, "stop", assistant_response=f"nothing to do {URL}") == {}
    assert publish.records(roots[layer.GLOBAL]) == []


def test_publish_without_a_link_is_recorded_only_if_the_tree_went_clean(roots):
    groot = roots[layer.GLOBAL]
    _land(roots, "iso-dates")
    _send(roots, "userPromptSubmit", prompt="autoharness publish")
    assert _send(roots, "stop", assistant_response="I could not push.") == {}
    assert publish.records(groot) == []  # nothing happened: no false record
    _send(roots, "userPromptSubmit", prompt="autoharness publish")
    _commit(roots)
    assert _send(roots, "stop", assistant_response="Pushed, but the forge printed no link.") == {}
    (record,) = publish.records(groot)
    assert record["url"] is None


def test_publish_when_no_repository_is_configured(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SHARED_SKILLS_REPO", "")
    roots = {layer.GLOBAL: tmp_path / "g", layer.PROJECT: tmp_path / "p"}
    text = _send(roots, "userPromptSubmit", prompt="autoharness publish")["context"]
    assert "no shared skills repository is configured" in text


# -- telling the other sessions ------------------------------------------------------------------

def test_each_session_announces_a_merge_request_exactly_once(roots):
    groot = roots[layer.GLOBAL]
    _send(roots, "agentSpawn", OTHER)
    _send(roots, "agentSpawn", THIRD)
    publish.add_record(URL, ["created iso-dates"], groot)

    busy = _send(roots, "stop", OTHER, assistant_response="finished my task")  # at the end of its action
    assert URL in busy["block"] and "created iso-dates" in busy["block"]
    assert _send(roots, "stop", OTHER) == {}                                    # the note's own stop: no loop
    assert _send(roots, "userPromptSubmit", OTHER, prompt="next") == {}
    assert _send(roots, "stop", OTHER) == {}

    idle = _send(roots, "userPromptSubmit", THIRD, prompt="hello")              # an idle one: with its next prompt
    assert URL in idle["context"]
    assert _send(roots, "stop", THIRD) == {}

    fresh = _send(roots, "agentSpawn", "a-new-session")                          # a new one: at startup
    assert URL in fresh["context"]
    assert _send(roots, "userPromptSubmit", "a-new-session", prompt="hi") == {}


def test_a_note_due_with_a_reflection_rides_in_the_same_instruction(roots, monkeypatch):
    monkeypatch.setattr(config, "REFLECT_EVERY_N", 1)
    publish.add_record(URL, ["created iso-dates"], roots[layer.GLOBAL])
    _send(roots, "preToolUse", tool_name="shell", tool_input={}, tool_tags=["shell", "@builtin"])
    verdict = _send(roots, "stop")
    assert "reflection pass" in verdict["block"] and URL in verdict["block"]


def test_old_merge_requests_are_not_announced_but_status_lists_them_all(roots):
    groot = roots[layer.GLOBAL]
    publish.add_record("https://gitlab.example.com/g/p/-/merge_requests/1", ["created old"], groot, now=1_000_000)
    publish.add_record(URL, ["created iso-dates"], groot)
    _land(roots, "iso-times")
    assert [r["url"] for r in publish.pending(SID, groot)] == [URL]
    text = _send(roots, "userPromptSubmit", prompt="autoharness status")["context"]
    assert "merge_requests/1" in text and URL in text
    assert "not yet in a merge request: 1" in text and "- created iso-times" in text


def test_session_start_reports_unpublished_changes(roots):
    assert _send(roots, "agentSpawn") == {}
    _land(roots, "iso-dates")
    assert "1 self-learned skill change(s) are not in a merge request yet" in _send(roots, "agentSpawn")["context"]


def test_publish_runs_no_git_command_that_writes(roots, monkeypatch):
    calls = []
    real = publish.subprocess.run

    def spy(argv, *a, **k):
        calls.append(argv)
        return real(argv, *a, **k)
    monkeypatch.setattr(publish.subprocess, "run", spy)
    _land(roots, "iso-dates")
    _send(roots, "agentSpawn")
    _send(roots, "userPromptSubmit", prompt="autoharness status")
    _send(roots, "userPromptSubmit", prompt="autoharness publish")
    _send(roots, "stop", assistant_response=URL)
    assert calls and {argv[1] for argv in calls} <= {"status", "rev-parse"}


# -- automatic publish ---------------------------------------------------------------------------

NOTE = "autoharness: opened a merge request for self-learned skills:"


def test_auto_publish_follows_the_learning_turn_and_the_note_carries_the_full_link(roots, monkeypatch):
    monkeypatch.setattr(config, "AUTO_PUBLISH", True)
    groot = roots[layer.GLOBAL]
    assert _send(roots, "userPromptSubmit", prompt="autoharness learn")["context"]
    _land(roots, "iso-dates")  # what the learning turn staged and its stop lands
    text = _send(roots, "stop", assistant_response="Staged iso-dates.")["block"]
    assert "automatically" in text and "- created iso-dates" in text
    assert NOTE in text and "<full merge request URL>" in text
    _commit(roots)
    assert _send(roots, "stop", assistant_response=f"{NOTE} created iso-dates -> {URL}") == {}
    (record,) = publish.records(groot)
    assert (record["url"], record["changes"]) == (URL, ["created iso-dates"])
    assert publish.pending(SID, groot) == []          # shown in this session by its own reply
    assert URL in _send(roots, "stop", sid=OTHER, assistant_response="x")["block"]  # and noted in the others


def test_auto_publish_also_picks_up_changes_at_an_ordinary_turn_end(roots, monkeypatch):
    monkeypatch.setattr(config, "AUTO_PUBLISH", True)
    _land(roots, "iso-dates")
    assert "- created iso-dates" in _send(roots, "stop", assistant_response="done")["block"]


def test_auto_publish_is_off_by_default(roots):
    _land(roots, "iso-dates")
    assert _send(roots, "stop", assistant_response="done") == {}


def test_a_failed_auto_publish_is_not_asked_for_again_until_the_changes_differ(roots, monkeypatch):
    monkeypatch.setattr(config, "AUTO_PUBLISH", True)
    groot = roots[layer.GLOBAL]
    _land(roots, "iso-dates")
    assert "block" in _send(roots, "stop", assistant_response="done")
    assert _send(roots, "stop", assistant_response="I could not push: access denied.") == {}
    assert publish.records(groot) == []
    assert _send(roots, "stop", assistant_response="next turn") == {}   # same change set: no nagging
    _land(roots, "iso-times")
    assert "- created iso-times" in _send(roots, "stop", assistant_response="later")["block"]


def test_a_publish_without_a_captured_link_is_still_noted_in_the_session_that_made_it(roots, monkeypatch):
    monkeypatch.setattr(config, "AUTO_PUBLISH", True)
    _land(roots, "iso-dates")
    assert "block" in _send(roots, "stop", assistant_response="done")
    _commit(roots)
    assert _send(roots, "stop", assistant_response="Pushed the branch.") == {}
    assert "no link was captured" in _send(roots, "userPromptSubmit", prompt="hello")["context"]


def test_auto_publish_in_prompt_mode_waits_for_the_next_prompt(roots, monkeypatch):
    monkeypatch.setattr(config, "AUTO_PUBLISH", True)
    monkeypatch.setattr(config, "REFLECT_MODE", "prompt")
    groot = roots[layer.GLOBAL]
    _land(roots, "iso-dates")
    assert _send(roots, "stop", assistant_response="done") == {}
    text = _send(roots, "userPromptSubmit", prompt="what time is it")["context"]
    assert "- created iso-dates" in text and "answer the user's prompt" in text
    _commit(roots)
    assert _send(roots, "stop", assistant_response=f"It is noon.\n{NOTE} created iso-dates -> {URL}") == {}
    assert [r["url"] for r in publish.records(groot)] == [URL]
