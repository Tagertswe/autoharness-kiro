"""The Kiro probe (kiro/probe): throwaway diagnostics, but they run on a machine nobody can debug
remotely, so the script's own contract is pinned here — what it logs, what it prints, what it blocks."""
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

PROBE_DIR = Path(__file__).resolve().parent.parent / "kiro" / "probe"


def _load(name):
    spec = importlib.util.spec_from_file_location(f"ahprobe_{name}", PROBE_DIR / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_report():
    sys.path.insert(0, str(PROBE_DIR))  # report.py imports its sibling probe.py by bare name
    try:
        return _load("report")
    finally:
        sys.path.remove(str(PROBE_DIR))


def _hook(tmp_path, event, payload, agent="autoharness-probe"):
    stdin = payload if isinstance(payload, str) else json.dumps(payload)
    return subprocess.run(
        [sys.executable, str(PROBE_DIR / "probe.py"), event, agent],
        input=stdin, capture_output=True, text=True,
        env={"AHPROBE_DIR": str(tmp_path), "PATH": "", "KIRO_TEST_VAR": "secret-value"},
    )


def _log(tmp_path):
    return [json.loads(line) for line in (tmp_path / "log.jsonl").read_text().splitlines()]


def test_spawn_and_prompt_print_distinct_numbered_markers(tmp_path):
    spawn = _hook(tmp_path, "agentSpawn", {"hook_event_name": "agentSpawn", "cwd": "/p"})
    first = _hook(tmp_path, "userPromptSubmit", {"hook_event_name": "userPromptSubmit", "prompt": "hi"})
    second = _hook(tmp_path, "userPromptSubmit", {"hook_event_name": "userPromptSubmit", "prompt": "hi"})
    assert spawn.stdout.startswith("AHPROBE-SPAWN-1 ")
    assert first.stdout.startswith("AHPROBE-PROMPT-1 ")
    assert second.stdout.startswith("AHPROBE-PROMPT-2 ")
    assert spawn.returncode == first.returncode == second.returncode == 0


def test_payload_is_logged_with_event_and_agent(tmp_path):
    payload = {"hook_event_name": "preToolUse", "tool_name": "fs_read", "tool_input": {"path": "/p/a"}}
    _hook(tmp_path, "preToolUse", payload, agent="autoharness-probe-skills")
    (entry,) = _log(tmp_path)
    assert entry["event"] == "preToolUse"
    assert entry["agent"] == "autoharness-probe-skills"
    assert entry["payload"] == payload
    assert entry["python"] == sys.version.split()[0]


def test_env_values_never_reach_the_log(tmp_path):
    _hook(tmp_path, "agentSpawn", {})
    (entry,) = _log(tmp_path)
    assert "KIRO_TEST_VAR" in entry["env_names"]
    assert "secret-value" not in (tmp_path / "log.jsonl").read_text()


def test_pre_tool_use_blocks_only_the_marked_call(tmp_path):
    blocked = _hook(tmp_path, "preToolUse", {
        "tool_name": "fs_write", "tool_input": {"path": "/p/ahprobe-block-test.txt"}})
    allowed = _hook(tmp_path, "preToolUse", {
        "tool_name": "fs_write", "tool_input": {"path": "/p/other.txt"}})
    assert blocked.returncode == 2
    assert "AHPROBE-DENIED" in blocked.stderr
    assert blocked.stdout == ""
    assert allowed.returncode == 0
    assert allowed.stdout == allowed.stderr == ""


def test_post_tool_use_is_silent(tmp_path):
    result = _hook(tmp_path, "postToolUse", {"tool_name": "fs_read", "tool_response": "x"})
    assert (result.returncode, result.stdout, result.stderr) == (0, "", "")


def test_stop_asks_for_another_turn_exactly_once(tmp_path):
    first = _hook(tmp_path, "stop", {"hook_event_name": "stop"})
    second = _hook(tmp_path, "stop", {"hook_event_name": "stop"})
    decision = json.loads(first.stdout)
    assert decision["decision"] == "block"
    assert "AHPROBE-STOP-CONTINUED" in decision["reason"]
    assert second.stdout.startswith("AHPROBE-STOP-2 ")  # never a second block: no loop
    assert first.returncode == second.returncode == 0


@pytest.mark.parametrize("stdin", ["", "not json {"])
def test_bad_stdin_is_recorded_not_fatal(tmp_path, stdin):
    result = _hook(tmp_path, "userPromptSubmit", stdin)
    (entry,) = _log(tmp_path)
    assert result.returncode == 0
    assert entry["payload"] is None
    assert entry["raw"] == stdin


def test_setup_writes_three_agents_with_absolute_hook_paths(tmp_path):
    setup = _load("setup")
    written = setup.install(tmp_path, python="python3")
    assert sorted(p.name for p in written) == [
        "autoharness-probe-skills.json", "autoharness-probe-sub.json", "autoharness-probe.json"]
    main = json.loads((tmp_path / "agents" / "autoharness-probe.json").read_text())
    assert set(main["hooks"]) == set(setup.EVENTS)
    for event, hooks in main["hooks"].items():
        (hook,) = hooks
        assert str(PROBE_DIR / "probe.py") in hook["command"]
        assert hook["command"].endswith(f" {event} autoharness-probe")
    assert Path(main["mcpServers"]["ahprobe"]["args"][0]) == PROBE_DIR / "mcp_echo.py"
    skills = json.loads((tmp_path / "agents" / "autoharness-probe-skills.json").read_text())
    assert skills["resources"] == [f"skill://{PROBE_DIR / 'skills'}/*/SKILL.md"]
    assert "hooks" not in json.loads((tmp_path / "agents" / "autoharness-probe-sub.json").read_text())


def test_setup_no_mcp_leaves_the_server_out(tmp_path):
    setup = _load("setup")
    setup.install(tmp_path, mcp=False)
    main = json.loads((tmp_path / "agents" / "autoharness-probe.json").read_text())
    assert "mcpServers" not in main


def test_setup_is_idempotent_and_uninstall_removes_only_its_own(tmp_path):
    setup = _load("setup")
    setup.install(tmp_path)
    setup.install(tmp_path)  # second run overwrites its own files without complaint
    mine = tmp_path / "agents" / "my-agent.json"
    mine.write_text(json.dumps({"name": "my-agent", "description": "hand written"}))
    removed = setup.uninstall(tmp_path)
    assert len(removed) == 3
    assert [p.name for p in (tmp_path / "agents").iterdir()] == ["my-agent.json"]


def test_setup_refuses_to_overwrite_a_foreign_agent(tmp_path):
    setup = _load("setup")
    foreign = tmp_path / "agents" / "autoharness-probe.json"
    foreign.parent.mkdir(parents=True)
    foreign.write_text(json.dumps({"name": "autoharness-probe", "description": "someone else's"}))
    with pytest.raises(SystemExit, match="refusing to overwrite"):
        setup.install(tmp_path)
    assert json.loads(foreign.read_text())["description"] == "someone else's"
    assert setup.uninstall(tmp_path) == []
    assert foreign.exists()


def test_mcp_echo_speaks_enough_protocol_to_be_called():
    requests = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
         "params": {"name": "probe_echo", "arguments": {"text": "hi"}}},
        {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "nope"}},
    ]
    proc = subprocess.run(
        [sys.executable, str(PROBE_DIR / "mcp_echo.py")],
        input="".join(json.dumps(r) + "\n" for r in requests), capture_output=True, text=True)
    init, listed, called, unknown = (json.loads(line) for line in proc.stdout.splitlines())
    assert init["result"]["serverInfo"]["name"] == "ahprobe"
    assert listed["result"]["tools"][0]["name"] == "probe_echo"
    assert called["result"]["content"][0]["text"] == "AHPROBE-MCP-OK hi"
    assert unknown["error"]["code"] == -32602


def test_report_shows_shapes_but_not_contents(tmp_path):
    _hook(tmp_path, "userPromptSubmit", {
        "hook_event_name": "userPromptSubmit", "cwd": "/p", "prompt": "my private prompt",
        "session_id": "abc"})
    _hook(tmp_path, "preToolUse", {
        "hook_event_name": "preToolUse", "tool_name": "fs_write",
        "tool_input": {"path": "/p/a.txt", "file_text": "private file body"}})
    result = subprocess.run(
        [sys.executable, str(PROBE_DIR / "report.py")], capture_output=True, text=True,
        env={"AHPROBE_DIR": str(tmp_path), "PATH": ""})
    out = result.stdout
    assert result.returncode == 0
    assert "prompt: str[17]" in out
    assert "fs_write -> {file_text, path}" in out
    assert "fields that look like a session id: ['session_id']" in out
    assert "autoharness-probe  preToolUse  1" in out
    assert "private" not in out


def test_report_without_a_log_says_the_hooks_never_ran(tmp_path):
    result = subprocess.run(
        [sys.executable, str(PROBE_DIR / "report.py")], capture_output=True, text=True,
        env={"AHPROBE_DIR": str(tmp_path), "PATH": ""})
    assert result.returncode == 1
    assert "hooks never ran" in result.stdout


@pytest.mark.parametrize("version, verdict", [("3.10.12", "ok"), ("3.14.4", "ok"),
                                              ("3.9.18", "TOO OLD"), ("None", "TOO OLD")])
def test_report_says_whether_the_hook_python_can_run_autoharness(version, verdict):
    report = _load_report()
    lines = report.summarise([{"event": "agentSpawn", "agent": "a", "python": version}])
    assert f"autoharness needs Python 3.10+: {verdict}" in lines
