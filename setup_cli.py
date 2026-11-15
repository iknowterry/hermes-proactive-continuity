"""``hermes proactive-continuity setup | status | uninstall``

Setup changes a handful of Hermes settings through Hermes' own CLI
(``hermes config set``, ``hermes tools enable``, ``hermes cron create``), after
backing up ``config.yaml`` and ``SOUL.md``. It never touches model, provider or
fallback settings, never edits ``SOUL.md`` and never restarts the gateway.
Uninstall puts ``config.yaml`` back byte-for-byte when nothing else changed it.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Optional

PLUGIN = "proactive-continuity"
CRON_NAME = "Proactive Continuity heartbeat"
SCRIPT_NAME = "proactive_continuity_board.py"
MISSING = "__missing__"
PROTECTED_SECTIONS = ("model", "provider", "providers", "fallback", "fallback_providers",
                      "auxiliary", "delegation")

HEARTBEAT_PROMPT = (
    "Kanban follow-up. The board changed since the last check (see the change above). "
    "In at most 4 lines, report only cards that are newly done, newly blocked, or failed. "
    "If nothing needs the user's attention, reply with only [SILENT]."
)


# ---- small helpers --------------------------------------------------------

def hermes_home() -> Path:
    try:
        from hermes_constants import get_hermes_home
        return Path(get_hermes_home())
    except Exception:
        return Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes")


def _hermes_bin() -> str:
    return shutil.which("hermes") or sys.argv[0]


def _run(*argv: str) -> subprocess.CompletedProcess:
    return subprocess.run([_hermes_bin(), *argv], capture_output=True, text=True, timeout=120, check=False)


def _sha(path: Path) -> Optional[str]:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def _parse_yaml(text: str) -> Any:
    """Parse YAML with whichever library this Hermes runtime ships. Hermes itself
    depends on ruamel.yaml; PyYAML is present in some installs and absent in
    others, so it is only the fallback."""
    try:
        from ruamel.yaml import YAML
        return YAML(typ="safe", pure=True).load(text)
    except ImportError:
        import yaml
        return yaml.safe_load(text)


def _load_config(home: Path) -> dict:
    """The profile's config.yaml exactly as written (no defaults merged in), so
    "unset" can be told apart from "set to the default"."""
    path = home / "config.yaml"
    if not path.is_file():
        return {}
    data = _parse_yaml(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def _lookup(config: dict, dotted: str) -> Any:
    node: Any = config
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return MISSING
        node = node[part]
    return node


def _text(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


# ---- the plan --------------------------------------------------------------

def planned_settings(config: dict, platform: str, auto_continue: bool,
                     manual_approvals: bool = False) -> list[tuple[str, Any]]:
    """``(key, new value)`` pairs. Only settings that differ are returned."""
    display = f"display.platforms.{platform}"
    wanted: list[tuple[str, Any]] = [
        ("approvals.denial_breaker_threshold", 1),       # one denial is final
        (f"{display}.tool_progress", "off"),             # no per-command lines in chat
        (f"{display}.interim_assistant_messages", True), # "I am about to do X"
        (f"{display}.long_running_notifications", True), # the built-in Working bubble
    ]
    timeout = _lookup(config, "approvals.timeout")
    if timeout is MISSING or not isinstance(timeout, (int, float)) or timeout < 300:
        wanted.append(("approvals.timeout", 300))
    if auto_continue:
        wanted.append((f"plugins.entries.{PLUGIN}.allow_gateway_injection", True))
    if manual_approvals:
        wanted.append(("approvals.mode", "manual"))            # every approval goes to a person
    for key, _ in wanted:
        assert key.split(".")[0] not in PROTECTED_SECTIONS, key
    return [(key, value) for key, value in wanted if _lookup(config, key) != value]


def _kanban_enabled(config: dict, platform: str) -> bool:
    toolsets = _lookup(config, f"platform_toolsets.{platform}")
    return isinstance(toolsets, list) and "kanban" in toolsets


def _latest_manifest(home: Path) -> Optional[Path]:
    found = sorted((home / "backups").glob(f"{PLUGIN}-*/manifest.json"))
    for path in reversed(found):
        try:
            if json.loads(path.read_text()).get("active"):
                return path
        except Exception:
            continue
    return None


def _confirm(args, question: str) -> bool:
    if getattr(args, "yes", False):
        return True
    if not sys.stdin.isatty():
        print("Not a terminal: re-run with --yes to apply, or --dry-run to preview.")
        return False
    return input(f"{question} [y/N] ").strip().lower() in ("y", "yes")


# ---- setup -----------------------------------------------------------------

def cmd_setup(args) -> int:
    home = hermes_home()
    if _latest_manifest(home):
        print("Already set up. Run `hermes proactive-continuity uninstall` first to start over.")
        return 1
    config = _load_config(home)
    platform = args.platform
    auto_continue = not args.no_auto_continue
    settings = planned_settings(config, platform, auto_continue, args.manual_approvals)
    kanban_for = [] if args.no_kanban_tools else [p for p in dict.fromkeys(("cli", platform))
                                                   if not _kanban_enabled(config, p)]

    print(f"Hermes home: {home}\nThis will:")
    print("  - back up config.yaml and SOUL.md (SOUL.md is not edited)")
    for key, value in settings:
        old = _lookup(config, key)
        print(f"  - set {key} = {_text(value)}   (now: {'unset' if old is MISSING else _text(old)})")
    for name in kanban_for:
        print(f"  - enable the kanban toolset for {name}")
    if not args.no_cron:
        print(f"  - copy {SCRIPT_NAME} to {home / 'scripts'}")
        print(f"  - create the cron job \"{CRON_NAME}\" ({args.every}, deliver to {args.deliver}), PAUSED")
    print("It will not touch model, provider or fallback settings, and will not restart the gateway.")
    if args.dry_run:
        print("Dry run: nothing changed.")
        return 0
    if not _confirm(args, "Apply these changes?"):
        return 1

    backup = home / "backups" / f"{PLUGIN}-{time.strftime('%Y%m%d-%H%M%S')}"
    backup.mkdir(parents=True, exist_ok=False)
    manifest: dict[str, Any] = {"active": True, "platform": platform, "files": {}, "settings": [],
                                "kanban_enabled_for": [], "cron_id": None, "script": None}
    for name in ("config.yaml", "SOUL.md"):
        source = home / name
        if source.is_file():
            shutil.copy2(source, backup / name)
        manifest["files"][name] = {"before": _sha(source)}
    path = backup / "manifest.json"

    def save() -> None:
        path.write_text(json.dumps(manifest, indent=2))

    save()
    failed = False
    for key, value in settings:
        old = _lookup(config, key)
        done = _run("config", "set", key, _text(value))
        if done.returncode != 0:
            done = _run("config", "set", "--force", key, _text(value))
        if done.returncode == 0:
            manifest["settings"].append({"key": key, "old": old, "new": value})
            save()
        else:
            failed = True
            print(f"  ! could not set {key}: {(done.stderr or done.stdout).strip()[:200]}")
    for name in kanban_for:
        done = _run("tools", "enable", "kanban", "--platform", name)
        if done.returncode == 0:
            manifest["kanban_enabled_for"].append(name)
            save()
        else:
            failed = True
            print(f"  ! could not enable kanban for {name}: {(done.stderr or done.stdout).strip()[:200]}")

    if not args.no_cron:
        scripts = home / "scripts"
        scripts.mkdir(exist_ok=True)
        target = scripts / SCRIPT_NAME
        shutil.copy2(Path(__file__).parent / "scripts" / SCRIPT_NAME, target)
        manifest["script"] = str(target)
        done = _run("cron", "create", args.every, HEARTBEAT_PROMPT, "--name", CRON_NAME,
                    "--monitor-script", SCRIPT_NAME, "--deliver", args.deliver,
                    "--paused", "--paused-reason", "Installed paused by proactive-continuity")
        found = re.search(r"\b[0-9a-f]{12}\b", done.stdout or "")
        if done.returncode == 0 and found:
            manifest["cron_id"] = found.group(0)
        else:
            failed = True
            print(f"  ! cron job not confirmed: {(done.stderr or done.stdout).strip()[:300]}")
        save()

    manifest["files"]["config.yaml"]["after"] = _sha(home / "config.yaml")
    save()
    print(f"\nDone. Backup and record: {backup}")
    if manifest["cron_id"]:
        print(f"Heartbeat is paused. Turn it on with: hermes cron resume {manifest['cron_id']}")
    print("Start a new chat (/new) so the rules and tools load. Display settings may need a "
          "gateway restart, which this plugin never does for you.")
    return 1 if failed else 0


# ---- uninstall -------------------------------------------------------------

def cmd_uninstall(args) -> int:
    home = hermes_home()
    path = _latest_manifest(home)
    if not path:
        print("Nothing to undo: no active proactive-continuity setup found.")
        return 0
    manifest = json.loads(path.read_text())
    backup, config_path = path.parent, home / "config.yaml"
    whole_file = (backup / "config.yaml").is_file() and \
        _sha(config_path) == manifest["files"].get("config.yaml", {}).get("after")

    print("This will:")
    if manifest.get("cron_id"):
        print(f"  - remove cron job {manifest['cron_id']} (\"{CRON_NAME}\")")
    if manifest.get("script"):
        print(f"  - delete {manifest['script']}")
    if whole_file:
        print("  - restore config.yaml from the backup, byte for byte")
    else:
        print("  - config.yaml changed since setup, so only these keys are put back:")
        for item in manifest["settings"]:
            old = item["old"]
            print(f"      {item['key']} -> {'unset' if old == MISSING else _text(old)}")
        for name in manifest.get("kanban_enabled_for", []):
            print(f"      disable the kanban toolset for {name}")
    if args.dry_run:
        print("Dry run: nothing changed.")
        return 0
    if not _confirm(args, "Undo the setup?"):
        return 1

    failed = False
    if manifest.get("cron_id"):
        done = _run("cron", "remove", manifest["cron_id"])
        if done.returncode != 0:
            failed = True
            print(f"  ! could not remove the cron job: {(done.stderr or done.stdout).strip()[:200]}")
    if manifest.get("script"):
        Path(manifest["script"]).unlink(missing_ok=True)
    # Removing the cron job does not write config.yaml, so the comparison above still holds.
    if whole_file:
        shutil.copy2(backup / "config.yaml", config_path)
    else:
        for item in manifest["settings"]:
            done = (_run("config", "unset", item["key"]) if item["old"] == MISSING
                    else _run("config", "set", item["key"], _text(item["old"])))
            failed = failed or done.returncode != 0
        for name in manifest.get("kanban_enabled_for", []):
            failed = _run("tools", "disable", "kanban", "--platform", name).returncode != 0 or failed

    before = manifest["files"]
    same_config = _sha(config_path) == before.get("config.yaml", {}).get("before")
    same_soul = _sha(home / "SOUL.md") == before.get("SOUL.md", {}).get("before")
    print(f"config.yaml identical to backup: {'yes' if same_config else 'no'}")
    print(f"SOUL.md identical to backup:     {'yes' if same_soul else 'no (it was changed by something else)'}")
    manifest["active"] = False
    path.write_text(json.dumps(manifest, indent=2))
    print(f"Backup kept at {backup}. To remove the plugin itself: hermes plugins remove {PLUGIN}")
    return 1 if failed else 0


# ---- status ----------------------------------------------------------------

def cmd_status(args) -> int:
    home = hermes_home()
    config = _load_config(home)
    path = _latest_manifest(home)
    print(f"Hermes home: {home}")
    print(f"Setup applied: {'yes, ' + str(path.parent) if path else 'no'}")
    platform = json.loads(path.read_text()).get("platform", "telegram") if path else "telegram"
    for key in ("approvals.denial_breaker_threshold", "approvals.timeout", "approvals.cron_mode",
                f"display.platforms.{platform}.tool_progress",
                f"display.platforms.{platform}.interim_assistant_messages",
                f"display.platforms.{platform}.long_running_notifications",
                f"plugins.entries.{PLUGIN}.allow_gateway_injection"):
        value = _lookup(config, key)
        print(f"  {key}: {'(default)' if value is MISSING else _text(value)}")
    for name in dict.fromkeys(("cli", platform)):
        print(f"  kanban toolset for {name}: {'on' if _kanban_enabled(config, name) else 'not enabled'}")
    if path:
        cron_id = json.loads(path.read_text()).get("cron_id")
        print(f"  heartbeat cron job: {cron_id or 'not created'}")
    return 0


# ---- argparse wiring ---------------------------------------------------------

def build_parser(subparser) -> None:
    subs = subparser.add_subparsers(dest="pc_command")
    setup = subs.add_parser("setup", help="Back up, then apply the recommended settings")
    setup.add_argument("--platform", default="telegram", help="Messaging platform to quiet down (default: telegram)")
    setup.add_argument("--deliver", default="telegram", help="Where the heartbeat reports (default: telegram)")
    setup.add_argument("--every", default="every 30m", help="Heartbeat schedule (default: every 30m)")
    setup.add_argument("--no-auto-continue", action="store_true", help="Do not grant message injection")
    setup.add_argument("--manual-approvals", action="store_true",
                       help="Also set approvals.mode to manual, so a person answers every approval")
    setup.add_argument("--no-cron", action="store_true", help="Do not create the heartbeat job")
    setup.add_argument("--no-kanban-tools", action="store_true", help="Do not enable the kanban toolset")
    undo = subs.add_parser("uninstall", help="Undo setup and restore the backup")
    for parser in (setup, undo):
        parser.add_argument("--yes", action="store_true", help="Do not ask for confirmation")
        parser.add_argument("--dry-run", action="store_true", help="Show what would change and stop")
    subs.add_parser("status", help="Show the settings this plugin cares about")
    subparser.set_defaults(func=run)


def run(args) -> int:
    command = getattr(args, "pc_command", None)
    handler = {"setup": cmd_setup, "uninstall": cmd_uninstall, "status": cmd_status}.get(command)
    if handler is None:
        print("Usage: hermes proactive-continuity <setup|status|uninstall> [--dry-run] [--yes]")
        return 2
    return handler(args)
