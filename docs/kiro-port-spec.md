# Spec: porting autoharness to Kiro CLI 2.x

Status: draft for review, revision 3. Nothing in this document is implemented.
Written 2026-10-12 against upstream `tigerless-labs/autoharness` at `3252d25` (plugin version 0.5.3).

Revision history:

- r1: Kiro CLI V3, background reflection through headless `kiro-cli`.
- r2: no API key, so reflection moved into the user's own session.
- r3: the work machine runs Kiro CLI 2.x, not V3. Hooks move into an agent config, the turn
  boundary moves from `Stop` to `userPromptSubmit`, and nothing depends on a session id.

## 1. Summary

autoharness is roughly 90% host-agnostic Python (stdlib only) and 10% Claude Code glue. The port
replaces the glue and changes where and when the reflector runs. The rest of the pipeline is
untouched.

- **Constraints.** A locked-down work setup on Kiro CLI 2.x, believed to be around 2.7, on an
  Ubuntu 22.04 base, whose `python3` is 3.10. No API
  key, so no headless runs. Custom hooks and custom agents are allowed. Whether a local MCP server
  is allowed is unknown.
- **Packaging.** No plugin and nothing published. Installation is a local script that writes one
  agent config (plus a curator agent) under `~/.kiro/agents/` or `.kiro/agents/`, pointing at this
  checkout.
- **How it attaches.** In CLI 2.x, hooks exist only inside a custom agent's config. autoharness
  therefore ships as an agent, `autoharness`, that behaves like the default agent with three hooks
  added. You either make it your default agent or have the installer add the hooks to an agent you
  already use.
- **Learning loop.** Hooks count tool calls. When enough work has accumulated, the hook that runs
  on your next prompt adds a reflection instruction to that turn's context. The agent distills the
  conversation it is already in, stages intents, and the deterministic promoter lands them.
- **Prior art.** No port of autoharness to Kiro exists. See section 10.

Nothing here has been verified against a running Kiro; Kiro is not installed on the machine this
was written on. The hook model (hooks embedded in an agent config, five events, exit code 2 blocks
a tool call) is from Kiro's official CLI 2.x reference and applies to the whole 2.x line. The
details that reference leaves out, namely the stdin payload fields and that hook output reaches the
model, come from third-party probes of kiro-cli 2.22 and 2.24, which are considerably newer than
2.7. Section 7 lists what to confirm on the work machine first.

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

| Claude Code | Kiro CLI 2.x | Confidence |
|---|---|---|
| Plugin + marketplace | None. An agent config file. | High |
| `hooks/hooks.json` | A `hooks` object inside an agent config, keyed by camelCase event: `{"hooks":{"agentSpawn":[{"command":"..."}], "preToolUse":[{"matcher":"...","command":"..."}]}}`. Standalone `.kiro/hooks/*.json` files are not read by CLI 2.x. | High (official 2.x reference) |
| Hooks apply to every session | Hooks apply only when the session runs that agent. A file named after the built-in default agent does not override it. `kiro-cli settings chat.defaultAgent <name>` or `kiro-cli agent set-default <name>` makes an agent the default. | High |
| `SessionStart` | `agentSpawn`. Stdout is added to context. | Medium |
| `UserPromptSubmit` | `userPromptSubmit`. Stdout is added to that turn's context; confirmed by probes on 2.22 and 2.24, unconfirmed on 2.7. | Medium |
| `PreToolUse` | `preToolUse`. Exit code 2 blocks the call. | High |
| `Stop` | `stop`. One probe on 2.24 saw it fire once per turn; a bug report on another build says it never fired. Whether it can hold the turn open on 2.x is untested; the docs promise that only for V3. | Low |
| `SessionEnd` | Does not exist. | High |
| Payload | `{"hook_event_name","cwd","prompt"}` for `agentSpawn` and `userPromptSubmit`; `{"hook_event_name","cwd","tool_name","tool_input"}` for `preToolUse`. **No `session_id`, no transcript path** in the 2.22 probe. | Medium |
| Tool names | Two spellings depending on the UI mode: `read` / `write` / `shell` in the TUI, `fs_read` / `fs_write` / `execute_bash` otherwise. `fs_write` input carries `command`, `path`, `file_text`. | Medium |
| Tool `Skill` | No known equivalent tool call. | Low |
| `agents/*.md` | `~/.kiro/agents/<name>.json` (global) or `.kiro/agents/<name>.json` (project; wins on a name clash). Fields: `prompt`, `tools`, `allowedTools`, `toolsSettings`, `resources`, `hooks`, `mcpServers`, `includeMcpJson`, `model`. | High |
| `.mcp.json` | `mcpServers` inline in the agent config, or `includeMcpJson: true` to pick up `~/.kiro/settings/mcp.json`. | High |
| `.claude/skills/<name>/SKILL.md` | `.kiro/skills/<name>/SKILL.md`, loaded by an agent through `skill://` entries in `resources`. One guide documents this for 2.x; the official reference calls `skill://` new in V3. | Medium |
| Pre-approved commands | `toolsSettings` shell `allowedCommands` (regex list) and write `allowedPaths` / `deniedPaths`. | Medium |
| Subagents | A subagent tool (`use_subagent`), restricted by `toolsSettings.subagent.availableAgents`. Hooks do not carry over into subagents. | Medium |

## 4. Target design

### 4.1 Three hooks, one turn boundary

Only three events are used, and all three have been seen working on 2.x by independent probes.
`stop` is deliberately not load-bearing.

| Hook | Does |
|---|---|
| `agentSpawn` | Lifecycle pass (archive what should be archived), reset this project's work counter, print the skill index and the last-run summary. |
| `preToolUse` | Count the tool call. Count a read into a managed skill directory as `use` or `view`. Deny direct writes into managed skill directories and the state directory (4.4). |
| `userPromptSubmit` | The turn boundary. Count one request, drain the intent queue (the promoter lands whatever the previous turn staged), then decide whether to inject an instruction: a reflection, a curation, or nothing. |

Upstream hangs the turn boundary on `Stop`. Moving it to the start of the next turn gives the same
count of one request per user turn and removes the dependency on the least reliable 2.x event. The
cost is that skills staged in a turn land when you send the next prompt, not when the turn ends. If
spike S4 shows `stop` fires reliably on the work version, a `stop` hook that only drains the queue
can be added so skills land immediately; nothing else changes.

### 4.2 Reflection at prompt time

```
you send a prompt
  -> userPromptSubmit hook:
       count the request; drain the queue
       work counter below threshold -> print nothing
       threshold crossed            -> reset the counter, print the reflection instruction
  -> the agent sees your prompt plus the instruction:
       first reflects on the conversation so far, stages intents, says one line
       then answers your prompt as normal
you send the next prompt
  -> userPromptSubmit hook: drain -> the promoter validates and lands the skills
```

The agent that did the work does the reflecting. It already holds the whole conversation, so no
transcript has to be found or parsed, and no separate process or key is involved.

**The instruction.** Built by the hook and printed on stdout. It is upstream's fork-carrier
instruction (`spawn.py:FORK_INSTRUCTION`, written for a reflector that can see the real
conversation), adapted to say: reflect on the work before this message, stage through the installed
route, report in one line, then handle the user's request. It carries the current skill index and
the path to `lib/format_spec.md` for the agent to read. If nothing is worth keeping, the agent
stages nothing and moves on.

**No session id.** The 2.22 payload has none, so the work counter cannot be keyed per session the
way upstream does it. On Kiro it is keyed per project root and reset at `agentSpawn`, which keeps a
fresh session from being asked to reflect on work it never saw. Two sessions open in the same
repository share the counter; the effect is a trigger that comes a little early or late in one of
them, which is harmless. If the payload on the work version does include a session id (spike S1),
the adapter uses it and upstream's per-session behaviour returns.

**On demand.** The `userPromptSubmit` hook receives the prompt text. A prompt that starts with
`autoharness learn` makes the hook inject the reflection instruction immediately, whatever the
counter says. This replaces upstream's `/learn` skill without depending on 2.x skill or prompt
features. It is also how you capture the end of a session, since there is no end-of-session
reflection: once you stop prompting there is no turn to attach one to.

**What changes for the user compared with upstream.**

- Reflection is visible. At the default cadence of 50 tool calls, an answer is occasionally
  preceded by a short reflection step.
- It spends credits and context from the working session, on whatever model the session uses. In
  exchange the reflector sees the real conversation instead of a clipped, redacted window.
- Work after the last trigger is only learned if you say `autoharness learn` before quitting.

New knob: `AUTOHARNESS_REFLECT_MODE` = `prompt` (default on Kiro) or `off`. `off` keeps counters,
index and lifecycle running and leaves learning to `autoharness learn`.

### 4.3 Curation at prompt time

The curator reads the library, not the conversation.

- Trigger: unchanged cadence (`AUTOHARNESS_CONSOLIDATE_EVERY_N` requests). The hook takes the
  pre-run snapshot of both skill trees itself (`spawn._snapshot_skills`, moved out of the spawn
  path), then injects an instruction to delegate to the `autoharness-curator` agent as a subagent,
  passing the agent-authored skill index.
- The curator agent's config allows only read tools and the staging route.
- A prompt gets at most one injected job. If reflection and curation fall due together, reflection
  goes first and curation waits for the next prompt.
- If delegating to a named agent is not possible on the work version (spike S6), the main agent
  runs the curator instruction itself. That costs main-session context and changes nothing else.

### 4.4 Guarding the skill tree

Upstream stops the reflector writing files by denying its write tools outright. That cannot be
done here, because reflection now shares a turn with your real request, where writes are
legitimate. The Kiro port guards the destination instead of the actor:

- `preToolUse` denies (exit 2) any write-tool call whose path resolves inside an agent-created
  skill directory, the archive, or `.kiro/autoharness/`. The message tells the agent to stage an
  intent instead.
- Skills you wrote yourself are not covered and stay editable as usual; autoharness never manages
  those anyway.
- This is a guard against the agent taking a shortcut, not a security boundary: a shell command
  can still write anywhere. The promoter remains the authority. It validates every intent, and a
  skill directory changed behind its back simply has no ledger entry for the change.

One consequence: asking Kiro to hand-edit a learned skill is refused. Edit the file yourself, or
ask the agent to stage an update.

### 4.5 Staging route: MCP or CLI

Whether the work setup permits a local stdio MCP server is unknown, so the port ships two routes
over the same `stage_skill.server.stage()` function:

| Route | How | When |
|---|---|---|
| MCP | `stage_skill` declared under `mcpServers` in the agent config. Upstream's route, unchanged server. | Default if spike S3 shows local MCP servers are allowed. |
| CLI | New thin entry point: `python3 -m autoharness.stage_skill.cli`, intent JSON on stdin via a quoted heredoc, result JSON on stdout. Called through the shell tool. | Fallback if MCP is restricted. |

Both validate identically and return the same errors, so the agent can fix a rejected intent on
the spot. The CLI route sends multi-line skill bodies through shell quoting, which is its main
weakness; the agent config pre-approves that one command with a `toolsSettings` shell
`allowedCommands` pattern so it does not raise an approval prompt each time. The installer takes
`--stage-via mcp|cli`, and the injected instructions name whichever route was installed.

### 4.6 Recall without relying on Kiro's skill loader

Skill support on the work version is uncertain, so recall does not depend on it. The index printed
at `agentSpawn` lists every learned skill with a one-line description and tells the agent to read
`<skills dir>/<name>/SKILL.md` when a description matches. Upstream already has this hint for
worktree sessions (`on_session_start._read_hint`); on Kiro it is always included.

Where `skill://` resources work (spike S5), the agent config also lists
`skill://.kiro/skills/*/SKILL.md` and the global equivalent, so Kiro's own recall runs alongside.
The glob is `*/SKILL.md`, not `**/SKILL.md`: archived skills live at `skills/.archive/<name>/`, and a
recursive glob would put them back into recall.

Usage counting follows from this. With no skill tool call to observe, a read of
`<skill>/SKILL.md` counts as `use` and a read of any other file in the skill directory counts as
`view`. Reads made while a reflection or curation instruction is pending are not counted, since
comparing against the library is not using it.

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
| `normalize(event) -> event` | Map the host payload to the internal shape: canonical event name, session key, `cwd`, `prompt`, tool kind (`read`, `write`, `shell`, `other`) from either tool-name spelling, target path. |
| `session_key(event)` | The payload's session id when present, otherwise a per-project key. |
| `emit_context(text)`, `emit_deny(reason)` | Output protocol: plain stdout and exit 2 on Kiro. |
| `turn_boundary` | Which event closes a turn: `Stop` on Claude, `userPromptSubmit` on Kiro. |
| `reflect_modes` | Claude: `headless`. Kiro: `prompt`. |

Recommendation: keep the Claude adapter rather than deleting the Claude path. Upstream is active
(235 merged PRs), and an adapter keeps the fork's diff confined to `host/`, the dispatcher and the
install files, so upstream merges stay cheap. It also keeps the existing 44 test files passing
unchanged, which is the regression net for the port. The same seam is where V3 support goes later
(section 9).

### 4.8 Files the port adds

```
kiro/
  agents/autoharness.json.tmpl           default-like agent + the three hooks
  agents/autoharness-curator.json.tmpl   subagent for the curation pass
  agents/prompts/curator.md              body of agents/curator.md, tool names reworded
src/autoharness/install.py               python3 -m autoharness.install [--scope global|workspace]
                                           [--into <existing agent>] [--stage-via mcp|cli]
                                           [--env K=V] [--uninstall]
src/autoharness/stage_skill/cli.py       CLI staging route
src/autoharness/hook/reflect.py          builds the reflection and curation instructions
src/autoharness/host/...
```

The main agent as installed (MCP route shown):

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
  "resources": ["skill://.kiro/skills/*/SKILL.md", "skill://~/.kiro/skills/*/SKILL.md"],
  "toolsSettings": { "subagent": { "availableAgents": ["autoharness-curator"] } },
  "hooks": {
    "agentSpawn":       [ { "command": "AUTOHARNESS_HOST=kiro PYTHONPATH=/abs/path/src python3 -m autoharness.hook.dispatch" } ],
    "userPromptSubmit": [ { "command": "...same..." } ],
    "preToolUse":       [ { "command": "...same..." } ]
  }
}
```

It sets no `prompt` and no tool restrictions, so it behaves like the built-in default agent with
hooks added. `allowedTools` is deliberately left out so your normal approval prompts are unchanged.
Exact field spellings for 2.x need checking against `kiro-cli agent validate` (spike S2).

If you already work in a custom agent, `--into <agent>` merges only the `hooks` entries, the
`stage_skill` server and the curator permission into that agent's file, after backing it up.

### 4.9 Per-component changes

**Roots (`lib/layer.py`).** `default_root` reads `host.current().root_dirname`. Skills land in
`.kiro/skills/`, state in `.kiro/autoharness/`, archive in `.kiro/skills/.archive/`. The worktree
remapping stays.

**Dispatch (`hook/dispatch.py`).** Call `host.normalize(event)` first, then route on canonical
event name and tool kind instead of literal names. The per-turn work now attached to `Stop` (bump
request counters, drain the interactive queue, check the threshold, check the curator cadence) is
factored into one function that the adapter's `turn_boundary` event calls. On Kiro:

- `agentSpawn` and `userPromptSubmit` print plain text on stdout, exit 0.
- A denied write exits 2 with the reason on stderr. `main()` currently always returns 0, so the
  deny path needs its own exit code.
- Every other outcome is exit 0 with nothing on stdout. Hook stdout becomes agent context on Kiro,
  so the dispatcher must never print diagnostics there.
- There is no reflector identity to detect and no child session, so the `agent_type` and
  `AUTOHARNESS_CHILD_SESSION` branches are inert on Kiro.

**Counters (`lib/counters.py`).** Take the adapter's session key instead of assuming a host session
id. No format change on disk.

**Skill format (`lib/validate.py`, `lib/format_spec.md`).** The Agent Skills format requires `name`
to match the folder name and to be lowercase letters, digits and hyphens, at most 64 characters.
`layer._SAFE_NAME` also allows uppercase, dots and underscores; on Kiro the promoter rejects names
outside the stricter set. autoharness also writes a `category` frontmatter key; keep it at top
level if Kiro tolerates unknown keys (S5), otherwise move it to `metadata.category` and teach the
index to read both.

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

Requirements on the work machine: Python 3.10+ as `python3` (stock on Ubuntu 22.04), permission to add an agent file and
select it (confirmed allowed), and either a local MCP server or shell access for the staging route.

For a work setup it is also worth knowing what the tool stores: learned skills under
`.kiro/skills/` contain distilled lessons plus redacted evidence quotes from sessions, and project
skills sit inside the repository, where they can be committed. `AUTOHARNESS_DISABLE_GLOBAL=1` keeps
everything per-project.

## 6. Risks and known gaps

| # | Risk | Effect | Mitigation |
|---|---|---|---|
| R1 | Hooks only run under the `autoharness` agent | Sessions started with another agent learn nothing and count nothing | Default-agent setting; `--into` for an existing agent; the index line makes it obvious when the hooks are live |
| R2 | The work build (around 2.7) is much older than the probed 2.22 / 2.24 | Payload fields, tool names or stdout injection differ | Spikes S1 and S2 before anything is built; `normalize()` accepts both tool-name spellings |
| R3 | Local MCP servers may be restricted | `stage_skill` unavailable | CLI staging route (4.5); spike S3 |
| R4 | Reflection shares a turn with the user's request | The agent may skip the reflection, or do it badly, when the request is urgent | An empty queue is a normal outcome and the counter simply starts again; `autoharness learn` forces it |
| R5 | Reflection costs context and credits in the working session | Faster context growth | One-line output, tunable cadence, `AUTOHARNESS_REFLECT_MODE=off`, curator delegated to a subagent |
| R6 | No session id | Concurrent sessions in one repo share the work counter | Accepted; per-session keying returns if the payload has an id |
| R7 | No skill tool call to observe | `use` is approximated by a read of `SKILL.md` | 4.6; lifecycle thresholds may need retuning after real use |
| R8 | Hook stdout becomes context | A stray print pollutes every turn | Stdout only from `agentSpawn` and an injecting `userPromptSubmit`; a test asserts empty stdout otherwise |
| R9 | No end-of-session reflection | The tail of a session is not learned | `autoharness learn` before quitting |
| R10 | Three Python starts per turn plus one per tool call | Latency | Same order of cost as on Claude today; hooks do no model calls |
| R11 | Work later moves to CLI V3 | Hook location and names change | Section 9 |

## 7. Phase 0: spikes on the work machine

Each is a throwaway agent config plus a few lines of script that logs stdin to a file. They have to
run where Kiro is installed. S1 to S3 decide the design; the rest tune it.

| # | Question | Decides |
|---|---|---|
| S1 | `kiro-cli --version`. Exact stdin payload for `agentSpawn`, `userPromptSubmit`, `preToolUse` (for a read, a write and a shell call). Is there a session id? Does `userPromptSubmit` stdout reach the model? | `normalize()`, counters, the whole prompt-time design |
| S2 | Does the agent template pass `kiro-cli agent validate`, and can it be made the default? | Template, install |
| S3 | Can a local stdio MCP server declared in the agent config be started and called? | MCP versus CLI staging |
| S4 | Does `stop` fire each turn? Does anything in its output reach the model or hold the turn open? | Optional early drain; an end-of-turn reflection mode |
| S5 | Do `skill://` resources load on this version? Are unknown frontmatter keys and the `.archive/` directory tolerated? | Native recall, skill format |
| S6 | Can the main agent delegate to a named agent as a subagent, and do the parent's hooks stay out of it? | Curator delegation |
| S7 | Does `agentSpawn` stdout reach the model, and does `preToolUse` exit 2 block a call with stderr shown to the agent? | Index injection, write guard |

## 8. Implementation phases

1. **Adapter seam, no behaviour change.** Introduce `host/`, move Claude behaviour into
   `host/claude.py`, default `AUTOHARNESS_HOST=claude` for this phase. Existing tests pass untouched.
2. **Python 3.10.** The TOML fallback reader and the lowered floor (4.9). Independent of Kiro and
   small enough to offer upstream on its own.
3. **Kiro adapter, roots, install.** `host/kiro.py`, agent templates, `install.py`, name-rule
   tightening, session key, default host flipped to `kiro`. At the end of this phase counters, the
   index and the lifecycle pass work on Kiro.
4. **Prompt-time reflection.** `hook/reflect.py`, the turn-boundary refactor, `autoharness learn`,
   the write guard, the MCP staging route.
5. **CLI staging route**, if S3 requires it.
6. **Prompt-time curation.**
7. **Docs.** README rewritten for Kiro; upstream README kept as `docs/claude-code.md`.

### Tests

- TOML fallback: the shipped rules file parses identically through `tomllib` and the fallback;
  unsupported syntax raises. The whole suite runs on 3.10 in CI.
- `host/kiro.py`: `normalize` over recorded payload fixtures from S1, both tool-name spellings;
  output protocol and exit codes.
- Turn boundary: each `userPromptSubmit` counts one request and drains the queue; threshold crossed
  injects once and resets; `agentSpawn` resets the work counter; `autoharness learn` injects
  regardless of the counter; at most one job per prompt; `AUTOHARNESS_REFLECT_MODE=off` never injects.
- Write guard: writes into managed skill, archive and state directories are denied with exit 2;
  writes to user-authored skills and everywhere else pass; path traversal and symlinks resolve
  before the check.
- Usage counters: `SKILL.md` read counts `use`, other files count `view`, nothing counts while an
  instruction is pending.
- `stage_skill/cli.py`: same accept and reject results as the MCP path for the cases in
  `tests/test_stage_skill.py`.
- `install.py`: idempotence, `--into` merge preserves the rest of the agent file, uninstall removes
  only its own entries, refusal to overwrite foreign files. All against a temporary home directory.
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
PascalCase triggers, adds global hooks, `SessionEnd`, a session id in the payload, and a documented
way for a `Stop` hook to start another turn. That last one would allow reflection at the end of
the turn that crossed the threshold, with writes denied for its duration, which is closer to
upstream. V3 still accepts the 2.x hook form inside agent configs, so this port should keep working
after an upgrade; a V3-native install is a later addition behind the same adapter.

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
2. **Own agent or merge into an existing one?** If you already use a custom agent at work,
   `--into` is the better fit; otherwise the `autoharness` agent as default.
3. **Install scope.** Recommended: global, so every repository learns.
4. **Default cadence.** Upstream's 50 tool calls was tuned for a background pass. With a visible
   pass a higher default may be more comfortable. Recommended: keep 50 and adjust after real use.

## Sources

- Kiro docs: [CLI 2.x reference](https://kiro.dev/docs/cli/2x-reference/) (hook model, exit codes,
  `toolsSettings`), [Hooks](https://kiro.dev/docs/hooks/), [Hook triggers](https://kiro.dev/docs/hooks/types/),
  [Hook actions](https://kiro.dev/docs/hooks/actions/), [V3 hooks migration](https://kiro.dev/docs/cli/v3/hooks-migration/)
  (2.x event names, tool groups), [Agent configuration reference](https://kiro.dev/docs/custom-agents/configuration-reference/),
  [Skills](https://kiro.dev/docs/skills/), [Headless mode](https://kiro.dev/docs/cli/headless/),
  [CLI commands](https://kiro.dev/docs/reference/cli-commands/)
- 2.x probes: [vshulcz/deja-vu#4304](https://github.com/vshulcz/deja-vu/issues/4304) (kiro-cli 2.22.0:
  payloads, stdout injection, default agent), [martin-conur/task-force#221](https://github.com/martin-conur/task-force/issues/221)
  (kiro-cli 2.24.0: hook location, events firing, stdout injection),
  [kirodotdev/Kiro#10344](https://github.com/kirodotdev/Kiro/issues/10344) (stop hook reports)
- 2.x guides (third-party): [Kiro CLI v2 Power User Guide](https://nbodd.github.io/kiro-power-guide/),
  [Kiro CLI Guide: Agent Configuration](https://kiro.kuronetwork.me/en/09-agents/)
