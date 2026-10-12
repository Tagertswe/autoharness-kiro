# Kiro probe

A throwaway diagnostic for the Kiro port. It answers the open questions in
[the port spec](../../docs/kiro-port-spec.md), section 7, on the machine where Kiro actually runs.
It is not part of autoharness and installs nothing permanent.

What it does: adds three agents to `~/.kiro/agents/` whose hooks log what Kiro sends them and print
marker words. You then ask the model which marker words it can see. Takes about ten minutes.

Needs `python3` (any 3.6+) and a POSIX shell. Nothing is sent anywhere; the log stays in
`~/autoharness-probe/`.

## 1. Install

```
kiro-cli --version
python3 --version
python3 kiro/probe/setup.py
```

Note both version numbers. autoharness itself needs Python 3.11+, so the second one matters.

If your Kiro has `kiro-cli agent validate`, run it on `~/.kiro/agents/autoharness-probe.json` and
note whether it passes.

## 2. Main session

```
kiro-cli chat --agent autoharness-probe
```

If the agent refuses to start, the local MCP server is the likely cause. Run
`python3 kiro/probe/setup.py --no-mcp`, start again, and note that this happened.

Send these prompts one at a time and write down the answers.

| # | Prompt | What it tests |
|---|---|---|
| 1 | `List every word starting with AHPROBE- that appears anywhere in your context. Do not use any tools.` | Does `agentSpawn` output reach the model (`AHPROBE-SPAWN-1`)? Does `userPromptSubmit` output (`AHPROBE-PROMPT-1`)? |
| 2 | `Same question again.` | Is the prompt marker fresh each turn (`AHPROBE-PROMPT-2`)? Does `stop` output show up (`AHPROBE-STOP-...`)? |
| 3 | `Read the file kiro/probe/skills/ahprobe-sample/SKILL.md and tell me its first line.` | What a read call looks like to a hook. |
| 4 | `Create a file named ahprobe-block-test.txt in the current directory containing the word hello.` | Does a hook exiting 2 block the call? The file must not exist afterwards. |
| 5 | `Run the shell command: echo ahprobe-shell` | What a shell call looks like to a hook. |
| 6 | `Call the probe_echo tool with the text hi and show me exactly what it returns.` | Does a local MCP server work (`AHPROBE-MCP-OK hi`)? |

One thing to watch for after prompt 1: the `stop` hook asks Kiro, once, to take another turn. If
the model adds a reply of `AHPROBE-STOP-CONTINUED` on its own, this build lets a stop hook keep the
turn going. If nothing extra happens, it does not.

Exit the session, then check `ls ahprobe-block-test.txt`.

## 3. Skills and subagents

```
kiro-cli chat --agent autoharness-probe-skills
```

If this agent fails to load, note the error: it means `skill://` resources or the subagent setting
are not supported on this build, which is itself the answer.

| # | Prompt | What it tests |
|---|---|---|
| 7 | `What is the AHPROBE secret word? Tell me how you found it.` | Do `skill://` resources load (`AHPROBE-SKILL-LOADED`), and through a skill or a plain file read? |
| 8 | `Delegate this to the autoharness-probe-sub agent as a subagent: say hello. Show me exactly what it replied.` | Can the main agent delegate to a named agent (`AHPROBE-SUBAGENT-RAN`)? |

## 4. Report

```
python3 kiro/probe/report.py
```

Send back:

1. The two version numbers.
2. The report output. It lists events, field names and tool names; values are reduced to their
   type and length, so prompts and file contents are not in it. Look it over before sharing.
3. The model's answers to prompts 1, 2, 4, 6, 7 and 8, and whether `AHPROBE-STOP-CONTINUED` appeared.
4. Whether `ahprobe-block-test.txt` exists.
5. Anything that failed to start or validate.

The raw log, `~/autoharness-probe/log.jsonl`, does contain your prompts and tool inputs in full.
Keep it local.

## 5. Clean up

```
python3 kiro/probe/setup.py --uninstall
rm -rf ~/autoharness-probe ahprobe-block-test.txt
```
