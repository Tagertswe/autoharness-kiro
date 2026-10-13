"""Stdlib-only reader for the TOML subset redaction_rules.toml uses, for Python 3.10 (no tomllib).

Ubuntu 22.04 ships Python 3.10 and the zero-dependency rule forbids tomli, so redact falls back to
this when tomllib is missing. Supported, and nothing else: comments, blank lines, `[[table]]` array
headers, and `key = value` where value is a basic "string", a literal 'string' or a one-line
'''literal'''. Anything outside that raises instead of being guessed at — a misread redaction rule
fails open, so an unknown construct must stop the load, not degrade it.
"""
import json
import re

_HEADER = re.compile(r"^\[\[([A-Za-z0-9_-]+)\]\]$")
_KEY = re.compile(r"^([A-Za-z0-9_-]+)\s*=\s*(.+)$")


class TOMLDecodeError(ValueError):
    pass


def _value(raw, lineno):
    for quote in ("'''", "'"):
        if raw.startswith(quote):
            end = raw.find(quote, len(quote))
            if end == -1:
                raise TOMLDecodeError(f"line {lineno}: unterminated or multi-line literal string")
            rest = raw[end + len(quote):].strip()
            if rest and not rest.startswith("#"):
                raise TOMLDecodeError(f"line {lineno}: unexpected text after string: {rest!r}")
            return raw[len(quote):end]
    if raw.startswith('"') and not raw.startswith('"""'):
        try:
            value, end = json.JSONDecoder().raw_decode(raw)  # basic strings share JSON's common escapes
        except ValueError as exc:
            raise TOMLDecodeError(f"line {lineno}: bad basic string: {exc}") from exc
        rest = raw[end:].strip()
        if rest and not rest.startswith("#"):
            raise TOMLDecodeError(f"line {lineno}: unexpected text after string: {rest!r}")
        return value
    raise TOMLDecodeError(f"line {lineno}: unsupported value (only strings are read): {raw!r}")


def loads(text):
    data, current = {}, None
    for lineno, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        header = _HEADER.match(line)
        if header:
            current = {}
            data.setdefault(header.group(1), []).append(current)
            continue
        pair = _KEY.match(line)
        if not pair or current is None:
            raise TOMLDecodeError(f"line {lineno}: unsupported syntax: {line!r}")
        key, raw = pair.groups()
        if key in current:
            raise TOMLDecodeError(f"line {lineno}: duplicate key {key!r}")
        current[key] = _value(raw.strip(), lineno)
    return data
