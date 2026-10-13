"""autoharness.install: the agent configs written for Kiro CLI 2.x, against a temporary Kiro home."""
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

from autoharness import install
from autoharness.lib import layer

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def home(tmp_path):
    return tmp_path / "kiro-home"


def _values(**extra):
    return install.settings(None, [f"{k}={v}" for k, v in extra.items()], environ={})


def _load(path):
    return json.loads(path.read_text())


def test_main_agent_is_default_like_with_four_hooks_and_the_stage_server(home):
    written = install.install(home / "agents", _values(), home)
    assert sorted(p.name for p in written) == ["autoharness-curator.json", "autoharness.json"]
    agent = _load(home / "agents" / "autoharness.json")
    assert agent["tools"] == ["*"] and agent["includeMcpJson"] is True
    assert "prompt" not in agent and "allowedTools" not in agent and "toolsSettings" not in agent
    assert set(agent["hooks"]) == {"agentSpawn", "userPromptSubmit", "preToolUse", "stop"}
    for entries in agent["hooks"].values():
        (hook,) = entries
        assert "matcher" not in hook  # every tool call must reach the counter exactly once
        argv = shlex.split(hook["command"])
        assert argv[0] == "env" and argv[-3:] == ["python3", "-m", "autoharness.hook.dispatch"]
        assert "AUTOHARNESS_HOST=kiro" in argv and f"PYTHONPATH={ROOT / 'src'}" in argv
    server = agent["mcpServers"]["stage_skill"]
    assert server["args"] == ["-m", "autoharness.stage_skill.server"]
    assert server["env"]["AUTOHARNESS_HOST"] == "kiro" and server["env"]["PYTHONPATH"] == str(ROOT / "src")


def test_the_installed_hook_command_actually_runs(home, tmp_path):
    install.install(home / "agents", _values(), home)
    command = _load(home / "agents" / "autoharness.json")["hooks"]["userPromptSubmit"][0]["command"]
    argv = [sys.executable if a == "python3" else a for a in shlex.split(command)]
    payload = {"hook_event_name": "userPromptSubmit", "cwd": str(tmp_path), "session_id": "s1",
               "prompt": "autoharness status"}
    proc = subprocess.run(argv, input=json.dumps(payload), capture_output=True, text=True, cwd=tmp_path,
                          env={"PATH": os.environ.get("PATH", ""), "HOME": str(tmp_path / "h")})
    assert proc.returncode == 0, proc.stderr
    assert "Merge requests opened for self-learned skills" in proc.stdout
    assert proc.stderr == ""


def test_resources_keep_installed_skills_and_never_use_a_recursive_glob(home):
    values = _values(AUTOHARNESS_GLOBAL_SKILLS_DIR="/path/to/shared-skills-repo/self-learning-skills",
                     AUTOHARNESS_PUBLISH_SKILL="/path/to/shared-skills-repo/mr/SKILL.md")
    assert install.resources(values, home) == [
        "skill:///path/to/shared-skills-repo/self-learning-skills/*/SKILL.md",
        f"skill://{home}/skills/*/SKILL.md",
        "skill://.kiro/skills/*/SKILL.md",
        "skill:///path/to/shared-skills-repo/mr/SKILL.md",
    ]
    assert install.resources(_values(), home) == [f"skill://{home}/skills/*/SKILL.md", "skill://.kiro/skills/*/SKILL.md"]
    # a folder of skills instead of one file: every skill in it is loaded
    values["AUTOHARNESS_PUBLISH_SKILL"] = "/path/to/shared-skills-repo/skills"
    assert install.resources(values, home)[-1] == "skill:///path/to/shared-skills-repo/skills/*/SKILL.md"
    assert not any("**" in r for r in install.resources(values, home))


def test_settings_come_from_the_env_file_then_flags_and_only_autoharness_keys_are_baked(tmp_path):
    env = tmp_path / ".env"
    env.write_text("AUTOHARNESS_SHARED_SKILLS_REPO=/path/to/shared-skills-repo\n"
                   "AUTOHARNESS_GLOBAL_SKILLS_DIR=${AUTOHARNESS_SHARED_SKILLS_REPO}/self-learning-skills\n"
                   "AUTOHARNESS_REFLECT_EVERY_N=50\nSOMETHING_ELSE=secret\n")
    values = install.settings(env, ["AUTOHARNESS_REFLECT_EVERY_N=80", "AUTOHARNESS_HOST=claude"], environ={})
    assert values == {"AUTOHARNESS_SHARED_SKILLS_REPO": "/path/to/shared-skills-repo",
                      "AUTOHARNESS_GLOBAL_SKILLS_DIR": "/path/to/shared-skills-repo/self-learning-skills",
                      "AUTOHARNESS_REFLECT_EVERY_N": "80", "AUTOHARNESS_HOST": "kiro"}
    assert install.settings(tmp_path / "missing.env", [], environ={}) == {"AUTOHARNESS_HOST": "kiro"}
    with pytest.raises(SystemExit, match="KEY=VALUE"):
        install.settings(None, ["novalue"], environ={})


def test_settings_reach_both_the_hooks_and_the_stage_server(home):
    install.install(home / "agents", _values(AUTOHARNESS_DISABLE_PROJECT="1", AUTOHARNESS_NOTE="two words"), home)
    agent = _load(home / "agents" / "autoharness.json")
    argv = shlex.split(agent["hooks"]["stop"][0]["command"])
    assert "AUTOHARNESS_DISABLE_PROJECT=1" in argv and "AUTOHARNESS_NOTE=two words" in argv
    assert agent["mcpServers"]["stage_skill"]["env"]["AUTOHARNESS_DISABLE_PROJECT"] == "1"


def test_curator_agent_gets_the_prompt_read_and_staging_only(home):
    install.install(home / "agents", _values(), home)
    curator = _load(home / "agents" / "autoharness-curator.json")
    assert curator["tools"] == ["read", "@stage_skill"]
    assert "hooks" not in curator
    assert "umbrella" in curator["prompt"] and "@stage_skill/stage_skill" in curator["prompt"]
    assert not curator["prompt"].lstrip().startswith("---")  # the Claude frontmatter is not part of it


def test_install_is_idempotent_and_refuses_a_foreign_file(home):
    agents = home / "agents"
    install.install(agents, _values(), home)
    first = (agents / "autoharness.json").read_text()
    install.install(agents, _values(), home)
    assert (agents / "autoharness.json").read_text() == first
    install.uninstall(agents)
    (agents / "autoharness.json").write_text(json.dumps({"name": "autoharness", "description": "hand written"}))
    with pytest.raises(SystemExit, match="refusing to overwrite"):
        install.install(agents, _values(), home)
    assert _load(agents / "autoharness.json")["description"] == "hand written"


def test_uninstall_removes_only_what_was_installed(home):
    agents = home / "agents"
    install.install(agents, _values(), home)
    (agents / "mine.json").write_text(json.dumps({"name": "mine"}))
    install.uninstall(agents)
    assert [p.name for p in agents.iterdir()] == ["mine.json"]


def test_into_merges_hooks_without_disturbing_the_agent_and_uninstall_reverses_it(home):
    agents = home / "agents"
    agents.mkdir(parents=True)
    original = {"name": "work", "description": "my agent", "prompt": "be terse", "tools": ["read"],
                "resources": ["file://README.md", f"skill://{home}/skills/*/SKILL.md"],
                "hooks": {"stop": [{"command": "echo mine"}], "postToolUse": [{"command": "echo post"}]},
                "mcpServers": {"other": {"command": "x"}}}
    (agents / "work.json").write_text(json.dumps(original))
    install.install(agents, _values(), home, into="work")
    install.install(agents, _values(), home, into="work")  # twice: no duplicated hooks or resources
    merged = _load(agents / "work.json")
    assert (merged["prompt"], merged["tools"], merged["description"]) == ("be terse", ["read"], "my agent")
    assert [h["command"] for h in merged["hooks"]["stop"]][0] == "echo mine"
    assert sum("autoharness.hook.dispatch" in h["command"] for h in merged["hooks"]["stop"]) == 1
    assert set(merged["hooks"]) == {"stop", "postToolUse", "agentSpawn", "userPromptSubmit", "preToolUse"}
    assert set(merged["mcpServers"]) == {"other", "stage_skill"}
    assert merged["resources"].count(f"skill://{home}/skills/*/SKILL.md") == 1
    assert not (agents / "autoharness.json").exists() and (agents / "autoharness-curator.json").exists()
    assert _load(agents / ("work.json" + install.BACKUP_SUFFIX)) == original

    install.uninstall(agents)
    assert _load(agents / "work.json") == original  # the resource it already had is still there
    assert not (agents / "autoharness-curator.json").exists()


def test_into_a_missing_agent_fails_before_touching_anything(home):
    with pytest.raises(SystemExit, match="cannot merge into"):
        install.install(home / "agents", _values(), home, into="nope")


def test_gitignore_is_written_into_the_learned_skills_folder_only(tmp_path):
    repo = tmp_path / "shared-skills-repo"
    repo.mkdir()
    values = _values(AUTOHARNESS_GLOBAL_SKILLS_DIR=str(repo / "self-learning-skills"))
    path = install.write_gitignore(values)
    assert path == repo / "self-learning-skills" / ".gitignore"
    for pattern in (".archive/", "*/.sidecar.json", "*/.ledger.jsonl", "*/references/evidence-*.md"):
        assert pattern in path.read_text().splitlines()
    path.write_text("# mine\n")
    assert install.write_gitignore(values) is None and path.read_text() == "# mine\n"  # not ours: left alone
    assert install.write_gitignore(_values()) is None
    with pytest.raises(SystemExit, match="does not exist"):
        install.write_gitignore(_values(AUTOHARNESS_GLOBAL_SKILLS_DIR=str(tmp_path / "nope" / "x")))


def test_cli_end_to_end_with_a_temporary_home(home, tmp_path, capsys):
    env = tmp_path / ".env"
    env.write_text("AUTOHARNESS_REFLECT_EVERY_N=40\n")
    assert install.main(["--kiro-home", str(home), "--env-file", str(env)]) == 0
    out = capsys.readouterr().out
    assert "kiro-cli agent set-default autoharness" in out and "AUTOHARNESS_REFLECT_EVERY_N" in out
    assert (home / "agents" / "autoharness.json").exists()
    assert install.main(["--kiro-home", str(home), "--uninstall"]) == 0
    assert not (home / "agents" / "autoharness.json").exists()


def test_wrapper_script_runs_without_pythonpath(home):
    proc = subprocess.run([sys.executable, str(ROOT / "kiro" / "install.py"), "--kiro-home", str(home),
                           "--env-file", os.devnull],
                          capture_output=True, text=True, env={"PATH": os.environ.get("PATH", "")})
    assert proc.returncode == 0, proc.stderr
    assert (home / "agents" / "autoharness.json").exists()


def test_no_site_specific_name_is_shipped():
    # the fork is public: paths in committed examples must be placeholders
    assert layer.GLOBAL_SKILLS_DIR_ENV in (ROOT / ".env.example").read_text()
    for path in (ROOT / ".env.example", ROOT / "docs" / "kiro.md"):
        for line in path.read_text().splitlines():
            if "AUTOHARNESS_SHARED_SKILLS_REPO=" in line and not line.lstrip().startswith("#"):
                assert line.split("=", 1)[1].startswith("/path/to/"), line


def test_real_processes_end_to_end_learn_stage_land_into_a_shared_folder(home, tmp_path):
    """The installed commands as Kiro would run them: separate processes, a fresh HOME, settings only
    from what the installer baked in. A learn prompt, one stage_skill call over MCP stdio, then the
    stop hook lands the skill in the shared folder and leaves the project directory untouched."""
    shared = tmp_path / "shared-skills-repo"
    shared.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    fake_home = tmp_path / "h"
    values = _values(AUTOHARNESS_GLOBAL_SKILLS_DIR=str(shared / "self-learning-skills"), AUTOHARNESS_DISABLE_PROJECT="1")
    install.install(home / "agents", values, home)
    install.write_gitignore(values)
    agent = _load(home / "agents" / "autoharness.json")
    base_env = {"PATH": os.environ.get("PATH", ""), "HOME": str(fake_home)}

    def hook(event, **fields):
        argv = [sys.executable if a == "python3" else a for a in shlex.split(agent["hooks"][event][0]["command"])]
        payload = {"hook_event_name": event, "cwd": str(project), "session_id": "sess-1", **fields}
        return subprocess.run(argv, input=json.dumps(payload), capture_output=True, text=True,
                              cwd=project, env=base_env)

    assert hook("agentSpawn").stdout == ""
    learn = hook("userPromptSubmit", prompt="autoharness learn")
    assert learn.returncode == 0 and 'level "global"' in learn.stdout

    server = agent["mcpServers"]["stage_skill"]
    body = "---\nname: iso-dates\ndescription: Use when formatting a date as ISO.\ncategory: dates\n---\n# T\nUse strftime.\n"
    requests = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
                {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "stage_skill", "arguments": {
                    "action": "create", "name": "iso-dates", "body": body, "reason": "r", "evidence": "the user said so"}}}]
    mcp = subprocess.run([sys.executable, *server["args"]], input="".join(json.dumps(r) + "\n" for r in requests),
                         capture_output=True, text=True, cwd=project, env={**base_env, **server["env"]})
    staged = json.loads(json.loads(mcp.stdout.splitlines()[1])["result"]["content"][0]["text"])
    assert staged["ok"] and staged["intent"]["level"] == "global", staged

    stop = hook("stop", assistant_response="staged iso-dates")
    assert (stop.returncode, stop.stdout, stop.stderr) == (0, "", "")
    assert (shared / "self-learning-skills" / "iso-dates" / "SKILL.md").read_text() == body
    assert list(project.iterdir()) == []  # nothing was written into the repository being worked in
    assert (fake_home / ".kiro" / "autoharness" / "projects").is_dir()

    denied = hook("preToolUse", tool_name="write", tool_tags=["write", "@builtin"],
                  tool_input={"command": "create", "path": str(shared / "self-learning-skills" / "iso-dates" / "SKILL.md")})
    assert denied.returncode == 2 and "stage_skill" in denied.stderr
    spawn = hook("agentSpawn")
    assert "iso-dates [global]" in spawn.stdout and "landed 1" in spawn.stdout
