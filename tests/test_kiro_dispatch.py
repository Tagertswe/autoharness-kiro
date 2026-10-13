"""hook/kiro_dispatch: the Kiro event routing, driven with payloads shaped like kiro-cli 2.29.1's.

The agent is played by the test: where a real session would call the stage_skill MCP tool, the test
calls server.stage() on the same queue, so a whole reflect -> stage -> land loop runs without Kiro."""
import json

import pytest

from autoharness import config
from autoharness.hook import kiro_dispatch, promoter
from autoharness.lib import counters, layer, sidecar, skill_store
from autoharness.stage_skill import server

SID = "0f8fad5b-d9cb-469f-a165-70867728950e"
OTHER = "7c9e6679-7425-40de-944b-e07fc1f90ae7"
BODY = "---\nname: {name}\ndescription: Use when formatting a date as ISO.\ncategory: dates\n---\n# T\nUse strftime.\n"


@pytest.fixture
def roots(tmp_path, monkeypatch):
    monkeypatch.delenv(config.CHILD_SESSION_ENV, raising=False)
    monkeypatch.delenv("KIRO_SESSION_ID", raising=False)
    monkeypatch.setattr(config, "REFLECT_EVERY_N", 3)
    monkeypatch.setattr(config, "CONSOLIDATE_EVERY_N", 0)
    monkeypatch.setattr(config, "REFLECT_MODE", "stop")
    monkeypatch.setattr(config, "SHARED_SKILLS_REPO", "")
    return {layer.GLOBAL: tmp_path / "home" / ".kiro", layer.PROJECT: tmp_path / "proj" / ".kiro"}


def _send(roots, event, sid=SID, **fields):
    payload = {"hook_event_name": event, "cwd": str(roots[layer.PROJECT].parent), "session_id": sid, **fields}
    return kiro_dispatch.handle(payload, roots=roots)


def _tool(roots, name, tool_input, sid=SID):
    return _send(roots, "preToolUse", sid, tool_name=name, tool_input=tool_input, tool_tags=[name, "@builtin"])


def _work(roots, n, sid=SID):
    for _ in range(n):
        assert _tool(roots, "shell", {"command": "ls"}, sid) == {}


def _stage(roots, name="iso-dates", level="project"):
    out = server.stage({"action": "create", "name": name, "level": level, "body": BODY.format(name=name),
                        "reason": "repeat", "evidence": "synthetic evidence"},
                       run_id=config.INTERACTIVE_RUN_ID, root=roots[layer.PROJECT])
    assert out["ok"], out["errors"]


def _land(roots, name="iso-dates", level="project"):
    v = promoter.promote({"action": "create", "name": name, "level": level, "body": BODY.format(name=name),
                          "reason": "r", "evidence": "e"}, roots=roots)
    assert v["ok"], v["findings"]
    return layer.symbol_dir(level, name, roots[level])


# -- reflection at the end of the turn ----------------------------------------------------------

def test_stop_below_threshold_is_silent_and_counts_one_request(roots):
    _work(roots, 2)
    assert _send(roots, "stop", assistant_response="ok") == {}
    assert counters.request_count(layer.PROJECT, roots[layer.PROJECT]) == 1
    assert counters.request_count(layer.GLOBAL, roots[layer.GLOBAL]) == 1


def test_threshold_blocks_once_then_the_following_stop_lands_what_was_staged(roots):
    _work(roots, 3)
    verdict = _send(roots, "stop", assistant_response="done")
    assert "reflection pass" in verdict["block"]
    assert config.STAGE_TOOL_KIRO in verdict["block"]
    assert str(config.FORMAT_SPEC) in verdict["block"]
    assert counters.session_count(SID, roots[layer.PROJECT]) == 0  # the window restarts

    assert _tool(roots, "@stage_skill/stage_skill", {"action": "create"}) == {}
    _stage(roots)
    assert counters.session_count(SID, roots[layer.PROJECT]) == 0  # the reflection's own calls are not work

    assert _send(roots, "stop", assistant_response="staged iso-dates") == {}  # never a second block: no loop
    assert sidecar.is_agent_created(layer.PROJECT, "iso-dates", roots[layer.PROJECT])
    assert counters.request_count(layer.PROJECT, roots[layer.PROJECT]) == 1  # the extra turn is not a request

    _work(roots, 1)
    assert _send(roots, "stop") == {}  # and the mark is gone: an ordinary turn again
    assert counters.session_count(SID, roots[layer.PROJECT]) == 1


def test_counters_are_per_session(roots):
    _work(roots, 2, SID)
    _work(roots, 2, OTHER)
    assert _send(roots, "stop", SID) == {}
    _work(roots, 1, OTHER)
    assert "block" in _send(roots, "stop", OTHER)
    assert "block" not in _send(roots, "stop", SID)


def test_a_stale_mark_is_cleared_by_the_next_prompt_and_by_session_start(roots):
    _work(roots, 3)
    assert "block" in _send(roots, "stop")
    assert _send(roots, "userPromptSubmit", prompt="next task") == {}  # the follow-up stop never came
    _work(roots, 3)
    assert "block" in _send(roots, "stop")  # so the next threshold still triggers
    _send(roots, "agentSpawn")
    _work(roots, 3)
    assert "block" in _send(roots, "stop")


def test_reflect_mode_off_never_blocks_but_learn_still_works(roots, monkeypatch):
    monkeypatch.setattr(config, "REFLECT_MODE", "off")
    _work(roots, 5)
    assert _send(roots, "stop") == {}
    verdict = _send(roots, "userPromptSubmit", prompt="autoharness learn")
    assert "asked for an autoharness reflection" in verdict["context"]


def test_prompt_mode_defers_the_instruction_to_the_next_prompt(roots, monkeypatch):
    monkeypatch.setattr(config, "REFLECT_MODE", "prompt")
    _work(roots, 3)
    assert _send(roots, "stop") == {}  # this mode never relies on a stop block
    verdict = _send(roots, "userPromptSubmit", prompt="fix the bug")
    assert "reflection pass" in verdict["context"]
    assert "handle the user's request below" in verdict["context"]
    _stage(roots)
    assert _send(roots, "stop") == {}
    assert skill_store.exists(layer.PROJECT, "iso-dates", roots[layer.PROJECT])
    assert _send(roots, "userPromptSubmit", prompt="another") == {}  # delivered once


def test_learn_prompt_forces_a_reflection_whatever_the_counter_says(roots):
    for prompt in ("autoharness learn", "  Autoharness Learn.", "autoharness learn from this session"):
        verdict = _send(roots, "userPromptSubmit", prompt=prompt)
        assert "asked for an autoharness reflection" in verdict["context"], prompt
    _stage(roots)
    assert _send(roots, "stop") == {}
    assert skill_store.exists(layer.PROJECT, "iso-dates", roots[layer.PROJECT])


@pytest.mark.parametrize("prompt", ["", "learn", "autoharness", "autoharnesslearn", "please autoharness learn",
                                    "autoharness learning is neat"])
def test_ordinary_prompts_inject_nothing(roots, prompt):
    assert _send(roots, "userPromptSubmit", prompt=prompt) == {}


def test_disable_project_tells_the_agent_to_stage_global(roots, monkeypatch):
    monkeypatch.setattr(config, "DISABLE_PROJECT", True)
    text = _send(roots, "userPromptSubmit", prompt="autoharness learn")["context"]
    assert 'level "global"' in text
    assert "[project]" not in text
    _stage(roots, level="project")  # a project create is rerouted, not refused
    _send(roots, "stop")
    assert skill_store.exists(layer.GLOBAL, "iso-dates", roots[layer.GLOBAL])
    assert not layer.skills_dir(layer.PROJECT, roots[layer.PROJECT]).exists()


# -- consolidation -------------------------------------------------------------------------------

def test_curation_delegates_to_the_curator_agent_and_snapshots_first(roots, monkeypatch):
    _land(roots, "iso-dates")
    _land(roots, "iso-times")
    monkeypatch.setattr(config, "CONSOLIDATE_EVERY_N", 2)
    assert _send(roots, "stop") == {}
    verdict = _send(roots, "stop")
    assert config.KIRO_CURATOR_AGENT in verdict["block"]
    assert "iso-dates" in verdict["block"] and "iso-times" in verdict["block"]
    snapshots = layer.state_dir(layer.PROJECT, roots[layer.PROJECT]) / "snapshots"
    assert [p.name for p in snapshots.glob("*-project.tar.gz")]


def test_curation_due_with_a_reflection_waits_for_the_next_turn(roots, monkeypatch):
    _land(roots, "iso-dates")
    _land(roots, "iso-times")
    monkeypatch.setattr(config, "CONSOLIDATE_EVERY_N", 1)
    _work(roots, 3)
    assert "reflection pass" in _send(roots, "stop")["block"]  # one job per stop, reflection first
    assert _send(roots, "stop") == {}                           # the reflection's own stop
    assert config.KIRO_CURATOR_AGENT in _send(roots, "stop")["block"]


def test_curation_is_skipped_when_there_is_nothing_to_fold(roots, monkeypatch):
    _land(roots, "iso-dates")
    monkeypatch.setattr(config, "CONSOLIDATE_EVERY_N", 1)
    assert _send(roots, "stop") == {}


# -- session start -------------------------------------------------------------------------------

def test_agent_spawn_prints_the_index_with_a_read_hint(roots):
    assert _send(roots, "agentSpawn") == {}  # empty library: zero injection
    _land(roots)
    text = _send(roots, "agentSpawn")["context"]
    assert "iso-dates [project]" in text
    assert str(layer.skills_dir(layer.PROJECT, roots[layer.PROJECT])) in text
    assert "read tool" in text


# -- usage counting ------------------------------------------------------------------------------

def test_reading_skill_md_counts_use_and_other_files_count_view(roots):
    sdir = _land(roots)
    (sdir / "references").mkdir(exist_ok=True)
    (sdir / "references" / "notes.md").write_text("n")
    _tool(roots, "read", {"operations": [{"mode": "Line", "path": str(sdir / "SKILL.md")},
                                         {"mode": "Line", "path": str(sdir / "references" / "notes.md")}]})
    data = sidecar.read(layer.PROJECT, "iso-dates", roots[layer.PROJECT])
    assert (data["use"], data["view"]) == (1, 1)


def test_reads_during_a_reflection_are_not_skill_use(roots):
    sdir = _land(roots)
    _send(roots, "userPromptSubmit", prompt="autoharness learn")
    _tool(roots, "read", {"operations": [{"mode": "Line", "path": str(sdir / "SKILL.md")}]})
    assert sidecar.read(layer.PROJECT, "iso-dates", roots[layer.PROJECT])["use"] == 0


def test_reading_a_hand_written_skill_writes_no_sidecar(roots):
    sdir = layer.skills_dir(layer.PROJECT, roots[layer.PROJECT]) / "mine"
    sdir.mkdir(parents=True)
    (sdir / "SKILL.md").write_text("---\nname: mine\ndescription: d\n---\nx\n")
    _tool(roots, "read", {"operations": [{"mode": "Line", "path": str(sdir / "SKILL.md")}]})
    assert not (sdir / sidecar.FILENAME).exists()


# -- write guard ---------------------------------------------------------------------------------

def test_writes_into_managed_paths_are_denied(roots, tmp_path, dir_link):
    sdir = _land(roots)
    archive = layer.archive_dir(layer.PROJECT, roots[layer.PROJECT])
    state = layer.state_dir(layer.GLOBAL, roots[layer.GLOBAL])
    alias = tmp_path / "alias"
    dir_link(alias, sdir)
    for target in (sdir / "SKILL.md", sdir / "references" / "new.md", archive / "old" / "SKILL.md",
                   state / "publishes.jsonl", alias / "SKILL.md",
                   sdir.parent / "other" / ".." / "iso-dates" / "SKILL.md"):
        verdict = _tool(roots, "write", {"command": "create", "path": str(target), "content": "x"})
        assert config.STAGE_TOOL_KIRO in verdict["deny"], target


def test_writes_elsewhere_and_into_hand_written_skills_pass(roots, tmp_path):
    _land(roots)
    mine = layer.skills_dir(layer.PROJECT, roots[layer.PROJECT]) / "mine" / "SKILL.md"
    for target in (tmp_path / "proj" / "src" / "a.py", mine):
        assert _tool(roots, "write", {"command": "create", "path": str(target), "content": "x"}) == {}


def test_a_relative_write_path_is_resolved_against_the_session_cwd(roots):
    _land(roots)
    verdict = _tool(roots, "write", {"command": "create", "path": ".kiro/skills/iso-dates/SKILL.md", "content": "x"})
    assert "deny" in verdict


# -- robustness ----------------------------------------------------------------------------------

@pytest.mark.parametrize("payload", [{}, {"hook_event_name": "postToolUse"}, {"hook_event_name": "Stop"},
                                     {"hook_event_name": "somethingNew", "session_id": SID}])
def test_unrouted_events_do_nothing(roots, payload):
    assert kiro_dispatch.handle(payload, roots=roots) == {}


def test_a_payload_without_a_session_id_still_counts(roots, monkeypatch):
    for _ in range(3):
        kiro_dispatch.handle({"hook_event_name": "preToolUse", "tool_name": "shell", "tool_input": {}}, roots=roots)
    assert "block" in kiro_dispatch.handle({"hook_event_name": "stop"}, roots=roots)
    monkeypatch.setenv("KIRO_SESSION_ID", SID)  # and the environment's id is used when the payload has none
    kiro_dispatch.handle({"hook_event_name": "preToolUse", "tool_name": "shell", "tool_input": {}}, roots=roots)
    assert counters.session_count(SID, roots[layer.PROJECT]) == 1


def test_a_failing_handler_never_raises(roots, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("x")
    monkeypatch.setattr(kiro_dispatch.promoter, "drain", boom)
    assert kiro_dispatch.handle({"hook_event_name": "stop", "session_id": SID}, roots=roots) \
        == {"error": "RuntimeError: x"}


def test_dispatch_main_routes_by_host_and_prints_nothing_unintended(roots, monkeypatch, capsys, tmp_path):
    import io

    from autoharness import host
    from autoharness.hook import dispatch
    monkeypatch.setenv(host.HOST_ENV, "kiro")
    monkeypatch.setattr(kiro_dispatch, "_roots", lambda cwd: roots)
    _land(roots)
    target = layer.symbol_dir(layer.PROJECT, "iso-dates", roots[layer.PROJECT]) / "SKILL.md"

    def run(payload):
        monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
        code = dispatch.main()
        out = capsys.readouterr()
        return code, out.out, out.err

    base = {"cwd": str(tmp_path), "session_id": SID}
    assert run({**base, "hook_event_name": "preToolUse", "tool_name": "shell", "tool_input": {}}) == (0, "", "")
    code, out, err = run({**base, "hook_event_name": "preToolUse", "tool_name": "write",
                          "tool_input": {"path": str(target)}})
    assert (code, out) == (2, "") and "stage_skill" in err
    code, out, err = run({**base, "hook_event_name": "agentSpawn"})
    assert code == 0 and "iso-dates" in out and err == ""
    for _ in range(2):
        run({**base, "hook_event_name": "preToolUse", "tool_name": "shell", "tool_input": {}})
    code, out, err = run({**base, "hook_event_name": "stop", "assistant_response": "x"})
    assert code == 0 and json.loads(out)["decision"] == "block" and err == ""
    assert run({**base, "hook_event_name": "stop", "assistant_response": "x"}) == (0, "", "")
