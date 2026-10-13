# autoharness on Kiro CLI

This fork runs autoharness under Kiro CLI 2.x as well as Claude Code. It installs locally from a
clone, publishes nothing, needs no API key and makes no network calls of its own. Design and
rationale are in [kiro-port-spec.md](kiro-port-spec.md); this page is how to use it.

Checked against kiro-cli 2.29.1 with the probe in [`kiro/probe/`](../kiro/probe/README.md). The
hooks themselves have unit tests but have not yet been run inside a real Kiro session; see
[First run](#first-run-checklist).

## Requirements

- Kiro CLI 2.x with custom agents, hooks and local MCP servers allowed.
- Python 3.10 or newer as `python3` (Ubuntu 22.04's default is fine). No packages to install.
- `git`, only if you use a shared skills repository.

## Install

```
git clone <this repository> ~/repos/autoharness-kiro
cd ~/repos/autoharness-kiro
python3 kiro/install.py
kiro-cli agent set-default autoharness      # or start it per session: kiro-cli chat --agent autoharness
```

This writes two files, `~/.kiro/agents/autoharness.json` and `~/.kiro/agents/autoharness-curator.json`,
both pointing at this clone. Updating is `git pull`; re-run the installer only when settings change.

| Option | Effect |
|---|---|
| `--scope workspace` | Write to `./.kiro/agents` instead of `~/.kiro/agents`. |
| `--into <agent>` | Add the hooks to an agent you already use. The agent file is backed up first and only hooks, the `stage_skill` server and skill resources are added. |
| `--env KEY=VALUE` | Bake in an `AUTOHARNESS_*` setting. Repeatable. Overrides `.env`. |
| `--python <path>` | Interpreter the hooks run. |
| `--uninstall` | Remove what was installed and undo a `--into` merge. Learned skills and state stay. |

Hooks in Kiro CLI 2.x belong to an agent. Sessions started with a different agent learn nothing.

## What happens in a session

- **At start** you get a one-line summary of the last learning run and the list of self-learned skills.
- **While you work**, every tool call is counted. Reading a learned skill counts as using it.
- **After enough work** (50 tool calls by default) the agent takes one extra short turn at the end
  of an answer: it reflects on the conversation, proposes skills through the `stage_skill` tool, and
  says in one line what it staged. A deterministic step then validates and writes them.
- **Direct edits of learned skills are blocked.** The agent has to propose a change; you can always
  edit the files by hand.
- **Occasionally** (every 250 requests) the agent delegates a consolidation pass to the
  `autoharness-curator` agent, which merges narrow skills into broader ones.

Skills you installed or wrote yourself are loaded and compared against, and never changed.

Prompts you can type:

| Prompt | Does |
|---|---|
| `autoharness learn` | Reflect now, whatever the counter says. Use it before closing a short session. |
| `autoharness status` | List merge requests opened so far and skill changes not yet published. |
| `autoharness publish` | Open a merge request for the learned skills (shared repository only). |

## Settings

Set them in `.env` at the root of the clone (copy `.env.example`), or with `--env`. `.env` is
ignored by git: put site-specific names and paths there and nowhere else. Re-run the installer
after changing it.

| Setting | Default | Meaning |
|---|---|---|
| `AUTOHARNESS_REFLECT_EVERY_N` | 50 | Tool calls between reflections. |
| `AUTOHARNESS_REFLECT_MODE` | `stop` | `stop`: reflect at the end of the turn. `prompt`: attach the reflection to your next prompt, for a Kiro build whose stop hook does not fire. `off`: only on `autoharness learn`. |
| `AUTOHARNESS_CONSOLIDATE_EVERY_N` | 250 | Requests between consolidation passes. 0 turns it off. |
| `AUTOHARNESS_INDEX_SUSPENDED` | 0 | 1 stops the skill list at session start. Useful once you have confirmed Kiro lists the skills itself. |
| `AUTOHARNESS_DISABLE_PROJECT` | 0 | 1 sends every learned skill to the global library and writes nothing into the repositories you work in. |
| `AUTOHARNESS_GLOBAL_SKILLS_DIR` | `~/.kiro/skills` | Where global learned skills live. |
| `AUTOHARNESS_SHARED_SKILLS_REPO` | | A clone of a shared skills repository. |
| `AUTOHARNESS_AUTO_PUBLISH` | 0 | 1 opens the merge request without being asked, when a turn ends with unpublished skill changes. |
| `AUTOHARNESS_PUBLISH_SKILL` | | The existing skills to follow when opening a merge request: a folder with one directory per skill, or a single `SKILL.md`. |

The other `AUTOHARNESS_*` settings in the main README apply unchanged. `AUTOHARNESS_NOTIFY_CMD`
runs a command of your choosing with skill names on stdin; leave it unset in a sealed environment.

## Shared skills repository

To keep learned skills in a folder of a team repository and propose them through merge requests:

```
AUTOHARNESS_SHARED_SKILLS_REPO=/path/to/shared-skills-repo
AUTOHARNESS_GLOBAL_SKILLS_DIR=${AUTOHARNESS_SHARED_SKILLS_REPO}/self-learning-skills
AUTOHARNESS_DISABLE_PROJECT=1
AUTOHARNESS_PUBLISH_SKILL=${AUTOHARNESS_SHARED_SKILLS_REPO}/path/to/skills
AUTOHARNESS_AUTO_PUBLISH=1
```

- Use a clone dedicated to this. Kiro recalls skills from whatever branch that clone has checked out.
- The installer creates the folder and a `.gitignore` in it, so counters, the ledger, archived
  skills and session evidence stay local and only `SKILL.md` and its support files reach git.
- `autoharness publish` hands the agent a summary and asks it to open one merge request limited to
  that folder, following the skills named above (they are loaded into the agent as well). The agent runs git under your normal approval prompts.
  autoharness itself only ever runs `git status`.
- With `AUTOHARNESS_AUTO_PUBLISH=1` you do not type the prompt: when a turn ends and learned skills
  are not in a merge request yet, the agent takes one more turn, opens the merge request and ends
  with a note in the session, `autoharness: opened a merge request for self-learned skills: <changes>
  -> <full link>`. The git commands still go through your approval prompts. One attempt is made per
  set of changes: if it fails or you decline, it is not asked for again until the changes differ,
  and `autoharness publish` retries by hand.
- When a merge request is opened, the link is saved in `~/.kiro/autoharness/publishes.jsonl`. Every
  other running session tells you about it once: at the end of its current turn, with its next
  answer, or at startup. `autoharness status` lists them all.
- A skill in the global library must not contain absolute paths or project names; such a proposal
  is rejected and the agent is told why.

## Where things are stored

| What | Where |
|---|---|
| Global learned skills | `~/.kiro/skills/`, or `AUTOHARNESS_GLOBAL_SKILLS_DIR` |
| Project learned skills | `<project>/.kiro/skills/` (not with `AUTOHARNESS_DISABLE_PROJECT=1`) |
| Archived skills | `.archive/` inside the skills directory |
| Global state, merge request records | `~/.kiro/autoharness/` |
| Project state (counters, queue, snapshots) | `<project>/.kiro/autoharness/`, or `~/.kiro/autoharness/projects/<id>/` with `AUTOHARNESS_DISABLE_PROJECT=1` |

The project is the directory Kiro is started in. Start Kiro from the repository root.

## First run checklist

These could not be tested without a Kiro installation. Check them the first time and note what
differs:

1. `kiro-cli agent validate --path ~/.kiro/agents/autoharness.json`, and the same for
   `autoharness-curator.json`. The curator's `tools` entry (`read`, `@stage_skill`) is the least
   certain part.
2. Start a session and ask "which autoharness skills are listed in your context?" after
   `autoharness learn` has produced one. This shows whether the `skill://` entries load, including
   the relative `.kiro/skills` one.
3. Type `autoharness learn` after some work. The agent should call `stage_skill`, and the skill
   should appear under the skills directory when the turn ends. If the call succeeds but nothing
   lands, the MCP server is running in a different directory than the session: say so, it is a
   known open point.
4. Type `autoharness status`. It should print the record list.
5. Ask the agent to edit a learned skill's `SKILL.md`. The write should be refused.
6. With a small `AUTOHARNESS_REFLECT_EVERY_N` (for example `--env AUTOHARNESS_REFLECT_EVERY_N=5`),
   check that a reflection turn follows an answer on its own. If it never does, reinstall with
   `--env AUTOHARNESS_REFLECT_MODE=prompt`.
7. With `AUTOHARNESS_AUTO_PUBLISH=1`, after `autoharness learn` lands a skill the agent should take
   a second extra turn, open the merge request and end with the note and link. If the skill lands
   but no publish follows, this Kiro build does not let a stop hook continue twice in a row: use
   `AUTOHARNESS_REFLECT_MODE=prompt`, or type `autoharness publish`.

The probe in `kiro/probe/` can be re-run after any Kiro update to see whether hook behaviour changed.
