"""The cost log (lib/usage): one line per turn autoharness asked for, in a file named by date."""
import json
import time

import pytest

from autoharness import config
from autoharness.hook import kiro_dispatch
from autoharness.host import kiro
from autoharness.lib import layer, usage
from autoharness.stage_skill import server

SID = "0f8fad5b-d9cb-469f-a165-70867728950e"
BODY = "---\nname: {name}\ndescription: Use when formatting a date as ISO.\ncategory: dates\n---\n# T\nUse strftime.\n"
DAY = 86400


@pytest.fixture
def roots(tmp_path, monkeypatch):
    monkeypatch.delenv(config.CHILD_SESSION_ENV, raising=False)
    monkeypatch.delenv("KIRO_SESSION_ID", raising=False)
    monkeypatch.setattr(config, "REFLECT_EVERY_N", 3)
    monkeypatch.setattr(config, "CONSOLIDATE_EVERY_N", 0)
    monkeypatch.setattr(config, "REFLECT_MODE", "stop")
    monkeypatch.setattr(config, "SHARED_SKILLS_REPO", "")
    monkeypatch.setattr(config, "AUTO_PUBLISH", False)
    return {layer.GLOBAL: tmp_path / "home" / ".kiro", layer.PROJECT: tmp_path / "proj" / ".kiro"}


def _send(roots, event, **fields):
    payload = {"hook_event_name": event, "cwd": str(roots[layer.PROJECT].parent), "session_id": SID, **fields}
    return kiro_dispatch.handle(payload, roots=roots)


def _tool(roots):
    return _send(roots, "preToolUse", tool_name="shell", tool_input={"command": "ls"}, tool_tags=["shell", "@builtin"])


def _rows(roots):
    return [row for _, row in usage.read(1, roots[layer.GLOBAL])]


def test_a_reflection_turn_is_logged_in_a_file_named_by_date(roots):
    groot = roots[layer.GLOBAL]
    for _ in range(3):
        _tool(roots)
    text = _send(roots, "stop", assistant_response="done")["block"]
    assert _rows(roots) == []  # the turn has only been asked for
    out = server.stage({"action": "create", "name": "iso-dates", "level": "project",
                        "body": BODY.format(name="iso-dates"), "reason": "r", "evidence": "e"},
                       run_id=config.INTERACTIVE_RUN_ID, root=roots[layer.PROJECT])
    assert out["ok"], out["errors"]
    _tool(roots)
    _tool(roots)
    assert _send(roots, "stop", assistant_response="Staged iso-dates.") == {}

    (path,) = usage.directory(groot).glob("*.jsonl")
    assert path.name == time.strftime("%Y-%m-%d") + ".jsonl"
    assert path.is_relative_to(layer.state_dir(layer.GLOBAL, groot))
    (row,) = _rows(roots)
    assert row["ts"].startswith(time.strftime("%Y-%m-%d"))
    assert (row["session"], row["kind"], row["tool_calls"]) == (SID, "reflect", 2)
    assert (row["instruction_chars"], row["response_chars"]) == (len(text), len("Staged iso-dates."))
    assert (row["landed"], row["rejected"], row["shared_turn"]) == (1, 0, False)
    assert row["seconds"] >= 0 and row["merge_request"] is None and row["reported"] is None


def test_ordinary_turns_are_not_logged(roots):
    _tool(roots)
    assert _send(roots, "stop", assistant_response="done") == {}
    assert _send(roots, "userPromptSubmit", prompt="hello") == {}
    assert not usage.directory(roots[layer.GLOBAL]).exists()


def test_learn_is_logged_under_its_own_kind_and_prompt_mode_marks_a_shared_turn(roots, monkeypatch):
    _send(roots, "userPromptSubmit", prompt="autoharness learn")
    _send(roots, "stop", assistant_response="nothing worth keeping")
    monkeypatch.setattr(config, "REFLECT_MODE", "prompt")
    for _ in range(3):
        _tool(roots)
    _send(roots, "stop", assistant_response="done")
    _send(roots, "userPromptSubmit", prompt="what time is it")
    _send(roots, "stop", assistant_response="noon")
    assert [(r["kind"], r["shared_turn"]) for r in _rows(roots)] == [("learn", False), ("reflect", True)]


def test_a_usage_figure_in_the_payload_is_kept_verbatim(roots):
    assert kiro.normalize({"hook_event_name": "stop"})["reported"] == {}
    _send(roots, "userPromptSubmit", prompt="autoharness learn")
    _send(roots, "stop", assistant_response="x", credits_used=0.42, usage={"input_tokens": 10})
    (row,) = _rows(roots)
    assert row["reported"] == {"credits_used": 0.42, "usage": {"input_tokens": 10}}


def test_read_spans_days_and_totals_add_up_per_kind(roots):
    groot = roots[layer.GLOBAL]
    now = time.time()
    usage.append({"kind": "reflect", "seconds": 10, "tool_calls": 2, "instruction_chars": 100, "response_chars": 5},
                 groot, now=now - 2 * DAY)
    usage.append({"kind": "reflect", "seconds": 5, "tool_calls": 1, "instruction_chars": 10, "response_chars": 5},
                 groot, now=now)
    usage.append({"kind": "publish", "seconds": 30, "tool_calls": 6}, groot, now=now)
    usage.append({"kind": "learn", "seconds": 1}, groot, now=now - 40 * DAY)
    assert len(list(usage.directory(groot).glob("*.jsonl"))) == 3
    assert len(usage.read(1, groot, now=now)) == 2
    pairs = usage.read(30, groot, now=now)
    assert [day for day, _ in pairs] == sorted(day for day, _ in pairs) and len(pairs) == 3
    assert usage.totals(pairs) == [
        ("reflect", {"turns": 2, "seconds": 15.0, "tool_calls": 3, "chars": 120}),
        ("publish", {"turns": 1, "seconds": 30.0, "tool_calls": 6, "chars": 0})]


def test_a_damaged_line_is_skipped(roots):
    groot = roots[layer.GLOBAL]
    path = usage.append({"kind": "note"}, groot)
    with path.open("a", encoding="utf-8") as f:
        f.write("not json\n" + json.dumps(["a list"]) + "\n")
    assert [r["kind"] for r in _rows(roots)] == ["note"]


def test_the_usage_prompt_reports_totals_the_folder_and_that_credits_are_not_known(roots):
    _send(roots, "userPromptSubmit", prompt="autoharness learn")
    _send(roots, "stop", assistant_response="x")
    text = _send(roots, "userPromptSubmit", prompt="autoharness usage")["context"]
    assert "- learn: 1 turn(s)" in text
    assert str(usage.directory(roots[layer.GLOBAL])) in text
    assert "credits are not in the log" in text
    assert "none recorded" not in text


def test_a_log_that_cannot_be_written_never_stops_a_landing(roots, monkeypatch):
    def boom(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(usage, "append", boom)
    _send(roots, "userPromptSubmit", prompt="autoharness learn")
    assert _send(roots, "stop", assistant_response="x") == {}
