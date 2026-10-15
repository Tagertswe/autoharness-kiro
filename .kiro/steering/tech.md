---
inclusion: always
---

# Tech and working rules

## Stack

- Python 3.10 is the floor and the target (the work machine runs Ubuntu 22.04's 3.10.12).
  `.python-version`, `ruff.toml` (`target-version = "py310"`) and CI all pin it. Do not use
  3.11+ features: no `tomllib` without the `lib/minitoml.py` fallback, no `except*`, no `Self`.
- Standard library only. No third-party runtime dependencies; pytest and ruff are dev tools only.
- Kiro CLI 2.x (measured on 2.29.1). Hook payload facts are in `docs/kiro-port-spec.md` section 3.

## Commands

```
python3 -m pytest -q                 # whole suite; pytest.ini sets pythonpath=src
python3 -m pytest -q tests/test_kiro_dispatch.py
ruff check .
python3 tools/check_doc_links.py
python3 kiro/install.py --help
```

## Rules

- **Never commit work-environment identifiers.** Internal repository names, hostnames, usernames,
  employer names and work filesystem paths go only in the gitignored `.env` at the repo root.
  Committed files, tests, commit messages and MR text use placeholders such as
  `/path/to/shared-skills-repo`. `.env.example` holds placeholders only. The fork is public on
  GitHub. Before every commit, grep the staged diff for such names.
- **Nothing may send data out.** autoharness makes no network calls. The only git commands it runs
  are `git status` and `git rev-parse`; commits, pushes and merge requests are done by the agent
  under the user's approval prompts. Do not add telemetry, update checks or HTTP calls.
  `AUTOHARNESS_NOTIFY_CMD` stays unset in the work setup.
- **A hook must never break a session.** Every Kiro handler is fail-safe: an exception becomes an
  empty verdict and exit 0. Keep it that way.
- **Never modify what autoharness did not create:** skills without `created_by: agent` in their
  sidecar, agent files it did not write, a `.gitignore` it did not write.
- **Leave upstream's Claude path alone.** `AUTOHARNESS_HOST` defaults to `claude`; Kiro code lives
  in `host/kiro.py`, `hook/kiro_dispatch.py`, `hook/reflect.py`, `install.py`, `lib/publish.py`,
  `lib/usage.py`. Upstream test files stay unchanged so upstream merges stay easy.
- **Tests with every change.** Add or update tests in the matching `tests/test_*.py`, run the whole
  suite, and keep it green on 3.10.
- **Spec before code.** When asked for a spec or design, write the document only; implement only
  after an explicit go-ahead. Record deviations from the spec in its section 12.
- **Git.** Commit or push only when asked. Never commit to `main`; branches are named
  `dev/tagertswe/<feature>`. Never put a chat or session link in a commit message.
