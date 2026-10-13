"""Host seam: which agent host this process is serving, and the one fact every layer needs from it.

autoharness was written for Claude Code; this fork also runs under Kiro CLI 2.x. The core modules
stay host-agnostic and ask here instead of naming a host. Claude is the default, so an unset
environment behaves exactly like upstream; the Kiro installer bakes AUTOHARNESS_HOST=kiro into the
hook and MCP commands it writes. Read from the environment at call time, not import time: a hook is
a fresh process either way, and tests switch hosts without reloading modules.

Per-host payload translation and output protocol live in host/claude.py and host/kiro.py; the Kiro
event routing is hook/kiro_dispatch.py (docs/kiro-port-spec.md section 4).
"""
import os
import re

HOST_ENV = "AUTOHARNESS_HOST"
CLAUDE = "claude"
KIRO = "kiro"
_ROOT_DIRNAME = {CLAUDE: ".claude", KIRO: ".kiro"}


def name():
    value = os.environ.get(HOST_ENV, "").strip().lower()
    return value if value in _ROOT_DIRNAME else CLAUDE  # unknown host: fall back, never crash a hook


def root_dirname():
    """The per-user / per-project config directory the host owns (skills, state and archive derive from it)."""
    return _ROOT_DIRNAME[name()]


# Kiro loads skills in the Agent Skills format, whose `name` must match the folder and be lowercase
# letters, digits and single hyphens, at most 64 characters. Claude's loader is laxer, and
# layer._SAFE_NAME (the path-safety check) allows uppercase, dots and underscores for it.
_KIRO_SKILL_NAME = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


def skill_name_error(skill_name):
    """Why the current host could not load a skill of this name, or None. Path safety is not judged
    here (lib/layer does that for every host); this is only the host's own format rule."""
    if name() != KIRO or not isinstance(skill_name, str):
        return None
    if len(skill_name) > 64 or not _KIRO_SKILL_NAME.match(skill_name):
        return (f"skill name {skill_name!r} must be lowercase letters, digits and single hyphens, "
                "at most 64 characters (Agent Skills format)")
    return None
