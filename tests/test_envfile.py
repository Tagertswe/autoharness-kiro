"""lib/envfile: the gitignored .env that keeps site-specific names out of this repository."""
import subprocess
from pathlib import Path

import pytest

from autoharness.lib import envfile

ROOT = Path(__file__).resolve().parents[1]


def test_reference_to_an_earlier_key_expands():
    values = envfile.loads("REPO=/srv/skills\nDIR=${REPO}/learned\n", environ={})
    assert values == {"REPO": "/srv/skills", "DIR": "/srv/skills/learned"}


def test_comments_blank_lines_quotes_and_export_prefix():
    text = "# note\n\nexport A=\"one two\"\nB='x'\nC=plain # not a comment\n"
    assert envfile.loads(text, environ={}) == {"A": "one two", "B": "x", "C": "plain # not a comment"}


def test_unknown_reference_is_an_error_not_an_empty_string():
    with pytest.raises(envfile.EnvFileError, match="NOPE"):
        envfile.loads("DIR=${NOPE}/x\n", environ={})


def test_reference_may_come_from_the_environment():
    assert envfile.loads("DIR=${HOME_X}/x\n", environ={"HOME_X": "/h"}) == {"DIR": "/h/x"}


def test_environment_wins_over_the_file():
    values = envfile.loads("REPO=/from/file\nDIR=${REPO}/d\n", environ={"REPO": "/from/env"})
    assert values == {"REPO": "/from/env", "DIR": "/from/env/d"}


def test_malformed_line_names_its_line_number():
    with pytest.raises(envfile.EnvFileError, match="line 2"):
        envfile.loads("A=1\nnot an assignment\n", environ={})


def test_env_files_are_ignored_by_git_and_the_example_is_not():
    def ignored(name):
        return subprocess.run(["git", "check-ignore", "-q", name], cwd=ROOT).returncode == 0
    if subprocess.run(["git", "rev-parse", "--git-dir"], cwd=ROOT, capture_output=True).returncode != 0:
        pytest.skip("not a git checkout")
    for name in (".env", ".env.local", "work.env", ".envrc"):
        assert ignored(name), name
    assert not ignored(".env.example")


def test_example_holds_placeholders_only():
    values = envfile.load(ROOT / ".env.example", environ={})
    for key, value in values.items():
        assert key.startswith("AUTOHARNESS_")
        assert not value.startswith("/") or value.startswith("/path/to/"), (key, value)
