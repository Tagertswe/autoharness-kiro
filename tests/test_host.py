"""The host seam (autoharness.host) and the directories that derive from it (lib/layer)."""
from pathlib import Path

import pytest

from autoharness import host
from autoharness.lib import layer


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    monkeypatch.delenv(layer.GLOBAL_SKILLS_DIR_ENV, raising=False)
    monkeypatch.delenv(layer.DISABLE_PROJECT_ENV, raising=False)
    return tmp_path / "home"


@pytest.mark.parametrize("value, expected", [(None, "claude"), ("", "claude"), ("kiro", "kiro"),
                                             (" Kiro ", "kiro"), ("claude", "claude"), ("nope", "claude")])
def test_host_name_defaults_to_claude(monkeypatch, value, expected):
    if value is None:
        monkeypatch.delenv(host.HOST_ENV, raising=False)
    else:
        monkeypatch.setenv(host.HOST_ENV, value)
    assert host.name() == expected


def test_roots_follow_the_host(home, tmp_path, monkeypatch):
    project = tmp_path / "proj"
    project.mkdir()
    monkeypatch.delenv(host.HOST_ENV, raising=False)
    assert layer.default_root(layer.GLOBAL) == home / ".claude"
    assert layer.default_root(layer.PROJECT, project) == project.resolve() / ".claude"
    monkeypatch.setenv(host.HOST_ENV, "kiro")
    assert layer.default_root(layer.GLOBAL) == home / ".kiro"
    assert layer.default_root(layer.PROJECT, project) == project.resolve() / ".kiro"
    assert layer.skills_dir(layer.GLOBAL) == home / ".kiro" / "skills"
    assert layer.state_dir(layer.GLOBAL) == home / ".kiro" / "autoharness"


def test_global_skills_dir_override_moves_skills_and_archive_but_not_state(home, tmp_path, monkeypatch):
    monkeypatch.setenv(host.HOST_ENV, "kiro")
    shared = tmp_path / "shared" / "learned"
    monkeypatch.setenv(layer.GLOBAL_SKILLS_DIR_ENV, str(shared))
    assert layer.skills_dir(layer.GLOBAL) == shared
    assert layer.archive_dir(layer.GLOBAL) == shared / ".archive"
    assert layer.symbol_dir(layer.GLOBAL, "foo") == shared / "foo"
    assert layer.state_dir(layer.GLOBAL) == home / ".kiro" / "autoharness"
    assert layer.skills_dir(layer.PROJECT, tmp_path / "p") == tmp_path / "p" / "skills"


def test_global_skills_dir_override_leaves_an_explicit_root_alone(home, tmp_path, monkeypatch):
    monkeypatch.setenv(layer.GLOBAL_SKILLS_DIR_ENV, str(tmp_path / "shared"))
    assert layer.skills_dir(layer.GLOBAL, tmp_path / "g") == tmp_path / "g" / "skills"


def test_disable_project_keeps_all_project_state_out_of_the_repository(home, tmp_path, monkeypatch):
    monkeypatch.setenv(host.HOST_ENV, "kiro")
    monkeypatch.setenv(layer.DISABLE_PROJECT_ENV, "1")
    one, two = tmp_path / "one", tmp_path / "two"
    one.mkdir()
    two.mkdir()
    root_one, root_two = layer.default_root(layer.PROJECT, one), layer.default_root(layer.PROJECT, two)
    assert root_one.is_relative_to(home / ".kiro" / "autoharness" / "projects")
    assert root_one != root_two  # keyed by project: two repositories never share counters
    assert root_one == layer.default_root(layer.PROJECT, one)  # and stable across hook processes
    assert not layer.state_dir(layer.PROJECT, root_one).is_relative_to(one)


@pytest.mark.parametrize("name, ok", [("iso-dates", True), ("a1", True), ("x" * 64, True), ("x" * 65, False),
                                      ("Iso-Dates", False), ("iso_dates", False), ("iso.dates", False),
                                      ("-lead", False), ("trail-", False), ("two--hyphens", False)])
def test_kiro_enforces_the_agent_skills_name_rule_and_claude_does_not(monkeypatch, name, ok):
    monkeypatch.setenv(host.HOST_ENV, "kiro")
    assert (host.skill_name_error(name) is None) == ok
    monkeypatch.setenv(host.HOST_ENV, "claude")
    assert host.skill_name_error(name) is None


def test_a_badly_named_create_is_rejected_at_staging_and_at_promotion_on_kiro(tmp_path, monkeypatch):
    from autoharness.hook import promoter
    from autoharness.stage_skill import server
    monkeypatch.setenv(host.HOST_ENV, "kiro")
    roots = {layer.GLOBAL: tmp_path / "g", layer.PROJECT: tmp_path / "p"}
    intent = {"action": "create", "name": "Iso_Dates", "level": "project", "reason": "r", "evidence": "e",
              "body": "---\nname: Iso_Dates\ndescription: Use when formatting a date as ISO.\n---\n# T\nUse strftime.\n"}
    staged = server.stage(dict(intent), run_id="interactive", root=roots[layer.PROJECT])
    assert not staged["ok"] and "lowercase" in staged["errors"][0][1]
    verdict = promoter.promote(dict(intent), roots=roots)
    assert not verdict["ok"] and verdict["findings"][0][0] == "shape"
    monkeypatch.setenv(host.HOST_ENV, "claude")
    assert promoter.promote(dict(intent), roots=roots)["ok"]  # upstream behaviour is untouched
