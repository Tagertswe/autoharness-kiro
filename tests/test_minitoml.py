"""lib/minitoml: the stdlib-only stand-in for tomllib on Python 3.10. A misread redaction rule fails
open, so the contract is "identical to tomllib on the shipped rules, loud on anything else"."""
import pytest

from autoharness import config
from autoharness.lib import minitoml, redact

STRINGS = ('[[a]]\nname = "x\\ty"  # basic string, escape decoded\n'
           "p = 'lit\\d'\nq = '''tri'ple\\s'''   # literal, backslash kept\n")


def test_shipped_rules_parse_identically_to_tomllib():
    tomllib = pytest.importorskip("tomllib")  # the comparison needs the real parser (3.11+)
    text = config.REDACTION_RULES.read_text()
    assert minitoml.loads(text) == tomllib.loads(text)
    assert minitoml.loads(STRINGS) == tomllib.loads(STRINGS)


def test_shipped_rules_load_into_named_patterns():
    data = minitoml.loads(config.REDACTION_RULES.read_text())
    assert set(data) == {"secret", "pii"}
    assert all(set(rule) <= {"name", "pattern", "validate"} and rule["name"] and rule["pattern"]
               for rules in data.values() for rule in rules)
    assert {"name": "credit_card", "validate": "luhn"}.items() <= data["pii"][-1].items()


def test_string_forms_and_trailing_comments():
    assert minitoml.loads(STRINGS) == {"a": [{"name": "x\ty", "p": "lit\\d", "q": "tri'ple\\s"}]}


@pytest.mark.parametrize("text", [
    "name = 'outside any table'\n",
    "[table]\nname = 'x'\n",
    "[[a]]\nn = 5\n",
    "[[a]]\nn = true\n",
    "[[a]]\nn = ['x']\n",
    "[[a]]\np = '''first\nsecond'''\n",
    "[[a]]\nn = 'x' trailing\n",
    "[[a]]\nn = 'x'\nn = 'y'\n",
    '[[a]]\nn = """x"""\n',
])
def test_anything_outside_the_subset_raises(text):
    with pytest.raises(minitoml.TOMLDecodeError):
        minitoml.loads(text)


def test_redact_works_through_the_fallback(monkeypatch):
    monkeypatch.setattr(redact, "tomllib", minitoml)
    redact._rules.cache_clear()
    try:
        assert redact.redact("mail a.b@example.com") == "mail [REDACTED:pii:email]"
        assert redact.secret_hits("key AKIAABCDEFGHIJKLMNOP") == ["aws_access_key_id"]
    finally:
        redact._rules.cache_clear()
