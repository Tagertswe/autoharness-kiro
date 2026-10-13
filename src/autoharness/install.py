"""Install autoharness for Kiro CLI 2.x: write the agent configs, nothing else.

    python3 kiro/install.py                      # ~/.kiro/agents/autoharness.json + the curator
    python3 kiro/install.py --scope workspace    # the same under ./.kiro/agents
    python3 kiro/install.py --into my-agent      # add the hooks to an agent you already use
    python3 kiro/install.py --uninstall

docs/kiro-port-spec.md 4.8 and section 5. Kiro CLI 2.x reads hooks only from an agent config and
has no variable for "this checkout", so the files are generated with absolute paths pointing at
this clone: nothing is copied, published or downloaded, and `git pull` is the update. Settings come
from the gitignored .env at the root of the checkout (and --env), and are baked into the hook and
MCP commands so they hold for every session, whatever shell starts Kiro.

Only files this installer wrote are overwritten or removed, an agent merged into is backed up
first, and learned skills and state are left alone on uninstall.
"""
import argparse
import json
import os
import shlex
import shutil
import sys
from pathlib import Path

from autoharness import host
from autoharness.lib import envfile, layer

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
OWNED = "[autoharness]"  # ownership marker in an agent's description
AGENT = "autoharness"
CURATOR = "autoharness-curator"
EVENTS = ("agentSpawn", "userPromptSubmit", "preToolUse", "stop")
DISPATCH = "autoharness.hook.dispatch"
# not *.json: Kiro reads every .json file in an agents directory as an agent config
MANIFEST = ".autoharness-install.manifest"
_OLD_MANIFEST = ".autoharness-install.json"  # where it was first written; read once, then removed
BACKUP_SUFFIX = ".autoharness-bak"
GITIGNORE_MARK = "# written by autoharness: local bookkeeping that must not reach the shared repository"
GITIGNORE = (GITIGNORE_MARK + "\n"
             ".archive/\n"
             "*/.sidecar.json\n"
             "*/.sidecar.json.lock\n"
             "*/.ledger.jsonl\n"
             "*/references/evidence-*.md\n")
_CURATOR_PREFACE = (
    "You are running under Kiro CLI. Where the text below names Claude Code tools, use Kiro's: the "
    "`read` tool for Read / Grep / Glob, and the `stage_skill` tool of the `stage_skill` MCP server "
    "(`@stage_skill/stage_skill`) for staging. Your task message carries the skill index, the skill "
    "directories and the path of the format spec; read the spec before staging.\n\n"
)


def kiro_home(override=None):
    return Path(override or os.environ.get("KIRO_HOME") or Path.home() / ".kiro")


def settings(env_path=None, extra=(), environ=None):
    """AUTOHARNESS_* settings to bake in: the .env file, then --env KEY=VALUE on top."""
    values = {}
    if env_path and Path(env_path).exists():
        values.update(envfile.load(Path(env_path), environ))
    for item in extra:
        key, sep, value = item.partition("=")
        if not sep or not key.strip():
            raise SystemExit(f"--env expects KEY=VALUE, got {item!r}")
        values[key.strip()] = value
    values = {k: v for k, v in values.items() if k.startswith("AUTOHARNESS_")}
    values[host.HOST_ENV] = host.KIRO
    return values


def _env(values):
    return {"PYTHONPATH": str(SRC), **values}


def _hook_command(values, python):
    # `env` rather than a VAR=value shell prefix: it works whether or not Kiro runs hooks through a shell
    pairs = " ".join(f"{key}={shlex.quote(value)}" for key, value in _env(values).items())
    return f"env {pairs} {shlex.quote(python)} -m {DISPATCH}"


def _mcp(values, python):
    return {"command": python, "args": ["-m", "autoharness.stage_skill.server"], "env": _env(values)}


def resources(values, home):
    """skill:// entries: the learned skills, the skills the user already had, the project's, and the
    merge-request skill(s). `*/SKILL.md`, never `**`: the archive sits in a dot-directory underneath."""
    learned = values.get(layer.GLOBAL_SKILLS_DIR_ENV, "").strip()
    out = []
    if learned:
        out.append(f"skill://{Path(learned).expanduser()}/*/SKILL.md")
    out.append(f"skill://{home / 'skills'}/*/SKILL.md")
    out.append("skill://.kiro/skills/*/SKILL.md")
    publish_skill = values.get("AUTOHARNESS_PUBLISH_SKILL", "").strip()
    if publish_skill:
        path = Path(publish_skill).expanduser()
        # one skill file, or a folder holding several (conventions, merge requests, ...)
        out.append(f"skill://{path}" if path.suffix == ".md" else f"skill://{path}/*/SKILL.md")
    return list(dict.fromkeys(out))


def _curator_prompt():
    text = (ROOT / "agents" / "curator.md").read_text(encoding="utf-8")
    if text.startswith("---"):
        text = text.split("---", 2)[2]  # Claude frontmatter: not part of the prompt
    return _CURATOR_PREFACE + text.strip() + "\n"


def build(values, home, python="python3"):
    main = {
        "name": AGENT,
        "description": f"{OWNED} Default agent plus autoharness self-learning hooks.",
        "tools": ["*"],
        "includeMcpJson": True,
        "mcpServers": {"stage_skill": _mcp(values, python)},
        "resources": resources(values, home),
        "hooks": {event: [{"command": _hook_command(values, python)}] for event in EVENTS},
    }
    curator = {
        "name": CURATOR,
        "description": f"{OWNED} Consolidates self-learned skills. Delegated to by the autoharness hooks.",
        "prompt": _curator_prompt(),
        "tools": ["read", "@stage_skill"],
        "mcpServers": {"stage_skill": _mcp(values, python)},
    }
    return {AGENT: main, CURATOR: curator}


def _read(path):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _owned(path):
    data = _read(path)
    return bool(data) and OWNED in str(data.get("description", ""))


def _write(path, data):
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def _is_ours(hook):
    return isinstance(hook, dict) and DISPATCH in str(hook.get("command", ""))


def _merge(agent, ours):
    """Add the hooks, the MCP server and the skill resources to an existing agent's config, leaving
    everything else as it was. Returns the resources that were added (uninstall removes only those)."""
    hooks = agent.setdefault("hooks", {})
    for event, entries in ours["hooks"].items():
        kept = [h for h in hooks.get(event) or [] if not _is_ours(h)]
        hooks[event] = kept + entries
    agent.setdefault("mcpServers", {})["stage_skill"] = ours["mcpServers"]["stage_skill"]
    have = agent.setdefault("resources", [])
    added = [r for r in ours["resources"] if r not in have]
    have.extend(added)
    return added


def _unmerge(agent, added):
    hooks = agent.get("hooks") or {}
    for event in list(hooks):
        hooks[event] = [h for h in hooks[event] or [] if not _is_ours(h)]
        if not hooks[event]:
            del hooks[event]
    (agent.get("mcpServers") or {}).pop("stage_skill", None)
    agent["resources"] = [r for r in agent.get("resources") or [] if r not in added]
    for key in ("hooks", "mcpServers", "resources"):
        if key in agent and not agent[key]:
            del agent[key]


def write_gitignore(values):
    """Keep counters, ledger, archive and session evidence out of the shared repository (4.10)."""
    learned = values.get(layer.GLOBAL_SKILLS_DIR_ENV, "").strip()
    if not learned:
        return None
    folder = Path(learned).expanduser()
    if not folder.parent.is_dir():
        raise SystemExit(f"{layer.GLOBAL_SKILLS_DIR_ENV} points into {folder.parent}, which does not exist")
    folder.mkdir(exist_ok=True)
    path = folder / ".gitignore"
    if path.exists() and GITIGNORE_MARK not in path.read_text(encoding="utf-8"):
        return None  # someone else's file: leave it, the operator decides
    path.write_text(GITIGNORE, encoding="utf-8")
    return path


def _manifest(agents):
    return _read(agents / MANIFEST) or _read(agents / _OLD_MANIFEST)


def install(agents, values, home, *, python="python3", into=None):
    agents.mkdir(parents=True, exist_ok=True)
    configs = build(values, home, python)
    manifest = _manifest(agents) or {"owned": [], "merged": {}}
    written = []
    targets = [CURATOR] if into else [AGENT, CURATOR]
    for name in targets:
        path = agents / f"{name}.json"
        if path.exists() and not _owned(path):
            raise SystemExit(f"refusing to overwrite {path}: not written by autoharness")
        _write(path, configs[name])
        written.append(path)
        if path.name not in manifest["owned"]:
            manifest["owned"].append(path.name)
    if into:
        path = agents / f"{into}.json"
        agent = _read(path)
        if agent is None:
            raise SystemExit(f"cannot merge into {path}: not found or not a JSON object")
        theirs = (agent.get("mcpServers") or {}).get("stage_skill")
        if theirs is not None and "autoharness.stage_skill.server" not in (theirs.get("args") or []):
            raise SystemExit(f"cannot merge into {path}: it already has an MCP server named stage_skill "
                             "that is not autoharness's; rename it first")
        backup = path.with_name(path.name + BACKUP_SUFFIX)
        if not backup.exists():
            shutil.copy2(path, backup)
        previously = manifest["merged"].get(path.name, [])
        added = _merge(agent, configs[AGENT])
        manifest["merged"][path.name] = sorted(set(previously) | set(added))
        _write(path, agent)
        written.append(path)
    _write(agents / MANIFEST, manifest)
    (agents / _OLD_MANIFEST).unlink(missing_ok=True)
    return written


def uninstall(agents):
    manifest = _manifest(agents) or {"owned": [f"{AGENT}.json", f"{CURATOR}.json"], "merged": {}}
    removed = []
    for name in manifest.get("owned", []):
        path = agents / name
        if path.exists() and _owned(path):
            path.unlink()
            removed.append(path)
    for name, added in (manifest.get("merged") or {}).items():
        path = agents / name
        agent = _read(path)
        if agent is None:
            continue
        _unmerge(agent, added)
        _write(path, agent)
        removed.append(path)
    for name in (MANIFEST, _OLD_MANIFEST):
        (agents / name).unlink(missing_ok=True)
    return removed


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--scope", choices=("global", "workspace"), default="global",
                        help="global: ~/.kiro/agents (default); workspace: ./.kiro/agents")
    parser.add_argument("--into", metavar="AGENT", help="merge the hooks into this existing agent instead "
                                                        "of installing the autoharness agent")
    parser.add_argument("--env", action="append", default=[], metavar="KEY=VALUE",
                        help="an AUTOHARNESS_* setting to bake in (repeatable); overrides .env")
    parser.add_argument("--env-file", default=str(ROOT / ".env"),
                        help="settings file (default: .env at the root of this checkout)")
    parser.add_argument("--python", default="python3", help="interpreter the hooks run (default: python3)")
    parser.add_argument("--kiro-home", help="Kiro config directory (default: $KIRO_HOME or ~/.kiro)")
    parser.add_argument("--uninstall", action="store_true")
    args = parser.parse_args(argv)
    home = kiro_home(args.kiro_home)
    agents = (home if args.scope == "global" else Path.cwd() / ".kiro") / "agents"
    if args.uninstall:
        for path in uninstall(agents):
            print(f"removed or restored {path}")
        print("Learned skills and state were left in place.")
        return 0
    try:
        values = settings(args.env_file, args.env)
    except envfile.EnvFileError as exc:
        raise SystemExit(f"{args.env_file}: {exc}") from exc
    for path in install(agents, values, home, python=args.python, into=args.into):
        print(f"wrote {path}")
    ignore = write_gitignore(values)
    if ignore:
        print(f"wrote {ignore}")
    print(f"\nSettings baked in: {', '.join(sorted(values))}")
    if args.into:
        print(f"The hooks now run whenever you use the {args.into} agent.")
    else:
        print("To use it:      kiro-cli chat --agent autoharness\n"
              "To make it the default agent:  kiro-cli agent set-default autoharness")
    print("In a session:   autoharness learn | autoharness publish | autoharness status\n"
          f"Merge request records are kept in {home / 'autoharness' / 'publishes.jsonl'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
