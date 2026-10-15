---
inclusion: always
---

# Product: autoharness for Kiro CLI

This repository is a fork of `tigerless-labs/autoharness`, a self-learning skill harness. Upstream
runs as a Claude Code plugin. This fork adds a second host, Kiro CLI 2.x, so the same pipeline runs
locally on a locked-down work machine with no API key and no plugin publishing.

What it does in a Kiro session: hooks count the work; at the end of a turn the agent is asked for
one more turn to reflect and propose skills through the `stage_skill` MCP tool; a deterministic
promoter (no model) validates and writes them as `SKILL.md` files; optionally the learned-skills
folder sits in a clone of a shared skills repository and the agent opens merge requests for it.

Read before changing anything:

- `docs/kiro.md`: user guide, settings, storage locations, first-run checklist.
- `docs/kiro-port-spec.md`: the design, probe results (section 7), as-built deviations (section 12)
  and the handoff list of what is open (section 13).
- `README.md`, section "Kiro CLI setup": install and the flow diagram.

Goals, in order: nothing leaves the sealed work environment; never touch skills or agent config
the user wrote; keep upstream's Claude Code path working and mergeable; keep it boring to install.
