"""Install or remove the Kiro probe agents.

    python3 kiro/probe/setup.py              # write the agents into ~/.kiro/agents
    python3 kiro/probe/setup.py --no-mcp     # same, without the local MCP server
    python3 kiro/probe/setup.py --uninstall  # remove them again

CLI 2.x reads hooks only from an agent config and has no variable for "this checkout", so the
agent files are generated here with absolute paths rather than shipped as static JSON. Three
agents, split so that a feature a given build rejects cannot take the others down with it:

  autoharness-probe         hooks + local MCP server   (spikes S1, S3, S4, S7)
  autoharness-probe-skills  hooks + skill:// + subagent (spikes S5, S6)
  autoharness-probe-sub     bare agent, the delegation target

Only files this script wrote (marked in their description) are ever overwritten or removed.
"""
import argparse
import json
import os
import shlex
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
OWNED = "[autoharness-probe]"  # ownership marker: never touch an agent file that lacks it
EVENTS = ("agentSpawn", "userPromptSubmit", "preToolUse", "postToolUse", "stop")
AGENTS = ("autoharness-probe", "autoharness-probe-skills", "autoharness-probe-sub")


def kiro_home(override=None):
    return Path(override or os.environ.get("KIRO_HOME") or Path.home() / ".kiro")


def _hooks(agent, python):
    cmd = f"{shlex.quote(python)} {shlex.quote(str(HERE / 'probe.py'))}"
    return {event: [{"command": f"{cmd} {event} {agent}"}] for event in EVENTS}


def build(python="python3", mcp=True):
    main = {
        "name": "autoharness-probe",
        "description": f"{OWNED} Logs hook payloads; default tools otherwise.",
        "tools": ["*"],
        "includeMcpJson": True,
        "hooks": _hooks("autoharness-probe", python),
    }
    if mcp:
        main["mcpServers"] = {"ahprobe": {"command": python, "args": [str(HERE / "mcp_echo.py")]}}
    skills = {
        "name": "autoharness-probe-skills",
        "description": f"{OWNED} Tests skill:// resources and subagent delegation.",
        "tools": ["*"],
        "includeMcpJson": True,
        "resources": [f"skill://{HERE / 'skills'}/*/SKILL.md"],
        "toolsSettings": {"subagent": {"availableAgents": ["autoharness-probe-sub"]}},
        "hooks": _hooks("autoharness-probe-skills", python),
    }
    sub = {
        "name": "autoharness-probe-sub",
        "description": f"{OWNED} Delegation target for the probe. Says a marker word.",
        "prompt": "You are a probe subagent. Whatever you are asked, reply with exactly "
                  "AHPROBE-SUBAGENT-RAN and nothing else.",
        "tools": ["*"],
    }
    return {"autoharness-probe": main, "autoharness-probe-skills": skills,
            "autoharness-probe-sub": sub}


def _owned(path):
    try:
        return OWNED in str(json.loads(path.read_text(encoding="utf-8")).get("description", ""))
    except (OSError, ValueError, AttributeError):
        return False


def install(home, python="python3", mcp=True):
    agents = home / "agents"
    agents.mkdir(parents=True, exist_ok=True)
    written = []
    for name, config in build(python, mcp).items():
        path = agents / f"{name}.json"
        if path.exists() and not _owned(path):
            raise SystemExit(f"refusing to overwrite {path}: not written by this probe")
        path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
        written.append(path)
    return written


def uninstall(home):
    removed = []
    for name in AGENTS:
        path = home / "agents" / f"{name}.json"
        if path.exists() and _owned(path):
            path.unlink()
            removed.append(path)
    return removed


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--uninstall", action="store_true")
    parser.add_argument("--no-mcp", action="store_true",
                        help="leave the local MCP server out of the main probe agent")
    parser.add_argument("--python", default="python3",
                        help="interpreter the hooks should run (default: python3)")
    parser.add_argument("--kiro-home", help="Kiro config directory (default: $KIRO_HOME or ~/.kiro)")
    args = parser.parse_args(argv)
    home = kiro_home(args.kiro_home)
    if args.uninstall:
        for path in uninstall(home):
            print(f"removed {path}")
        return 0
    for path in install(home, args.python, not args.no_mcp):
        print(f"wrote {path}")
    print("\nNext: follow kiro/probe/README.md, starting with\n"
          "  kiro-cli chat --agent autoharness-probe")
    return 0


if __name__ == "__main__":
    sys.exit(main())
