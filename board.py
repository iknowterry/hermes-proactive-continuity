"""Read the built-in Kanban board and turn it into a few lines of context.

The board is the only task store. This module never writes to it and keeps no
copy beyond a small "what did the user last see" snapshot in plugin state.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Callable, Iterable, Mapping, Optional

OPEN_STATUSES = ("running", "blocked", "review", "ready", "todo", "scheduled", "triage")
_ORDER = {status: i for i, status in enumerate(OPEN_STATUSES)}


def fetch_cards(dispatch_tool: Callable[[str, dict], str], limit: int = 50) -> Optional[list[dict]]:
    """List cards through the built-in ``kanban_list`` tool. ``None`` means the
    board could not be read (toolset off, no board yet); callers stay quiet."""
    try:
        raw = dispatch_tool("kanban_list", {"limit": limit})
        data = json.loads(raw) if isinstance(raw, str) else raw
    except Exception:
        return None
    if not isinstance(data, Mapping) or data.get("error"):
        return None
    tasks = data.get("tasks")
    if not isinstance(tasks, list):
        return None
    return [t for t in tasks if isinstance(t, Mapping)]


def open_cards(cards: Iterable[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    live = [c for c in cards if str(c.get("status", "")).lower() in _ORDER]
    return sorted(live, key=lambda c: (_ORDER[str(c.get("status")).lower()], str(c.get("id", ""))))


def snapshot(cards: Iterable[Mapping[str, Any]]) -> dict[str, str]:
    """``{card id: status}`` for every card, open or not."""
    return {str(c.get("id")): str(c.get("status", "")).lower() for c in cards if c.get("id")}


def fingerprint(snap: Mapping[str, str]) -> str:
    return hashlib.sha256(json.dumps(sorted(snap.items())).encode()).hexdigest()[:16]


def _line(card: Mapping[str, Any]) -> str:
    title = " ".join(str(card.get("title", "")).split())[:90]
    assignee = card.get("assignee")
    who = f", {assignee}" if assignee else ""
    return f"- {card.get('id')}: {title} ({card.get('status')}{who})"


def format_open(cards: list[Mapping[str, Any]], max_cards: int = 5) -> str:
    if not cards:
        return ""
    shown = cards[:max(1, max_cards)]
    lines = ["Open Kanban cards (use kanban_show for details):"] + [_line(c) for c in shown]
    if len(cards) > len(shown):
        lines.append(f"- and {len(cards) - len(shown)} more (kanban_list)")
    return "\n".join(lines)


def changes(before: Mapping[str, str], cards: Iterable[Mapping[str, Any]]) -> list[str]:
    """Lines describing what moved to done or blocked, or appeared, since ``before``."""
    out: list[str] = []
    for card in cards:
        cid, status = str(card.get("id")), str(card.get("status", "")).lower()
        old = before.get(cid)
        if old == status:
            continue
        title = " ".join(str(card.get("title", "")).split())[:90]
        if status == "done":
            out.append(f"- finished: {cid} {title}")
        elif status == "blocked":
            out.append(f"- blocked, needs you: {cid} {title}")
        elif old is None and status in _ORDER:
            out.append(f"- new: {cid} {title} ({status})")
    return out


def format_catchup(idle_minutes: float, change_lines: list[str]) -> str:
    hours = idle_minutes / 60
    gap = f"{hours:.0f} hours" if hours >= 2 else f"{idle_minutes:.0f} minutes"
    head = (f"The user is back after about {gap}. Begin your reply with a 2-4 line catch-up "
            "before answering: what finished, what is blocked, what failed.")
    if change_lines:
        body = "Board changes while they were away:\n" + "\n".join(change_lines[:8])
    else:
        body = "No Kanban card changed while they were away; say so in one line."
    tail = "If scheduled jobs matter here, check cronjob_manage(action=\"list\") for failures."
    return "\n".join((head, body, tail))
