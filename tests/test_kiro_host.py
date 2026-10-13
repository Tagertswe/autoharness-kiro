"""host/kiro: translation of the payloads kiro-cli 2.29.1 sends (shapes recorded by kiro/probe) and
the output protocol the probe confirmed."""
import io
import json
from pathlib import Path

import pytest

from autoharness.host import kiro

SID = "0f8fad5b-d9cb-469f-a165-70867728950e"


def _tool(name, tool_input, tags=None, cwd="/work/repo"):
    return {"hook_event_name": "preToolUse", "cwd": cwd, "session_id": SID, "tool_name": name,
            "tool_input": {"__tool_use_purpose": "x", **tool_input},
            "tool_tags": tags if tags is not None else [name, "@builtin"]}


def test_read_carries_one_path_per_operation():
    ev = kiro.normalize(_tool("read", {"operations": [
        {"mode": "Line", "path": "/a/SKILL.md"}, {"mode": "Line", "path": "docs/x.md"}, "junk", {"mode": "x"}]}))
    assert (ev["event"], ev["kind"], ev["session_id"]) == ("preToolUse", "read", SID)
    assert ev["paths"] == [Path("/a/SKILL.md"), Path("/work/repo/docs/x.md")]  # relative: against the session cwd


def test_write_shell_subagent_and_mcp_kinds():
    assert kiro.normalize(_tool("write", {"command": "create", "content": "c", "path": "/a/b.txt"}))["paths"] \
        == [Path("/a/b.txt")]
    assert kiro.normalize(_tool("write", {"path": "b.txt"}))["kind"] == "write"
    assert kiro.normalize(_tool("shell", {"command": "ls"}))["kind"] == "shell"
    assert kiro.normalize(_tool("subagent", {"task": "t", "stages": []}))["kind"] == "subagent"
    mcp = kiro.normalize(_tool("@stage_skill/stage_skill", {"action": "create"},
                               ["@stage_skill/stage_skill", "@stage_skill", "@mcp"]))
    assert (mcp["kind"], mcp["paths"]) == ("mcp", [])


@pytest.mark.parametrize("name, kind", [("fs_read", "read"), ("fs_write", "write"),
                                        ("execute_bash", "shell"), ("use_subagent", "subagent"),
                                        ("something_new", "other")])
def test_non_interactive_tool_names_map_to_the_same_kinds(name, kind):
    assert kiro.normalize(_tool(name, {}, tags=[]))["kind"] == kind


def test_a_renamed_tool_is_still_classified_by_its_tags():
    assert kiro.normalize(_tool("reader2", {}, tags=["read", "@builtin"]))["kind"] == "read"


def test_prompt_and_stop_fields():
    prompt = kiro.normalize({"hook_event_name": "userPromptSubmit", "cwd": "/w", "session_id": SID, "prompt": "hi"})
    stop = kiro.normalize({"hook_event_name": "stop", "cwd": "/w", "session_id": SID, "assistant_response": "done"})
    assert (prompt["prompt"], stop["response"]) == ("hi", "done")


@pytest.mark.parametrize("payload", [None, [], {}, {"hook_event_name": 3, "tool_input": "x", "tool_tags": "y",
                                                    "session_id": None, "cwd": 5, "prompt": ["a"]}])
def test_malformed_payloads_degrade_to_empty_fields(payload):
    ev = kiro.normalize(payload)
    assert (ev["event"], ev["session_id"], ev["cwd"], ev["prompt"], ev["paths"]) == ("", "", None, "", [])


def _emit(verdict):
    out, err = io.StringIO(), io.StringIO()
    return kiro.emit(verdict, stdout=out, stderr=err), out.getvalue(), err.getvalue()


def test_emit_protocol():
    assert _emit({}) == (0, "", "")
    assert _emit({"error": "boom"}) == (0, "", "")  # diagnostics never reach the model's context
    assert _emit({"context": "index"}) == (0, "index\n", "")
    assert _emit({"deny": "no"}) == (2, "", "no\n")
    code, out, err = _emit({"block": "reflect\nnow"})
    assert (code, err) == (0, "")
    assert json.loads(out) == {"decision": "block", "reason": "reflect\nnow"}
