# Spec: porting autoharness to Kiro CLI 2.x

Status: revision 7. Implemented on 2026-10-13, phases 1 to 8, then extended up to 2026-10-15 with
automatic publish, a cost log and safety fixes, with unit tests and a real-process end-to-end test
on Python 3.10 and 3.14. First installed on the work machine with `--into` an existing agent; one
defect was found and fixed (section 7). Not yet exercised through a full learn-and-publish cycle
inside Kiro: the first-run checklist in [kiro.md](kiro.md) covers what is left. Where the code
differs from the design below, section 12 says how; section 13 is the handoff list.
Written 2026-10-12 against upstream `tigerless-labs/autoharness` at `3252d25` (plugin version 0.5.3).

Revision history:

- r1: Kiro CLI V3, background reflection through headless `kiro-cli`.
- r2: no API key, so reflection moved into the user's own session.
- r3: the work machine runs Kiro CLI 2.x, not V3. Hooks move into an agent config, the turn
  boundary moves from `Stop` to `userPromptSubmit`, and nothing depends on a session id.
- r4: the probe ran on the work machine, which has kiro-cli 2.29.1, not 2.7. `stop` fires every
  turn and can make the agent take another turn, the payload carries a session id, and local MCP
  servers, `skill://` resources and subagents all work. Reflection moves back to the end of the
  turn, counters are per session again, and the CLI staging route is dropped.
- r5: learned skills live in a folder of a shared skills repository and reach it through merge
  requests (4.10).
- r6: implemented. Section 12 lists the deviations from the design.
- r7: automatic publish, a folder of skills as the publish guide, the cost log, the name-collision
  and foreign-`stage_skill` guards, the installer manifest fix from the first install at work, and
  a handoff section (13) for continuing the work on the work machine.

## 1. Summary

autoharness is roughly 90% host-agnostic Python (stdlib only) and 10% Claude Code glue. The port
replaces the glue and changes where the reflector runs. The rest of the pipeline is untouched.

- **Constraints.** A locked-down work setup on kiro-cli 2.29.1, on an Ubuntu 22.04 base with
  Python 3.10.12. No API key, so no headless runs. Custom hooks, custom agents and local MCP
  servers are allowed.
- **Packaging.** No plugin and nothing published. Installation is a local script that writes one
  agent config (plus a curator agent) under `~/.kiro/agents/` or `.kiro/agents/`, pointing at this
  checkout.
- **How it attaches.** In CLI 2.x, hooks exist only inside a custom agent's config. autoharness
  therefore ships as an agent, `autoharness`, that behaves like the default agent with four hooks
  added. You either make it your default agent or have the installer add the hooks to an agent you
  already use.
- **Learning loop.** Hooks count tool calls. When enough work has accumulated, the `stop` hook at
  the end of a turn asks the agent for one more turn with a reflection instruction. The agent
  distills the conversation it is already in, stages intents through the `stage_skill` MCP tool,
  and the deterministic promoter lands them.
- **Prior art.** No port of autoharness to Kiro exists. See section 10.

Every Kiro capability this design depends on was observed working on the work machine by the probe
in `kiro/probe/`. Section 7 has the results and the short list of things still unchecked.

## 2. What the repo is today

```
.claude-plugin/     plugin.json + marketplace.json          Claude packaging
hooks/hooks.json    4 events -> one dispatcher command      Claude hook wiring
.mcp.json           stage_skill MCP server                  Claude MCP wiring
agents/             reflector.md, curator.md                Claude subagent definitions
skills/learn/       /learn skill                            Claude skill
src/autoharness/
  hook/             dispatch, capture, spawn, promoter, on_*   event handling
  lib/              layer, counters, sidecar, ledger, lifecycle, validate, redact, ...
  stage_skill/      MCP server: the reflector's only write face
tests/              44 test files, pytest, no third-party runtime deps
```

Upstream pipeline:

1. **Capture.** `PreToolUse` counts tool calls per session. `Stop` (once per turn) bumps the request
   counters and, once the tool-call count passes `AUTOHARNESS_REFLECT_EVERY_N`, launches a detached
   reflection.
2. **Reflect.** `hook/spawn.py` slices the host transcript, redacts it, and pipes it into a child
   `claude -p` session running the `reflector` agent. The reflector can only call `stage_skill`,
   which appends an intent to a queue.
3. **Promote.** `hook/promoter.py` is the only writer. It validates each intent and atomically
   renames the result into the live skill directory.
4. **Surface.** `SessionStart` runs the lifecycle pass (archiving) and injects an index of
   self-authored skills into the session context.
5. **Curate.** Every `AUTOHARNESS_CONSOLIDATE_EVERY_N` requests a `curator` child session merges
   narrow skills into umbrellas.

Steps 2 and 5 need a child process, and therefore a key on Kiro. Steps 1, 3 and 4 are plain Python
in hooks. Upstream already has an in-session path: the `/learn` skill makes the main agent stage
intents into a queue named `interactive`, which a later hook drains (`dispatch.py:126`). The Kiro
design makes that path the automatic one.

### Host coupling inventory

| Where | Coupling |
|---|---|
| `lib/layer.py:61-62` | Roots are `~/.claude` and `<repo>/.claude`. Skills, archive and state all derive from here. |
| `hook/dispatch.py` | Event names; payload fields `session_id`, `transcript_path`, `tool_name`, `tool_input`, `agent_type`, `cwd`; tool names `Skill`, `Read`, `Write`, `Edit`, `MultiEdit`, `NotebookEdit`; output protocol `hookSpecificOutput` JSON. |
| `hook/on_skill_call.py:32` | Read path is `tool_input.file_path`; skill identity is `tool_input.skill` and similar. |
| `lib/counters.py`, `hook/on_stop.py` | The tool-call counter is keyed by `session_id`. |
| `hook/capture.py`, `hook/spawn.py` | Transcript slicing and the `claude -p` child launch. Not used on Kiro. |
| `agents/*.md` | Claude frontmatter; tool allowlist `Read, Grep, Glob, mcp__plugin_autoharness_stage_skill__stage_skill`. |
| `hooks/hooks.json`, `.mcp.json` | `${CLAUDE_PLUGIN_ROOT}` path expansion. |
| `lib/validate.py`, `lib/format_spec.md` | Skill name and frontmatter rules follow Claude's skill format. |

## 3. Concept mapping for Kiro CLI 2.x

"Probe" in the last column means observed on the work machine with kiro-cli 2.29.1. "Docs" means
taken from Kiro's documentation and not exercised by the probe.

| Claude Code | Kiro CLI 2.29.1 | Source |
|---|---|---|
| Plugin + marketplace | None. An agent config file. | Docs |
| `hooks/hooks.json` | A `hooks` object inside an agent config, keyed by camelCase event: `{"hooks":{"agentSpawn":[{"command":"..."}], "stop":[{"command":"..."}]}}`. Standalone `.kiro/hooks/*.json` files are not read by CLI 2.x. | Probe |
| Hooks apply to every session | Hooks apply only when the session runs that agent. `kiro-cli settings chat.defaultAgent <name>` or `kiro-cli agent set-default <name>` makes an agent the default. | Docs |
| `SessionStart` | `agentSpawn`. Stdout is added to context. | Probe |
| `UserPromptSubmit` | `userPromptSubmit`. Stdout is added to that turn's context, fresh each turn. | Probe |
| `PreToolUse` | `preToolUse`. Exit code 2 blocks the call and the agent sees stderr. A blocked call gets no `postToolUse`. | Probe |
| `Stop` | `stop`. Fires at the end of every turn. Stdout `{"decision":"block","reason":"..."}` makes the agent take another turn acting on the reason. `stop` then fires again for that extra turn. Plain text on stdout does not reach the model. | Probe |
| `SessionEnd` | Does not exist. | Docs |
| Payload | Every event: `hook_event_name`, `cwd`, `session_id` (36 characters). `userPromptSubmit` adds `prompt`. Tool events add `tool_name`, `tool_input`, `tool_tags`; `postToolUse` adds `tool_response`. `stop` adds `assistant_response`. No transcript path. `KIRO_SESSION_ID` is also in the hook's environment. | Probe |
| `cwd` | The directory Kiro was started in, which need not be the repository root. | Probe |
| Tool names | `read`, `write`, `shell`, `subagent` in the TUI. An MCP tool is `@<server>/<tool>`. The docs give `fs_read` / `fs_write` / `execute_bash` for non-interactive runs, which this port does not use. | Probe |
| Tool inputs | `write`: `command`, `content`, `path`. `read`: `operations`, a list of `{mode, path}`. `shell`: `command`. `subagent`: `stages`, `task`. Each also has `__tool_use_purpose`. | Probe |
| Tool tags | `tool_tags` classifies the call: `["read", "@builtin"]` for a built-in, `["@ahprobe/probe_echo", "@ahprobe", "@mcp"]` for an MCP tool. | Probe |
| Tool `Skill` | No tool call. Kiro puts each skill's name, description and file path in context and the agent opens `SKILL.md` with `read`. | Probe |
| `agents/*.md` | `~/.kiro/agents/<name>.json` (global) or `.kiro/agents/<name>.json` (project). Fields used here: `prompt`, `tools`, `toolsSettings`, `resources`, `hooks`, `mcpServers`, `includeMcpJson`. | Probe |
| `.mcp.json` | `mcpServers` inline in the agent config. A local stdio server starts and its tool can be called. | Probe |
| `.claude/skills/<name>/SKILL.md` | `skill://<glob>` entries in the agent's `resources`. An unknown frontmatter key (`category`) is tolerated. | Probe |
| Subagents | The `subagent` tool, restricted by `toolsSettings.subagent.availableAgents`. Delegating to a named agent works. | Probe |

## 4. Target design

### 4.1 Four hooks, the turn boundary on `stop`

| Hook | Does |
|---|---|
| `agentSpawn` | Lifecycle pass (archive what should be archived), print the last-run summary and the project skill index. |
| `preToolUse` | Count the tool call for this session. Count a read into a managed skill directory as `use` or `view`. Deny direct writes into managed skill directories and the state directory (4.4). |
| `stop` | The turn boundary, as upstream. Count one request, drain the intent queue, then decide whether to ask for another turn: a reflection, a curation, or nothing. |
| `userPromptSubmit` | Recognise the `autoharness learn` prompt, and clear a stale "reflecting" mark (4.2). Prints nothing otherwise. |

`postToolUse` fires too and is not used.

### 4.2 Reflection at the end of the turn

```
you send a prompt; the agent works and finishes its answer
  -> stop hook:
       count the request; drain the queue
       work counter below threshold -> print nothing; the turn ends
       threshold crossed            -> reset the counter, mark the session "reflecting",
                                       print {"decision":"block","reason":"<reflection instruction>"}
  -> the agent takes one more turn: reflects, stages intents, says one line
  -> stop hook fires again:
       the session is marked "reflecting" -> clear the mark, drain the queue, print nothing
       the promoter validates and lands the skills
```

The agent that did the work does the reflecting. It already holds the whole conversation, so no
transcript has to be found or parsed, and no separate process or key is involved. This is
upstream's shape with the child session replaced by one extra turn in the same session.

**The instruction.** Built by the hook and carried in the `reason` field. It is upstream's
fork-carrier instruction (`spawn.py:FORK_INSTRUCTION`, written for a reflector that can see the real
conversation), adapted to say: reflect on the work so far, stage through `stage_skill`, report in
one line. It carries the current skill index and the path to `lib/format_spec.md` for the agent to
read. If nothing is worth keeping, the agent stages nothing.

**Loop guard.** The probe saw `stop` fire again after the continued turn (seven `stop` calls for
six prompts), and the payload has no field saying a given `stop` follows a block. The hook
therefore keeps its own mark, keyed by `session_id`. A `stop` that finds the mark never blocks. The
mark is also cleared at `userPromptSubmit` and `agentSpawn`, so it cannot outlive the turn it
belongs to.

**Counters.** The payload has a `session_id`, so the tool-call counter is keyed per session exactly
as upstream. The per-project counter that revision 3 introduced is gone.

**On demand.** The `userPromptSubmit` hook receives the prompt text. A prompt that starts with
`autoharness learn` makes the hook print the reflection instruction for that turn, whatever the
counter says. This replaces upstream's `/learn` skill.

**Fallback mode.** Revision 3's design, where the instruction is injected by `userPromptSubmit` on
the prompt after the threshold is crossed, stays available as `AUTOHARNESS_REFLECT_MODE=prompt`. It
exists because there is a public report of `stop` never firing on another 2.x build
(kirodotdev/Kiro#10344), and a Kiro update at work could land on such a build.

**What changes for the user compared with upstream.**

- Reflection is visible. At the default cadence of 50 tool calls, an answer is occasionally
  followed by a short reflection step.
- It spends credits and context from the working session, on whatever model the session uses. In
  exchange the reflector sees the real conversation instead of a clipped, redacted window.
- Work in a session that never reaches the threshold is only learned if you say
  `autoharness learn` before quitting.

New knob: `AUTOHARNESS_REFLECT_MODE` = `stop` (default on Kiro), `prompt` or `off`. `off` keeps
counters, index and lifecycle running and leaves learning to `autoharness learn`.

### 4.3 Curation at the end of the turn

The curator reads the library, not the conversation.

- Trigger: unchanged cadence (`AUTOHARNESS_CONSOLIDATE_EVERY_N` requests). The `stop` hook takes the
  pre-run snapshot of both skill trees itself (`spawn._snapshot_skills`, moved out of the spawn
  path), then blocks with an instruction to delegate to the `autoharness-curator` agent through the
  `subagent` tool, passing the agent-authored skill index.
- The curator agent's config allows only `read` and the `stage_skill` server.
- A `stop` asks for at most one job. If reflection and curation fall due together, reflection goes
  first and curation waits for the next turn.
- Whether the parent's hooks also run for tool calls made inside a subagent is unchecked (section
  7). If they do, the curator's reads must not count as skill use; the "pending job" mark already
  covers that.

### 4.4 Guarding the skill tree

Upstream stops the reflector writing files by denying its write tools outright. The Kiro port
guards the destination instead of the actor. A guard keyed on "this session is reflecting" would
block your real work if the mark were ever wrong, and the destination guard has no such state.

- `preToolUse` denies (exit 2) any `write` call whose `path` resolves inside an agent-created
  skill directory, the archive, or `.kiro/autoharness/`. The message tells the agent to stage an
  intent instead. The probe confirmed both halves: the call is blocked and the agent reads the
  message.
- Skills you wrote yourself are not covered and stay editable as usual; autoharness never manages
  those anyway.
- This is a guard against the agent taking a shortcut, not a security boundary: a shell command
  can still write anywhere. The promoter remains the authority. It validates every intent, and a
  skill directory changed behind its back simply has no ledger entry for the change.

One consequence: asking Kiro to hand-edit a learned skill is refused. Edit the file yourself, or
ask the agent to stage an update.

### 4.5 Staging route: MCP

The probe started a local stdio MCP server from the agent config and called its tool, so the port
uses upstream's route unchanged: `stage_skill` declared under `mcpServers`, seen by hooks as
`@stage_skill/stage_skill`.

Revision 3 also planned a shell-based CLI route in case local MCP servers were restricted. It is
not built. If policy changes, it is a thin entry point over the same `stage_skill.server.stage()`
function and can be added then.

### 4.6 Recall through Kiro's skill loader

The probe showed how `skill://` resources behave on this build: Kiro puts each skill's name,
description and file path in context, and the agent opens `SKILL.md` with the ordinary `read` tool
when a description matches. That is the recall behaviour autoharness wants, so the agent config
lists the skill directories as `skill://` resources.

- The glob is `*/SKILL.md`, not `**/SKILL.md`: archived skills live at `skills/.archive/<name>/`,
  and a recursive glob would put them back into recall. Whether `*` skips the dot-directory on its
  own is unchecked, so the lifecycle test in section 7 covers it.
- The probe used an absolute path. The installer writes the global skills directory as an absolute
  path. The project directory has to be relative (`skill://.kiro/skills/*/SKILL.md`), which is
  unchecked. Until it is, the `agentSpawn` hook also prints the project skill index with a hint to
  read `<skills dir>/<name>/SKILL.md`; upstream already has this hint for worktree sessions
  (`on_session_start._read_hint`).

**Skills autoharness did not create.** A developer's machine already has skills: installed ones in
`~/.kiro/skills/`, and hand-written ones in the shared repository. The port treats them as upstream
does, read but never changed.

- Recall: Kiro's docs say the built-in default agent loads skills automatically and a custom agent
  only loads what its `resources` list. The installer therefore always lists
  `~/.kiro/skills/*/SKILL.md` (as an absolute path) next to the learned-skills folder, so switching
  to the `autoharness` agent does not cost you the skills you already had. `--into` leaves an
  existing agent's own `resources` as they are.
- Reflection: the agent compares a new lesson against every skill it has loaded, so it does not
  re-learn what an installed skill already says. Upstream gives its reflector an index of both
  layers for this; on Kiro the loaded skill metadata is already in the agent's context.
- Changes: the promoter rejects any intent against a skill whose sidecar does not say
  `created_by: agent`. Installed and hand-written skills are never patched, merged, archived or
  counted. The curator is only shown agent-created skills.
- When the learned-skills folder is redirected (4.10), `~/.kiro/skills/` is no longer the global
  layer's directory. It stays in `resources` for recall and is otherwise untouched.

Usage counting follows from this. A `read` of `<skill>/SKILL.md` counts as `use` and a read of any
other file in the skill directory counts as `view`. Reads made while a reflection or curation job
is pending are not counted, since comparing against the library is not using it. One `read` call
carries a list of operations, each with its own `path`, so a single call can count several skills.

### 4.7 Host adapter seam

Add one package, `src/autoharness/host/`, and route every item in the coupling inventory through
it. The core modules stop naming a host.

```
src/autoharness/host/
  __init__.py     current() -> adapter, selected by AUTOHARNESS_HOST (default "kiro" in this fork)
  base.py         the interface below
  claude.py       today's behaviour, moved verbatim
  kiro.py         the new behaviour
```

| Member | Purpose |
|---|---|
| `root_dirname` | `.claude` or `.kiro`. Consumed by `lib/layer.py`. |
| `normalize(event) -> event` | Map the host payload to the internal shape: canonical event name, session id, `cwd`, `prompt`, tool kind (`read`, `write`, `shell`, `subagent`, `mcp`, `other`) from `tool_tags`, target paths. |
| `emit_context(text)`, `emit_deny(reason)`, `emit_continue(instruction)` | Output protocol. On Kiro: plain stdout, exit 2 with stderr, and the `stop` block JSON. |
| `turn_boundary` | Which event closes a turn: `Stop` on Claude, `stop` on Kiro. |
| `reflect_modes` | Claude: `headless`. Kiro: `stop`, `prompt`. |

Recommendation: keep the Claude adapter rather than deleting the Claude path. Upstream is active
(235 merged PRs), and an adapter keeps the fork's diff confined to `host/`, the dispatcher and the
install files, so upstream merges stay cheap. It also keeps the existing 44 test files passing
unchanged, which is the regression net for the port. The same seam is where V3 support goes later
(section 9).

### 4.8 Files the port adds

```
kiro/
  agents/autoharness.json.tmpl           default-like agent + the four hooks
  agents/autoharness-curator.json.tmpl   subagent for the curation pass
  agents/prompts/curator.md              body of agents/curator.md, tool names reworded
src/autoharness/install.py               python3 -m autoharness.install [--scope global|workspace]
                                           [--into <existing agent>]
                                           [--env K=V] [--uninstall]
src/autoharness/hook/reflect.py          builds the reflection and curation instructions
src/autoharness/host/...
```

The main agent as installed:

```json
{
  "name": "autoharness",
  "description": "Default agent plus autoharness self-learning hooks.",
  "tools": ["*"],
  "includeMcpJson": true,
  "mcpServers": {
    "stage_skill": { "command": "python3", "args": ["-m", "autoharness.stage_skill.server"],
                     "env": { "PYTHONPATH": "/abs/path/src" } }
  },
  "resources": ["skill://.kiro/skills/*/SKILL.md", "skill:///home/you/.kiro/skills/*/SKILL.md"],
  "toolsSettings": { "subagent": { "availableAgents": ["autoharness-curator"] } },
  "hooks": {
    "agentSpawn":       [ { "command": "AUTOHARNESS_HOST=kiro PYTHONPATH=/abs/path/src python3 -m autoharness.hook.dispatch" } ],
    "userPromptSubmit": [ { "command": "...same..." } ],
    "preToolUse":       [ { "command": "...same..." } ],
    "stop":             [ { "command": "...same..." } ]
  }
}
```

It sets no `prompt` and no tool restrictions, so it behaves like the built-in default agent with
hooks added. `allowedTools` is deliberately left out so your normal approval prompts are unchanged.
The probe's agents used the same fields, loaded on 2.29.1, and the main one passed
`kiro-cli agent validate --path <file>`.

If you already work in a custom agent, `--into <agent>` merges only the `hooks` entries, the
`stage_skill` server and the curator permission into that agent's file, after backing it up.

### 4.9 Per-component changes

**Roots (`lib/layer.py`).** `default_root` reads `host.current().root_dirname`. Skills land in
`.kiro/skills/`, state in `.kiro/autoharness/`, archive in `.kiro/skills/.archive/`. The worktree
remapping stays.

**Dispatch (`hook/dispatch.py`).** Call `host.normalize(event)` first, then route on canonical
event name and tool kind instead of literal names. The per-turn work attached to `Stop` (bump
request counters, drain the interactive queue, check the threshold, check the curator cadence) is
factored into one function that the adapter's `turn_boundary` event calls. On Kiro:

- `agentSpawn` and an injecting `userPromptSubmit` print plain text on stdout, exit 0.
- A `stop` that wants another turn prints the block JSON on stdout, exit 0.
- A denied write exits 2 with the reason on stderr. `main()` currently always returns 0, so the
  deny path needs its own exit code.
- Every other outcome is exit 0 with nothing on stdout. Hook stdout becomes agent context on Kiro,
  so the dispatcher must never print diagnostics there.
- There is no reflector identity to detect and no child session, so the `agent_type` and
  `AUTOHARNESS_CHILD_SESSION` branches are inert on Kiro.
- `cwd` is where Kiro was started. `lib/layer.py` deliberately takes the session cwd as the
  project's identity and does not walk up to the repository root (only linked worktrees are
  remapped), so a session started in a subdirectory learns into that subdirectory's
  `.kiro/skills/`. The port keeps upstream's rule; start Kiro from the repository root.

**Counters (`lib/counters.py`).** No change. The Kiro payload has a `session_id`.

**Skill format (`lib/validate.py`, `lib/format_spec.md`).** The Agent Skills format requires `name`
to match the folder name and to be lowercase letters, digits and hyphens, at most 64 characters.
`layer._SAFE_NAME` also allows uppercase, dots and underscores; on Kiro the promoter rejects names
outside the stricter set. autoharness also writes a `category` frontmatter key. It stays at top
level: the probe's sample skill carried one and loaded.

**Python 3.10 (`lib/redact.py`, `hook/dispatch.py`).** Upstream requires 3.11, and Ubuntu 22.04
ships 3.10. The only thing in `src/` that needs 3.11 is `tomllib`, used once to load
`lib/redaction_rules.toml`. Checked on 2026-10-12 in a Python 3.10 container: with a TOML parser
supplied and the version guard lowered, 543 of 544 tests pass, and the one failure is the test
that asserts the guard's message names 3.11. The port therefore lowers the floor to 3.10:

- `lib/redact.py` uses `tomllib` when it exists and otherwise a small stdlib-only reader for the
  subset the rules file uses (`[[table]]` headers, `key = "string"`, `key = '''literal'''`,
  comments). It rejects anything outside that subset instead of guessing, because a misread
  redaction rule fails open. A test loads the shipped rules file through both readers and asserts
  identical results.
- `MIN_PYTHON` in `hook/dispatch.py` becomes `(3, 10)`, with the README badge, the guard's test and
  the CI matrix updated to match and 3.10 added to the matrix.
- No third-party dependency is added. `tomli` would do the same job, but upstream's zero-dependency
  rule exists so the hooks work with a bare interpreter, and `pip` may not be available at work.

**Promoter, ledger, sidecar, lifecycle, notify.** No change.

**Configuration.** `AUTOHARNESS_*` variables are unchanged apart from the new
`AUTOHARNESS_REFLECT_MODE`. They are set in the shell environment, or baked into the hook command
prefix with the installer's `--env`. `AUTOHARNESS_CARRIER` and `AUTOHARNESS_DIGEST_EXCHANGES` have no
effect on Kiro.

### 4.10 Shared skills repository and merge requests

At work, Kiro skills are collected in an internal repository. For the trial, self-learned skills
get their own folder there, `self-learning-skills/`, and changes reach the repository through merge
requests opened with a skill that already exists in that repository.

**Site-specific names stay out of this repository.** This fork is public. The shared repository's
name, its path, and the merge-request skill's location are local settings and are never committed.
They live in a `.env` file at the root of this checkout, which `.gitignore` already excludes. A
committed `.env.example` documents the keys with placeholder values.

- The file is plain `KEY=value` lines. `install.py` reads it with a few lines of stdlib code (no
  `python-dotenv`) and writes the values into the hook commands of the agent config under
  `~/.kiro/agents/`, which is outside this repository. The hooks themselves never read `.env`, so
  changing a value means re-running the installer.
- A value may refer to an earlier key as `${KEY}`. That is the only expansion: no command
  substitution, no defaults, and an unknown key is an error, not an empty string.
- Values already set in the shell environment win over the file.
- Docs, tests, fixtures and commit messages use placeholders such as `/path/to/shared-skills-repo`.
  A test asserts that `.env` is ignored by git and that `.env.example` contains no absolute path
  outside `/path/to/`.

**Where skills live.** The global layer's skills directory becomes configurable and points into a
clone of the shared repository. There is one copy of each skill: the promoter writes into the
clone's working tree, Kiro recalls from the same files, and git carries them to the repository.

```
AUTOHARNESS_SHARED_SKILLS_REPO=/path/to/shared-skills-repo
AUTOHARNESS_GLOBAL_SKILLS_DIR=${AUTOHARNESS_SHARED_SKILLS_REPO}/self-learning-skills
AUTOHARNESS_DISABLE_PROJECT=1
AUTOHARNESS_PUBLISH_SKILL=${AUTOHARNESS_SHARED_SKILLS_REPO}/<folder of the repository's skills, or one SKILL.md>
```

- `AUTOHARNESS_SHARED_SKILLS_REPO` (new) is the one place the shared repository is named. The other
  values refer to it, and the hooks use it as the directory to run `git status` in.

- `AUTOHARNESS_GLOBAL_SKILLS_DIR` (new) overrides `~/.kiro/skills` for the global layer. Today
  `lib/layer.py` derives the skills directory from the layer root, so this is a second override
  next to the root. State (queue, ledger, counters, snapshots) stays in `~/.kiro/autoharness/`,
  outside the repository.
- `AUTOHARNESS_DISABLE_PROJECT` (new, the mirror of the existing `AUTOHARNESS_DISABLE_GLOBAL`)
  sends every learned skill to the global layer. Nothing is then written into the repositories you
  work in, so learned skills cannot be committed to them by accident.
- The agent config lists `skill://<that folder>/*/SKILL.md` as an absolute path, the form the probe
  confirmed.

**What is kept out of git.** The promoter and the hooks write three things into a skill directory
that should not be in a merge request. The installer adds a `.gitignore` in `self-learning-skills/` for
them:

| Path | Why it stays local |
|---|---|
| `*/.sidecar.json` | Per-skill counters, rewritten on every use. Committing them would dirty the tree constantly. |
| `.archive/` | Archived skills. In git an archived skill shows up as a deletion, which is the right reading. |
| `*/references/evidence-*.md` | Redacted quotes from the sessions a skill was learned from. A shared repository gets the lesson, not the session. |

A consequence of leaving the sidecar out: on a fresh clone the skills have no `created_by` record,
so autoharness on that machine treats them as user-authored and does not manage them. That is
acceptable for a one-person trial and has to be solved before a second person joins.

**Opening a merge request.** Never automatic. A prompt that starts with `autoharness publish`
makes the `userPromptSubmit` hook inject an instruction, the same mechanism as `autoharness learn`:

- The hook runs `git status --porcelain -- self-learning-skills` in the clone and builds a summary from
  it and from the ledger: which skills were created, updated or archived since the last publish,
  one line each.
- The instruction tells the agent to follow the existing merge-request skill, limit the change to
  `self-learning-skills/`, and use the summary as the description.
- The agent does the git work through its shell tool under your normal approval prompts.
  autoharness itself never commits, pushes or calls the GitLab API.
- The `agentSpawn` line reports how many skill changes are not yet in a merge request, so they do
  not pile up unnoticed.

The agent has to be able to see the existing skill, so the installer takes its location from
`AUTOHARNESS_PUBLISH_SKILL` in `.env` and adds it to the agent's `skill://` resources. With `--into`
an agent that already loads the shared repository's skills, nothing needs adding.

**Automatic publish.** `AUTOHARNESS_AUTO_PUBLISH=1` removes the prompt. At a stop where no
reflection or consolidation is due, and at the stop that ends a learning turn, unpublished changes
make the stop hook block once more with the publish instruction. The agent's reply has to end with
a fixed-form note carrying the full merge request link, which is how the session that opened it
shows it. The stop that ends a publish turn never blocks, so the chain is at most work, learn,
publish. One attempt is made per change set (remembered in `~/.kiro/autoharness/auto_publish.json`
and cleared on success), so a publish that fails or is declined is not requested every turn. In
prompt mode the instruction is attached to the next prompt instead. If the tree went clean but no
link was found in the reply, the session is not marked as told and gets the note at its next prompt.

**Cost log (`lib/usage.py`).** Every turn autoharness asks for is accounted for when it ends: one
JSON line in `~/.kiro/autoharness/usage/YYYY-MM-DD.jsonl` with the session, the kind of turn, its
duration, its tool calls, the size of the instruction and of the reply, what landed, and the merge
request link for a publish. A hook is given no credit or token figure on 2.29.1, so credits are not
recorded; a payload field whose name contains credit, usage or token is stored verbatim if a later
build adds one. `autoharness usage` prints 30-day totals per kind.

**Announcing the merge request in other sessions.** Once a merge request exists, every running
session that uses the autoharness agent mentions it once, with the link.

- Capturing the link: the publish instruction tells the agent to end its reply with the merge
  request URL. The `stop` hook for that turn reads `assistant_response`, which the probe showed is in
  the payload, takes the URL from it, and appends a record (time, URL, the summary lines) to
  `~/.kiro/autoharness/publishes.jsonl`. autoharness still makes no network call; it only reads what
  the agent said. If no URL is found, the record is written without one and the note says so.
- Showing it: each record is queued for every session, and a session delivers it at whichever of
  its hooks runs first. Which sessions have delivered which record is tracked by `session_id`.
  - `stop`: when a session finishes the turn it was working on, the hook asks for one more short
    turn whose only instruction is to tell you what was created or revised and give the link. This
    is the same block mechanism as reflection, with the same loop guard. A session that was busy
    when the merge request was opened therefore reports it as soon as its current action ends.
  - `userPromptSubmit`: a session that was idle delivers the note with its next answer.
  - `agentSpawn`: a new session lists the merge requests recorded in the last few days.
- If a reflection or curation is due at the same `stop`, the note is added to that instruction, so
  it is never held back by the one-job rule.
- The note reaches you through the agent. The probe confirmed that hook output reaches the model
  and that a `stop` block produces a visible reply; whether Kiro also shows hook output on screen
  directly is unchecked.
- An idle session cannot be written to. It shows the note when you next use it.
- Nothing is lost if a note is missed. The records are permanent, and a prompt that starts with
  `autoharness status` lists every recorded merge request with its link and date, along with the
  changes not yet published. The installer also prints where the record file is.
- The record holds an internal URL. It lives in the state directory under the home directory, never
  in this repository or the shared one.
- Not covered: whether the merge request was later merged or closed. That would need the GitLab
  API. A local approximation is possible after a `git pull` (the published commit is on the default
  branch) and is left for later.

**Review changes come back through git.** A reviewer's edit lands on the default branch and reaches
your clone with `git pull`. Recall reads the files as they are, so the edited skill is what Kiro
uses from then on. How the promoter treats a later update to a skill whose text changed outside
the ledger has to be checked against `hook/promoter.py` before this phase is built.

**Use a dedicated clone of the shared repository.** The folder Kiro recalls from is whatever branch that clone has checked
out. A clone (or git worktree) used only for this keeps your skills from changing when you switch
branches for other documentation work.

**The write guard still applies.** The `write` tool is denied inside `self-learning-skills/` (4.4). The
merge-request skill is unaffected as long as it works through git commands. Asking the agent to
apply a review comment by editing a skill file is refused; it stages an update instead, or you edit
by hand.

## 5. Running locally, without publishing

Yes. Nothing in this design uses a registry, a marketplace, an API key or any network service of
its own. The only model calls are the ones your normal Kiro session makes.

```
git clone git@github.com:Tagertswe/autoharness-kiro.git ~/repos/autoharness-kiro
cd ~/repos/autoharness-kiro
python3 -m autoharness.install --scope global
kiro-cli settings chat.defaultAgent autoharness     # or: kiro-cli chat --agent autoharness
```

`--scope global` writes:

```
~/.kiro/agents/autoharness.json
~/.kiro/agents/autoharness-curator.json   (+ prompts/)
```

`--scope workspace` writes the same files under `<repo>/.kiro/agents/`. The installer is
idempotent, refuses to overwrite a file it did not write, backs up any agent file it merges into,
and `--uninstall` removes exactly what it installed. It prints the default-agent command rather
than running it, since that changes a personal setting. Learned skills and state are left on disk,
as upstream does.

Updating is `git pull`; the installed files point at the checkout, so there is no cached copy. The
installer only needs re-running when a template changes.

Requirements on the work machine, all met: Python 3.10+ as `python3` (3.10.12 there), permission to
add an agent file and select it, and a local MCP server for the staging route.

For a work setup it is also worth knowing what the tool stores: learned skills under
`.kiro/skills/` contain distilled lessons plus redacted evidence quotes from sessions, and project
skills sit inside the repository, where they can be committed. `AUTOHARNESS_DISABLE_GLOBAL=1` keeps
everything per-project.

## 6. Risks and known gaps

| # | Risk | Effect | Mitigation |
|---|---|---|---|
| R1 | Hooks only run under the `autoharness` agent | Sessions started with another agent learn nothing and count nothing | Default-agent setting; `--into` for an existing agent; the last-run line at session start makes it obvious when the hooks are live |
| R2 | A Kiro update at work changes hook behaviour, in particular `stop` | Reflection stops triggering, or payload fields move | `AUTOHARNESS_REFLECT_MODE=prompt`; all payload handling in `normalize()`; the probe can be re-run after any update |
| R3 | The `stop` block was tested with a one-line reason | A long instruction carrying a skill index might be truncated or handled differently | Check early in phase 4; if it is a problem the reason shrinks to a pointer to a file the hook wrote |
| R4 | The agent may reflect badly or skip it | Nothing staged | An empty queue is a normal outcome and the counter simply starts again; `autoharness learn` forces it |
| R5 | Reflection costs context and credits in the working session | Faster context growth | One-line output, tunable cadence, `AUTOHARNESS_REFLECT_MODE=off`, curator delegated to a subagent |
| R6 | The "reflecting" mark could stick | A due reflection is skipped once | Cleared at `userPromptSubmit` and `agentSpawn`; it never blocks tools, so the cost is one missed trigger |
| R7 | No skill tool call to observe | `use` is approximated by a read of `SKILL.md` | 4.6; lifecycle thresholds may need retuning after real use |
| R8 | Hook stdout becomes context | A stray print pollutes every turn | Stdout only from `agentSpawn`, an injecting `userPromptSubmit` and a blocking `stop`; a test asserts empty stdout otherwise |
| R9 | One `userPromptSubmit` was not logged during the probe (eight prompts sent, seven logged) | An `autoharness learn` prompt could occasionally go unnoticed | Unexplained so far; nothing else depends on this event in `stop` mode, and repeating the prompt recovers |
| R10 | Four Python starts per turn plus one per tool call | Latency | Same order of cost as on Claude today; hooks do no model calls |
| R11 | Work later moves to CLI V3 | Hook location and names change | Section 9 |
| R12 | Learned skills go to a shared repository | Text distilled from your sessions becomes visible to everyone with access | Publishing is manual and goes through a reviewed merge request; evidence files are ignored by git; read the diff before opening it |
| R13 | The skills folder is a git working tree | Switching branches or a conflicting pull changes what Kiro recalls | Dedicated clone; the hooks only ever read git status there |

## 7. Probe results and what is still unchecked

The probe in `kiro/probe/` ran on the work machine on 2026-10-13: kiro-cli 2.29.1, Python 3.10.12,
hooks launched by `kiro-cli-chat`.

| Spike | Question | Result |
|---|---|---|
| S1 | Payload fields; session id; does `userPromptSubmit` stdout reach the model? | Fields as in section 3. `session_id` present on every event. Stdout reaches the model and is fresh each turn. |
| S2 | Do the agent configs load? | Yes, all three. `kiro-cli agent validate --path <file>` printed no errors for the main probe agent. `kiro-cli agent set-default` exists; it was not run. |
| S3 | Local stdio MCP server | Starts and its tool is callable (`@ahprobe/probe_echo`). |
| S4 | Does `stop` fire, and can it hold the turn open? | Fires every turn. The block decision produced a continued turn. Plain stdout did not reach the model. |
| S5 | `skill://` resources | Metadata is put in context; the body is read on demand with `read`. An unknown frontmatter key is tolerated. |
| S6 | Delegation to a named subagent | Works, through the `subagent` tool. |
| S7 | `agentSpawn` stdout; `preToolUse` exit 2 | Stdout reaches the model. Exit 2 blocks the call, the agent sees stderr, and no file was written. |

Still unchecked, none of which blocks starting:

1. Whether a relative `skill://.kiro/skills/*/SKILL.md` glob works, and whether `*` leaves
   `.archive/` out.
2. Whether the parent agent's hooks run for tool calls made inside a subagent. The probe's subagent
   made none.
3. How `stop` handles a long `reason` (R3).
4. Whether Kiro shows hook output to the user or only to the model (4.10).
5. Why one `userPromptSubmit` was not logged (R9).
6. Whether a `stop` hook can block twice in a row. Automatic publish after a learning turn needs
   it; the probe blocked once. If it cannot, the publish is asked for at the end of the next turn
   only when that turn is not itself a learning turn, so set `AUTOHARNESS_REFLECT_MODE=prompt`.
7. Where Kiro keeps per-turn credits. The official docs describe `/usage` (billing and credits,
   remaining credits) and say sessions are saved in a local database under `~/.kiro/`, but not
   whether credits are stored. A third-party cost tracker (CodeBurn) reads per-turn credits from
   `metering_usage` in `~/.kiro/sessions/cli/<session id>.json`, beside a `<session id>.jsonl`; it
   does not say for which versions. If that file exists on 2.29.1, `lib/usage.py` can read the real
   credit figure for each autoharness turn by the session id the hook already has.
8. Where Kiro starts the `stage_skill` MCP server (see the end of section 12).

Observed on the first install at work (2026-10-14), `--into` an existing agent: Kiro printed
`invalid agent config: .autoharness-install.json` at session start. Kiro reads every `*.json` in
an agents directory as an agent config, and the installer's manifest lived there under that name.
Fixed: the manifest is now `.autoharness-install.manifest`, a re-install migrates the old file.

## 8. Implementation phases

1. **Adapter seam, no behaviour change.** Introduce `host/`, move Claude behaviour into
   `host/claude.py`, default `AUTOHARNESS_HOST=claude` for this phase. Existing tests pass untouched.
2. **Python 3.10.** The TOML fallback reader and the lowered floor (4.9). Independent of Kiro and
   small enough to offer upstream on its own.
3. **Kiro adapter, roots, install.** `host/kiro.py`, agent templates, `install.py`, name-rule
   tightening, default host flipped to `kiro`. At the end of this phase counters, the skill
   resources and the lifecycle pass work on Kiro.
4. **End-of-turn reflection.** `hook/reflect.py`, the turn-boundary refactor, the `stop` block and
   its loop guard, `autoharness learn`, the write guard, the `stage_skill` server in the agent.
5. **Prompt-mode fallback.** `AUTOHARNESS_REFLECT_MODE=prompt`.
6. **End-of-turn curation.**
7. **Shared skills repository.** `.env` loading in the installer, `AUTOHARNESS_SHARED_SKILLS_REPO`,
   `AUTOHARNESS_GLOBAL_SKILLS_DIR`,
   `AUTOHARNESS_DISABLE_PROJECT`, the `.gitignore`, `autoharness publish`, the unpublished count (4.10).
8. **Docs.** README rewritten for Kiro; upstream README kept as `docs/claude-code.md`.

### Tests

- TOML fallback: the shipped rules file parses identically through `tomllib` and the fallback;
  unsupported syntax raises. The whole suite runs on 3.10 in CI.
- `host/kiro.py`: `normalize` over payload fixtures recorded by the probe on 2.29.1; output
  protocol and exit codes.
- Turn boundary: each `stop` counts one request and drains the queue; threshold crossed blocks once
  and resets; the `stop` that follows a block never blocks and clears the mark; `userPromptSubmit`
  and `agentSpawn` clear a stale mark; `autoharness learn` injects regardless of the counter; at
  most one job per `stop`; `AUTOHARNESS_REFLECT_MODE=off` never blocks or injects; `prompt` mode
  injects at `userPromptSubmit` and never blocks.
- Write guard: writes into managed skill, archive and state directories are denied with exit 2;
  writes to user-authored skills and everywhere else pass; path traversal and symlinks resolve
  before the check.
- Usage counters: `SKILL.md` read counts `use`, other files count `view`, nothing counts while an
  instruction is pending.
- `install.py`: idempotence, `--into` merge preserves the rest of the agent file, uninstall removes
  only its own entries, refusal to overwrite foreign files. All against a temporary home directory.
- `.env` loading: `${KEY}` expands to an earlier key, an unknown key raises, the shell environment
  wins, comments and blank lines are skipped.
- Shared skills repository: the global skills directory override is honoured by promoter, index,
  lifecycle and write guard while state stays under the home directory;
  `AUTOHARNESS_DISABLE_PROJECT` routes project intents to global; the generated `.gitignore` keeps
  sidecar, archive and evidence files out of `git status`; `autoharness publish` injects a summary
  that matches a scripted set of changes and injects nothing when the tree is clean; the `stop`
  hook records the URL from `assistant_response` and records without one when none is present; a
  second session announces each record exactly once, at `stop` or at `userPromptSubmit` whichever
  comes first, and nothing after that; a note due together with a reflection is carried in the same
  instruction; `autoharness status` lists all records;
  no code path
  runs a git command that writes. All against a temporary git repository.
- Template validity, in the style of `tests/test_plugin_manifest.py`.
- `test_dispatch*.py`, `test_on_skill_call.py`, `test_counters.py` and `test_layer.py` parametrized
  over both hosts.
- An end-to-end test that drives the dispatcher through a scripted event sequence with a fake
  agent staging an intent, asserting a skill lands under `.kiro/skills/`.
- No frontend, so no Playwright coverage applies.

## 9. Out of scope

**Headless reflection.** Upstream's background model needs `kiro-cli chat --no-interactive`, which
needs `KIRO_API_KEY`, and a way to read the session transcript, which the hook payload does not
provide. Neither is available here. One cheap check is still worth doing once: run
`kiro-cli chat --no-interactive "say hi"` while logged in normally. The docs say a key is required,
but that an existing login is rejected has not been confirmed.

**Kiro CLI V3 and the IDE.** V3 moves hooks to standalone `.kiro/hooks/*.json` files with
PascalCase triggers and adds global hooks and `SessionEnd`. V3 still accepts the 2.x hook form
inside agent configs, so this port should keep working after an upgrade; a V3-native install is a
later addition behind the same adapter. Global hooks would remove R1. kiro-cli 2.29.1 already has
`kiro-cli agent upgrade`, which rewrites agent configs into "the universal format both engines
load", so the V3 engine is present on the work build and the installer's files may need to survive
that command.

## 10. Prior art

Checked on 2026-10-12:

- **Forks.** Upstream lists 759 forks. 617 still resolve; 142 are deleted or renamed, mostly
  random-named accounts. None of the 617 has a `.kiro/` directory on its default branch or a branch
  with "kiro" in its name. The only fork that mentions Kiro anywhere is this one, and only in its
  description. This check would miss a port that kept everything under different paths on a
  differently named branch.
- **GitHub search.** Repository search for "autoharness kiro" returns only awesome-lists and star
  lists that mention both words. Code search for `autoharness` under `.kiro` paths returns nothing.
- **Upstream.** No issue or PR mentions Kiro. The only other-host request is issue #189, "codex
  support?", still open.
- **Related, not a port.** [rommelporras/kiro-config](https://github.com/rommelporras/kiro-config) is
  a Kiro CLI configuration with a hook-driven "self-learning knowledge pipeline": a
  `userPromptSubmit` hook detects user corrections, shell scripts append them to an episodes file,
  and repeated keywords are promoted into a rules file that is injected on every prompt. It learns
  rules by keyword counting rather than having the model write and maintain skills, and has no
  lifecycle or pruning. It is useful as evidence that the hook mechanics this spec relies on work
  in practice on Kiro CLI.

## 11. Open decisions

1. **Keep the Claude path or drop it?** Recommended: keep it behind the adapter (4.7).
2. **Own agent or merge into an existing one?** Decided: `--into` the custom agent already used
   at work. The standalone `autoharness` agent stays for anyone without one.
3. **Install scope.** Recommended: global, so every repository learns.
4. **Default cadence.** Upstream's 50 tool calls was tuned for a background pass. With a visible
   pass a higher default may be more comfortable. Recommended: keep 50 and adjust after real use.
5. **Reflect at the end of the turn or on the next prompt?** Recommended: end of turn (`stop`),
   now that the probe shows it works, with `prompt` kept as a fallback mode.
6. **The merge-request skill.** Partly decided: the shared repository keeps a folder of its own
   skills (conventions, merge-request creation), and `AUTOHARNESS_PUBLISH_SKILL` points at that
   folder in `.env`. Still open: whether the agent picks the right skill from it, and what branch
   naming, target branch and description format those skills expect.
7. **Layout of `self-learning-skills/`.** Decided: a `self-learning-skills` folder next to the
   repository's own skills folder, path in `.env`. Still to confirm with the repository's owners
   that one directory per skill and a `.gitignore` in it are welcome.
8. **Evidence files in the repository?** Recommended: no (4.10).

## 12. As built: deviations from the design

The sections above are the design as reviewed. The implementation differs in these places.

| Design | As built | Why |
|---|---|---|
| `AUTOHARNESS_HOST` defaults to `kiro` in this fork (4.7) | Defaults to `claude`. The installer bakes `AUTOHARNESS_HOST=kiro` into every hook and MCP command it writes. | The 544 upstream tests and the Claude plugin keep working with no environment set, and upstream merges do not touch a flipped default. |
| `host/base.py` with `normalize`, `emit_*`, `turn_boundary`, `reflect_modes`; one dispatcher routing canonical events (4.7, 4.9) | `host/__init__.py` (host name, root directory name, skill-name rule), `host/claude.py` (output protocol), `host/kiro.py` (payload translation, output protocol), and a separate `hook/kiro_dispatch.py` for Kiro's routing. `hook/dispatch.py:dispatch()` is unchanged; `main()` picks the route by host. | Claude's routing stays byte-for-byte what upstream tests. The shared pieces (counters, `on_stop`, promoter, session-start pass, usage counting) are called from both routes. |
| Agent templates as `.json.tmpl` files and a curator prompt file under `kiro/agents/` (4.8) | `src/autoharness/install.py` generates both agent configs. The curator prompt is `agents/curator.md` without its Claude frontmatter, with a short Kiro preface. `kiro/install.py` is a wrapper that needs no `PYTHONPATH`. | Paths are absolute and per machine, so a static template adds nothing, and one curator text cannot drift from upstream's. |
| `toolsSettings.subagent.availableAgents: ["autoharness-curator"]` on the main agent (4.8) | Not set. | It would restrict a default-like agent to that one subagent. If delegation turns out to need it, the curation instruction tells the agent to skip the pass and say so. |
| Hook commands as `VAR=value python3 -m ...` (4.8) | `env VAR=value ... python3 -m autoharness.hook.dispatch`. | Works whether or not Kiro runs hook commands through a shell. |
| The `agentSpawn` index lists project skills only when the relative `skill://` glob is unverified (4.6) | Lists learned skills of every active layer, with the directories to read them from. `AUTOHARNESS_INDEX_SUSPENDED=1` turns it off. | Recall then works even if no `skill://` entry loads. |
| `AUTOHARNESS_DISABLE_PROJECT` sends skills to the global layer (4.10) | Also moves the project layer's state (counters, intent queue, snapshots) to `~/.kiro/autoharness/projects/<hash of the project path>/`. A project-level create is rerouted to global, not refused. If `AUTOHARNESS_DISABLE_GLOBAL` is also set, that one wins. | "Nothing is written into the repositories you work in" has to include `.kiro/autoharness/`. |
| The publish summary is built from `git status` and the ledger (4.10) | From `git status` only: created, updated or removed, per skill. | The status already says everything the description needs. |
| A publish record is written when the agent's reply has a URL, and without one otherwise (4.10) | Written when the reply has a merge-request URL, or when the folder's uncommitted changes went to zero during the turn. Otherwise nothing is recorded. | An `autoharness publish` that failed or had nothing to do must not leave a false record. |
| The unpublished count is "changes not yet in a merge request" (4.10) | It counts uncommitted changes under the folder. Changes committed on a branch but not pushed are not counted. | Only `git status` is ever run. |
| README rewritten for Kiro, upstream README moved (phase 8) | Upstream README kept, with a pointer to the new [kiro.md](kiro.md). | Keeps upstream merges free of conflicts in the README. |
| `tests/test_dispatch*.py` and others parametrized over both hosts (section 8) | Kiro has its own test files (`test_host`, `test_kiro_host`, `test_kiro_dispatch`, `test_publish`, `test_kiro_install`, `test_envfile`, `test_minitoml`). Upstream's test files are unchanged apart from the Python-floor message. | Same reason as the unchanged dispatcher. |

Added during implementation, not in the design:

- The reflection's own tool calls and reads are not counted as work or as skill use, by the same
  per-session mark that guards against a `stop` loop.
- A consolidation that falls due on the same turn as a reflection is remembered in a flag file and
  runs at the next turn boundary. It is skipped when fewer than two agent-created skills exist.
- A payload without a usable `session_id` falls back to `KIRO_SESSION_ID` from the environment, then
  to one shared counter.
- `.gitignore` in the learned-skills folder also excludes `*/.ledger.jsonl` and the sidecar lock file.
- `AUTOHARNESS_PUBLISH_SKILL` may name a folder of skills instead of one `SKILL.md`. The installer
  then loads `<folder>/*/SKILL.md`, and the publish instruction tells the agent to follow the ones
  covering the repository's conventions and merge-request creation.
- Automatic publish, `AUTOHARNESS_AUTO_PUBLISH=1` (described in 4.10). One attempt per change set,
  remembered in `~/.kiro/autoharness/auto_publish.json`; the publish instruction asks for a
  fixed-form note with the full merge request link; a publish without a captured link leaves the
  session unmarked so the note follows at its next prompt.
- A create whose name matches a skill autoharness did not create is rejected (`routing`). Before
  this, upstream's create path overwrote that skill's `SKILL.md`.
- `--into` refuses an agent that already has its own MCP server named `stage_skill`, and leaves the
  file untouched. The installer manifest is `.autoharness-install.manifest`, not `*.json`.
- Cost log, `lib/usage.py` (described in 4.10): one line per autoharness turn in
  `~/.kiro/autoharness/usage/YYYY-MM-DD.jsonl`, and the `autoharness usage` prompt. No credits:
  a hook is given none (section 7, item 7).
- Python 3.10 pinned as the target: `.python-version`, the CI lint job and the README badge.
- `.kiro/steering/` (product, tech, structure) for a Kiro agent working on this repository; the
  rest of `.kiro/` is ignored by git.

One more open point for the first run, in addition to section 7: the `stage_skill` MCP server finds
its queue from its working directory. If Kiro starts MCP servers in a different directory than the
session's, staged skills are queued where the `stop` hook does not look. The end-to-end test assumes
both are the session directory.

## 13. Handoff: state and next steps

For whoever continues this, human or agent, on the work machine. The steering files in
`.kiro/steering/` carry the working rules; this is the state.

**Where it stands.** Everything in sections 4 and 12 is implemented and tested: 684 tests pass on
Python 3.14 and 682 on 3.10 (two skips there are expected: the `tomllib` comparison and a check that
needs a git checkout). It has been installed `--into` an existing agent at work once.

**Setting up a clone at work.**

1. Clone, then `cp .env.example .env` and fill in the paths. `.env` is never committed.
2. `python3 kiro/install.py --into <agent>` (or the standalone agent), then
   `kiro-cli agent validate --path ~/.kiro/agents/<agent>.json`.
3. If pytest is available: `python3 -m pytest -q`.

**Next steps, in order.**

1. Run the first-run checklist in [kiro.md](kiro.md) and record what differs in section 7.
2. Check where Kiro keeps per-turn credits (section 7, item 7). The command in the guide's cost
   section prints only field names. If `~/.kiro/sessions/cli/<session id>.json` holds them, add a
   reader to `lib/usage.py` that fills a `credits` field per autoharness turn, matched by session id
   and turn time, with tests on a recorded fixture with values replaced.
3. Do one real `autoharness publish` against the shared repository and tune
   `reflect.publish_request` to what its merge-request skills expect (open decision 6).
4. Re-run the probe after any Kiro upgrade, and check that `kiro-cli agent upgrade` leaves the
   merged hooks intact (section 9).
5. Calibrate `AUTOHARNESS_REFLECT_EVERY_N` after a week of real use (open decision 4).

## Sources

- Kiro docs: [CLI 2.x reference](https://kiro.dev/docs/cli/2x-reference/) (hook model, exit codes,
  `toolsSettings`), [Hooks](https://kiro.dev/docs/hooks/), [Hook triggers](https://kiro.dev/docs/hooks/types/),
  [Hook actions](https://kiro.dev/docs/hooks/actions/), [V3 hooks migration](https://kiro.dev/docs/cli/v3/hooks-migration/)
  (2.x event names, tool groups), [Agent configuration reference](https://kiro.dev/docs/custom-agents/configuration-reference/),
  [Skills](https://kiro.dev/docs/skills/), [Headless mode](https://kiro.dev/docs/cli/headless/),
  [CLI commands](https://kiro.dev/docs/reference/cli-commands/),
  [Slash commands](https://kiro.dev/docs/reference/slash-commands/) (`/usage`),
  [Session management](https://kiro.dev/docs/cli/chat/session-management/)
- Per-turn credits in Kiro session files, third party: [CodeBurn Kiro provider notes](https://github.com/getagentseal/codeburn/blob/main/docs/providers/kiro.md)
- 2.x probes: [vshulcz/deja-vu#4304](https://github.com/vshulcz/deja-vu/issues/4304) (kiro-cli 2.22.0:
  payloads, stdout injection, default agent), [martin-conur/task-force#221](https://github.com/martin-conur/task-force/issues/221)
  (kiro-cli 2.24.0: hook location, events firing, stdout injection),
  [kirodotdev/Kiro#10344](https://github.com/kirodotdev/Kiro/issues/10344) (stop hook reports)
- 2.x guides (third-party): [Kiro CLI v2 Power User Guide](https://nbodd.github.io/kiro-power-guide/),
  [Kiro CLI Guide: Agent Configuration](https://kiro.kuronetwork.me/en/09-agents/)
