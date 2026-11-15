"""Decide whether a tool call should go to the built-in human-approval gate.

This is a best-effort list of recognisable risky calls, not a sandbox: a command
can be written in ways no pattern list catches. It exists to put a person in
front of the common outward-facing actions, on top of Hermes' own guards.

Pure functions, no Hermes imports. ``classify`` returns a short reason when the
call needs approval and ``None`` when it does not. The plugin only ever *adds*
an approval; it never approves anything itself.
"""

from __future__ import annotations

import os
import re
import shlex
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
    (r"\bgh\s+api\b.*(\s(-X|--method)[\s=]*(POST|PUT|PATCH|DELETE)\b|\s(-f|-F|--field|--raw-field|--input)[\s=])",
     "changes something on GitHub"),
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
    (r"\bcurl\b.*\s(-X|--request)[\s=]*(POST|PUT|PATCH|DELETE)\b"
     r"|\bcurl\b.*\s(-d|--data\S*|-F|--form\S*|--json|-T|--upload-file)[\s=]"
     r"|\bwget\b.*\s(--post-data|--post-file|--body-data|--body-file|--method)[\s=]",
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
# git global options that take a separate value: ``git -C dir push``.
_GIT_VALUE_OPTS = frozenset({"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--exec-path",
                             "--super-prefix", "--config-env", "--attr-source"})
_SHELL_BREAK = re.compile(r"\|\||&&|[;|&\n(){}]|`")


def _git_pushes(command: str) -> bool:
    """True when any git invocation in the command has ``push`` as its subcommand.
    Global options and their values are skipped, so ``git -C x push`` counts and
    ``git stash push`` or ``git log --grep push`` do not. If a piece cannot be
    parsed, any git call that mentions push counts."""
    for piece in _SHELL_BREAK.split(command):
        if "git" not in piece:
            continue
        try:
            words = shlex.split(piece)
        except ValueError:
            if re.search(r"\bgit\b.*\bpush\b", piece, re.IGNORECASE | re.DOTALL):
                return True
            continue
        for i, word in enumerate(words):
            if os.path.basename(word).lower() not in ("git", "git.exe"):
                continue
            rest = words[i + 1:]
            while rest and rest[0].startswith("-"):
                rest = rest[2:] if rest[0] in _GIT_VALUE_OPTS else rest[1:]
            if rest and rest[0].lower() == "push":
                return True
    return False


SENSITIVE_PATH = re.compile(
    r"(^|/)(" + "|".join(re.escape(part) for part in _SENSITIVE_PARTS)
    + "|" + re.escape(_DOTENV) + r"(\.|$)"
    + "|(" + "|".join(re.escape(name) for name in _SENSITIVE_NAMES) + r")$)",
    re.IGNORECASE,
)
_PATCH_TARGET = re.compile(
    r"^(?:\*\*\* (?:Update|Add|Delete) File:|\*\*\* Move to(?: File)?:|\+\+\+|---|rename to|rename from|copy to)"
    r"[ \t]+(\S[^\t\n]*?)[ \t]*(?:\t.*)?$",
    re.MULTILINE,
)
_PATCH_HEADER = re.compile(r"^\*\*\* (?!Begin Patch|End Patch|End of File)(.*)$", re.MULTILINE)
_UNREADABLE = "\0unreadable-patch-header"
_PATH_KEYS = ("path", "file_path", "filename", "target", "file", "dest", "destination")
_PATCH_KEYS = ("patch", "diff", "content", "input", "changes")


def _sensitive(path: str) -> bool:
    """Match the path as written and as the filesystem would resolve it."""
    raw = path.strip().replace("\\", "/")
    if raw.startswith(("a/", "b/")):
        raw = raw[2:]
    forms = {raw}
    try:
        expanded = os.path.expanduser(raw)
        forms.add(os.path.normpath(expanded).replace("\\", "/"))
        forms.add(os.path.realpath(expanded).replace("\\", "/"))
    except (OSError, ValueError):
        pass
    return any(SENSITIVE_PATH.search(form) for form in forms)


def _write_targets(args: Mapping[str, Any]) -> list[str]:
    targets = [args[key] for key in _PATH_KEYS if isinstance(args.get(key), str) and args[key]]
    for key in _PATCH_KEYS:
        body = args.get(key)
        if isinstance(body, str):
            found = [m for m in _PATCH_TARGET.findall(body) if m != "/dev/null"]
            targets += found
            # A "*** ..." header this code cannot read names a file it cannot check: ask.
            if len(_PATCH_HEADER.findall(body)) > sum(1 for m in _PATCH_TARGET.finditer(body)
                                                       if m.group(0).startswith("***")):
                targets.append(_UNREADABLE)
    return targets

_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_SPLIT = re.compile(r"[^a-z0-9]+")


def _words(name: str) -> list[str]:
    """``sendEmail``, ``send-email`` and ``send_email`` all give ``[send, email]``."""
    return [w for w in _SPLIT.split(_CAMEL.sub("_", name).lower()) if w]


def _risky_word(text: str, wanted: set[str]) -> Optional[str]:
    """A wanted word in ``text``, also matching short endings (sends, posted,
    deleting) but not longer unrelated words (postgres, reorder)."""
    for word in _words(text):
        for want in wanted:
            if word == want or (word.startswith(want) and len(word) - len(want) <= 3):
                return want
    return None


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
        if _git_pushes(command):
            return f"the command pushes commits to a remote: {preview}"
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
        for path in _write_targets(args):
            if path == _UNREADABLE:
                return "its patch names a file in a form that could not be checked"
            if _sensitive(path):
                return f"it edits a system or credential file ({path[:140]})"
        return None

    wanted = {str(w).lower() for w in tool_words}
    hit = _risky_word(name, wanted)
    if hit:
        # The tool's own name says it acts; an ``action`` argument cannot talk it out of that.
        return f"it would {hit} something outside this chat"
    action = args.get("action")
    if isinstance(action, str):
        if action.lower() in READ_ONLY_ACTIONS:
            return None
        hit = _risky_word(action, wanted)
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
