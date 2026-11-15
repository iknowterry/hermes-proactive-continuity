"""Decide whether a tool call should go to the built-in human-approval gate.

Pure functions, no Hermes imports. ``classify`` returns a short reason when the
call needs approval and ``None`` when it does not. The plugin only ever *adds*
an approval; it never approves anything itself.
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Mapping, Optional

DEFAULT_TOOL_WORDS = (
    "send", "post", "publish", "reply", "tweet", "email", "pay", "purchase", "buy",
    "trade", "order", "transfer", "withdraw", "deploy", "delete", "remove",
)

# Tools that are internal to Hermes and safe even though a word above may match.
ALWAYS_EXEMPT = frozenset({
    "kanban_create", "kanban_comment", "kanban_complete", "kanban_block", "kanban_unblock",
    "kanban_link", "kanban_list", "kanban_show", "kanban_heartbeat", "kanban_request_review",
    "kanban_request_changes", "kanban_attach", "kanban_attach_url", "kanban_attachments",
    "memory", "session_search", "skill_manage", "skill_view", "skills_list", "todo",
    "clarify", "delegate_task", "react_to_message", "read_file", "search_files",
    "web_search", "web_extract", "cronjob_manage",
})

# An ``action`` argument with one of these values is a read, not a side effect.
READ_ONLY_ACTIONS = frozenset({
    "list", "get", "read", "show", "search", "status", "info", "describe", "fetch",
    "history", "view", "find", "check", "preview",
})

SHELL_TOOLS = frozenset({"terminal", "execute_code"})
WRITE_TOOLS = frozenset({"write_file", "patch"})

# Outward-facing or hard-to-undo shell commands. Plain destructive commands such
# as ``rm -rf`` are already caught by Hermes' own dangerous-command approval, so
# they are not repeated here (that would ask the user twice).
COMMAND_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"\bgit\s+push\b", "pushes commits to a remote"),
    (r"\bgh\s+(pr\s+(create|merge|close)|release\s+create|issue\s+(create|close|comment)|repo\s+delete)\b",
     "changes something on GitHub"),
    (r"\b(npm|pnpm|yarn)\s+publish\b|\btwine\s+upload\b|\bcargo\s+publish\b|\bgem\s+push\b",
     "publishes a package"),
    (r"\bdocker\s+push\b", "pushes a container image"),
    (r"\b(vercel|netlify|wrangler|flyctl|fly|firebase|railway)\b.*\b(deploy|publish|--prod)\b",
     "deploys to a hosting service"),
    (r"\bkubectl\s+(apply|delete|rollout|scale)\b|\bhelm\s+(install|upgrade|uninstall)\b",
     "changes a Kubernetes cluster"),
    (r"\bterraform\s+(apply|destroy)\b|\bpulumi\s+(up|destroy)\b", "changes cloud infrastructure"),
    (r"\b(sendmail|mailx|mutt|msmtp)\b|\bmail\s+-s\b", "sends email"),
    (r"\bcurl\b.*\s(-X|--request)\s*(POST|PUT|PATCH|DELETE)\b|\bcurl\b.*\s(-d|--data\S*|-F|--form)\s",
     "sends data to a web service"),
    (r"\bhermes\s+(config\s+(set|unset)|plugins\s+(install|enable|remove)|update|gateway\s+(restart|stop))\b",
     "changes Hermes itself"),
    (r"\b(systemctl|launchctl)\s+(start|stop|restart|enable|disable|load|unload|bootout|bootstrap)\b",
     "changes a system service"),
    (r"\bcrontab\s+(-e|-r|\S+\.\S+)", "edits the system crontab"),
)

# Paths whose edits change the system or hold credentials. This only matches a
# path string the model passed to a write tool; nothing here reads these files.
_DOTENV = "." + "env"
_SENSITIVE_PARTS = (
    "etc/", ".ssh/", ".aws/", ".gnupg/", ".kube/", "Library/LaunchAgents/", "systemd/",
    ".hermes/config.yaml", ".hermes/SOUL.md", ".hermes/auth.json", ".hermes/" + _DOTENV,
)
_SENSITIVE_NAMES = (".zshrc", ".bashrc", ".bash_profile", ".profile", ".gitconfig")
SENSITIVE_PATH = re.compile(
    r"(^|/)(" + "|".join(re.escape(part) for part in _SENSITIVE_PARTS)
    + "|" + re.escape(_DOTENV) + r"(\.|$)"
    + "|(" + "|".join(re.escape(name) for name in _SENSITIVE_NAMES) + r")$)"
)

_SPLIT = re.compile(r"[^a-z0-9]+")


def _words(name: str) -> list[str]:
    return [w for w in _SPLIT.split(name.lower()) if w]


def _first_text(args: Mapping[str, Any], keys: Iterable[str]) -> str:
    for key in keys:
        value = args.get(key)
        if isinstance(value, str) and value:
            return value
    return ""


def classify(
    tool_name: str,
    args: Optional[Mapping[str, Any]],
    *,
    tool_words: Iterable[str] = DEFAULT_TOOL_WORDS,
    exempt_tools: Iterable[str] = (),
    extra_command_patterns: Iterable[str] = (),
) -> Optional[str]:
    """Return why this call needs a human yes, or ``None`` to let it through."""
    name = (tool_name or "").strip()
    args = args if isinstance(args, Mapping) else {}
    if not name or name in ALWAYS_EXEMPT or name in set(exempt_tools):
        return None

    if name in SHELL_TOOLS:
        command = _first_text(args, ("command", "code", "script", "cmd"))
        preview = " ".join(command.split())
        preview = preview if len(preview) <= 140 else preview[:137] + "..."
        for pattern, why in COMMAND_PATTERNS:
            if re.search(pattern, command, re.IGNORECASE | re.DOTALL):
                return f"the command {why}: {preview}"
        for pattern in extra_command_patterns:
            try:
                if re.search(str(pattern), command, re.IGNORECASE | re.DOTALL):
                    return f"the command matches a pattern you asked to confirm: {preview}"
            except re.error:
                continue
        return None

    if name in WRITE_TOOLS:
        path = _first_text(args, ("path", "file_path", "filename", "target"))
        if path and SENSITIVE_PATH.search(path.replace("\\", "/")):
            return f"it edits a system or credential file ({path})"
        return None

    action = args.get("action")
    if isinstance(action, str) and action.lower() in READ_ONLY_ACTIONS:
        return None

    wanted = {str(w).lower() for w in tool_words}
    hit = next((w for w in _words(name) if w in wanted), None)
    if hit is None and isinstance(action, str):
        hit = next((w for w in _words(action) if w in wanted), None)
    if hit:
        return f"it would {hit} something outside this chat"
    return None


def directive(tool_name: str, reason: str) -> dict:
    """The ``pre_tool_call`` return that hands the call to Hermes' approval gate."""
    return {
        "action": "approve",
        "message": f"proactive-continuity: {reason}",
        "rule_key": f"proactive-continuity:{tool_name}",
    }
