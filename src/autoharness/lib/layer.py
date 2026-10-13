"""Resolves a layer name into its on-disk location — the one place where a layer surfaces as a path.

The rest of the code is layer-agnostic and passes layer in as an argument. Unknown layers / unsafe
symbol names are always rejected (fail-safe, deny-by-default), because this is the chokepoint that
builds filesystem paths: privilege escalation / path traversal must be stopped here.

The project layer's identity is the session cwd — except inside a linked git worktree, where it is
remapped to the main worktree root, so counters / intents / skills from all worktrees of one repo
land in one place and survive worktree removal. Only the linked-worktree case is remapped (git-dir
differs from git-common-dir): plain repos, repo subdirectories, and non-git directories keep cwd
verbatim, so a nested project can never be attributed to an enclosing repo. Any git failure falls
back to cwd (fail-safe).

Two relocations for a deployment that keeps learned skills in a shared repository
(docs/kiro-port-spec.md 4.10), both read from the environment because config imports this module:
- AUTOHARNESS_GLOBAL_SKILLS_DIR moves the global layer's skills (and archive) out of the host
  directory; state stays under the home directory.
- AUTOHARNESS_DISABLE_PROJECT=1 moves the project layer's root under the home directory, keyed by
  the project path, so counters and the intent queue are never written into the repository.
"""
import hashlib
import os
import re
import subprocess
from functools import cache
from pathlib import Path

from autoharness import host

GLOBAL = "global"
PROJECT = "project"
LAYERS = (GLOBAL, PROJECT)

GLOBAL_SKILLS_DIR_ENV = "AUTOHARNESS_GLOBAL_SKILLS_DIR"
DISABLE_PROJECT_ENV = "AUTOHARNESS_DISABLE_PROJECT"

_SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def _check_layer(layer):
    if layer not in LAYERS:
        raise ValueError(f"unknown layer: {layer!r} (expected one of {LAYERS})")


def _check_name(name):
    if not isinstance(name, str) or ".." in name or not _SAFE_NAME.match(name):
        raise ValueError(f"unsafe symbol name: {name!r}")


def _main_worktree_root(cwd):
    return _main_worktree_root_resolved(str(Path(cwd).resolve()))


@cache
def _main_worktree_root_resolved(cwd):
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--git-dir", "--git-common-dir"],
            cwd=cwd, capture_output=True, text=True, timeout=5,
        )
        lines = proc.stdout.splitlines()
        if proc.returncode != 0 or len(lines) < 2:
            return Path(cwd)
        git_dir, common_dir = ((Path(cwd) / line).resolve() for line in lines[:2])
        if git_dir != common_dir:
            return common_dir.parent
    except (OSError, subprocess.SubprocessError):
        pass
    return Path(cwd)


def project_disabled():
    return os.environ.get(DISABLE_PROJECT_ENV, "").strip() == "1"


def default_root(layer, cwd=None):
    _check_layer(layer)
    home_root = Path.home() / host.root_dirname()
    if layer == GLOBAL:
        return home_root
    project = _main_worktree_root(str(cwd or Path.cwd()))
    if project_disabled():
        digest = hashlib.sha256(str(project).encode("utf-8")).hexdigest()[:16]
        return home_root / "autoharness" / "projects" / digest
    return project / host.root_dirname()


def _root(layer, root):
    _check_layer(layer)
    return Path(root) if root is not None else default_root(layer)


def skills_dir(layer, root=None):
    base = _root(layer, root)
    if layer == GLOBAL:
        override = os.environ.get(GLOBAL_SKILLS_DIR_ENV, "").strip()
        # only the real global root is redirected: an explicit root (tests, tools) keeps its own tree
        if override and base == default_root(GLOBAL):
            return Path(override).expanduser()
    return base / "skills"


def archive_dir(layer, root=None):
    return skills_dir(layer, root) / ".archive"


def state_dir(layer, root=None):
    return _root(layer, root) / "autoharness"


def symbol_dir(layer, name, root=None):
    _check_name(name)
    return skills_dir(layer, root) / name


SUBFILE_DIRS = ("scripts", "templates", "assets", "references")
EVIDENCE_PREFIX = "references/evidence-"


def check_subfile(rel):
    if not isinstance(rel, str) or not rel or ".." in rel or "\\" in rel:
        raise ValueError(f"unsafe subfile path: {rel!r}")
    segments = rel.split("/")
    if len(segments) < 2 or segments[0] not in SUBFILE_DIRS:
        raise ValueError(f"subfile path must sit under one of {SUBFILE_DIRS}: {rel!r}")
    for segment in segments:
        _check_name(segment)


def subfile_path(layer, name, rel, root=None):
    check_subfile(rel)
    return symbol_dir(layer, name, root) / rel
