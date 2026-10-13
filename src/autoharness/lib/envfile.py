"""Reader for the local .env file that holds site-specific settings (docs/kiro-port-spec.md 4.10).

Names and paths from a work environment must never be committed to this public repository, so they
live in a gitignored .env next to the checkout and the installer bakes them into the agent config
it writes under the home directory. Stdlib only (no python-dotenv), and deliberately small: plain
KEY=value lines, comments, optional quotes, and ${KEY} referring to an earlier key or to the
process environment. No command substitution and no defaults; a reference to an unknown key is an
error rather than an empty string, because a silently empty path would point the skill library at
the wrong place.
"""
import os
import re

_LINE = re.compile(r"^(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$")
_REF = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


class EnvFileError(ValueError):
    pass


def loads(text, environ=None):
    """Parse .env text into an ordered dict. A key already set in *environ* keeps the environment's
    value: what the operator exported for this run wins over the file."""
    environ = os.environ if environ is None else environ
    values = {}
    for lineno, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = _LINE.match(line)
        if not match:
            raise EnvFileError(f"line {lineno}: expected KEY=value, got {raw!r}")
        key, value = match.group(1), match.group(2).strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]

        def _expand(ref, lineno=lineno):
            name = ref.group(1)
            if name in values:
                return values[name]
            if name in environ:
                return environ[name]
            raise EnvFileError(f"line {lineno}: ${{{name}}} is not defined above or in the environment")

        values[key] = environ[key] if key in environ else _REF.sub(_expand, value)
    return values


def load(path, environ=None):
    return loads(path.read_text(encoding="utf-8"), environ)
