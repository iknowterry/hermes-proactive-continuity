#!/usr/bin/env python3
"""Monitor script for the proactive-continuity heartbeat cron job.

Prints one stable line per Kanban card (id and status, no timestamps). Hermes'
built-in ``--monitor-script`` hashes this output each tick and only wakes the
agent when it changed, so a quiet board costs no model call and sends nothing.
"""

import json
import os
import shutil
import subprocess
import sys


def main() -> int:
    hermes = shutil.which("hermes") or os.path.expanduser("~/.local/bin/hermes")
    try:
        done = subprocess.run([hermes, "kanban", "list", "--json"], capture_output=True,
                              text=True, timeout=45, check=False)
        data = json.loads(done.stdout or "[]")
    except Exception:
        print("board: unavailable")
        return 0
    tasks = data.get("tasks", []) if isinstance(data, dict) else data
    rows = sorted(f"{t.get('id')} {t.get('status')}" for t in tasks if isinstance(t, dict) and t.get("id"))
    print("\n".join(rows) if rows else "board: empty")
    return 0


if __name__ == "__main__":
    sys.exit(main())
