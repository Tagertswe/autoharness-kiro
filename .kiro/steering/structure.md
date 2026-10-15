---
inclusion: always
---

# Structure

```
src/autoharness/
  config.py              every knob (AUTOHARNESS_* env), one place
  host/                  host seam: __init__ (host name, root dir, skill-name rule),
                         claude.py (output protocol), kiro.py (payload normalise + emit)
  hook/
    dispatch.py          entry point for every hook; routes to kiro_dispatch when AUTOHARNESS_HOST=kiro
    kiro_dispatch.py     Kiro events: agentSpawn, userPromptSubmit, preToolUse, stop; per-session
                         job marks (loop guard); auto publish; cost accounting
    reflect.py           instruction texts handed to the agent (reflect, curate, publish, notes, status, usage)
    promoter.py          deterministic validate + land of staged intents (shared with Claude)
    on_*.py, spawn.py, capture.py   upstream Claude path (spawn/capture unused on Kiro)
  lib/
    layer.py             global/project roots, skills dirs, DISABLE_PROJECT relocation
    publish.py           shared repo: git status summary, MR records, per-session announcements
    usage.py             dated cost log ~/.kiro/autoharness/usage/YYYY-MM-DD.jsonl
    envfile.py           .env reader with ${KEY} expansion
    minitoml.py          tomllib fallback for 3.10
    others               upstream: sidecar, ledger, lifecycle, counters, validate, redact, ...
  stage_skill/server.py  the MCP server with the stage_skill tool
  install.py             writes ~/.kiro/agents/{autoharness,autoharness-curator}.json, --into, --uninstall
kiro/install.py          wrapper: python3 kiro/install.py
kiro/probe/              diagnostic that measured Kiro's hook behaviour; re-run after Kiro updates
agents/, skills/, hooks/, .claude-plugin/   upstream Claude Code plugin files
docs/kiro.md, docs/kiro-port-spec.md        Kiro guide and spec
tests/                   pytest; Kiro-specific: test_host, test_kiro_*, test_publish, test_usage,
                         test_envfile, test_minitoml
```

Runtime state never goes into this repository: global state is `~/.kiro/autoharness/`, learned
skills are `~/.kiro/skills/` or `AUTOHARNESS_GLOBAL_SKILLS_DIR`.
