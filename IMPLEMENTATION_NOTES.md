# Implementation notes

Verified against Hermes Agent **v0.23.1** (`3b421578588`), installed tree, on 2026-11-15. Every API below was read in the installed docs (`website/docs/`) or source, not taken from the live website.

## The nine behaviours

| # | Behaviour | Built-in used | New code | Why the new code is needed |
|---|---|---|---|---|
| 1 | Acknowledge before long work | `display.platforms.<p>.interim_assistant_messages`, `long_running_notifications` (set by setup) | one rule line | Nothing makes the model say what it is about to do; the bubble is generic |
| 2 | Background work, chat stays free | Kanban dispatcher (a card with an assignee runs in a worker), `delegate_task`, `/agents` | rule lines | None for the mechanism |
| 3 | Milestone-only updates | `display.platforms.<p>.tool_progress: off` (set by setup) | rule lines | Hermes has no "milestones only" mode |
| 4 | Routines, silent when unchanged | `cronjob_manage`, `[SILENT]`, `hermes cron create --monitor-script --paused` | rule line, 30-line monitor script | The script only prints board state; hashing and suppression are Hermes' |
| 5 | Durable memory | `memory` tool targets `user`/`memory`, `session_search` | rule line | None. No journal file, no store |
| 6 | Approval before risky actions, denial final | `pre_tool_call` → `{"action": "approve"}` → `tools.approval.request_tool_approval`; `approvals.denial_breaker_threshold: 1` | `riskgate.py` (classifier) | Built-in approval only covers dangerous shell commands |
| 7 | Reusable skills | `skill_manage` | rule line | None |
| 8 | Catch-up on return | `kanban_list` via `ctx.dispatch_tool`, `pre_llm_call` context, `ctx.state` | `board.py`, part of `before_turn` | No built-in digest for messaging chats |
| 9 | Report failures | `post_tool_call` (`status`, `error_message`), `transform_llm_output` | ~40 lines in `__init__.py` | Nothing forces a report when the model stays quiet |

## Where this differs from the brief, and why

**Rules go in a system prompt section, not in `SOUL.md`.** The brief asked for a marked block appended to `SOUL.md`. v0.23.1 has `ctx.register_system_prompt_section()` for exactly this: bounded (4,000 characters), named in the prompt, logged at session start, frozen per session so the prompt cache holds. Using it means the plugin never edits a user file, disabling the plugin removes the rules, and the "SOUL.md byte-identical after uninstall" criterion holds by construction. The section is rendered once per new session, so rules load after `/new`.

**The heartbeat uses `--monitor-script`, not a `wakeAgent` script.** Hermes hashes the script's output and skips the agent run when it is unchanged. That is the "no message and no model call when nothing changed" requirement with no logic of ours.

**Auto-continue is triggered by the model, not by reading the board.** The brief proposed: on turn end, if the active card has a next step and is not blocked, inject "do the next step". Two facts from the source change that:

1. A Kanban card needs an assignee, and the dispatcher runs every assigned card in its own worker process. A card is therefore background work with its own session, not a note about what the chat is doing. If the chat also continued the card's work, the work would run twice.
2. Card summaries from `kanban_list` do not carry a "next step" field.

So the rule tells the agent to end a turn with `[CONTINUE]` when steps remain that need nothing from the user. `transform_llm_output` removes the marker; `on_session_end` injects one continue message, at most `auto_continue_max_per_hour` times per session, never after an interrupted turn and never inside a cron run. This needs `plugins.entries.proactive-continuity.allow_gateway_injection`, which setup grants unless `--no-auto-continue` is passed.

For cards, the built-in already continues: a card created with `goal_mode: true` runs the same loop as `/goal` inside its worker. The plugin does not duplicate that.

**`rm -rf` is left to the built-in guard.** Escalating it from the plugin as well would show two approval prompts for one command. Users who want it can add a pattern to `risk_command_patterns`. This needs confirming on GB10 (see open items).

**Speed.** The plugin does not route sub-steps to another model; `delegate_task` already uses the user's `delegation` settings, and the brief forbids changing models. It only suggests `/new` once the conversation passes `context_warn_tokens`.

## Hook contract used

| Hook | Return used | Notes |
|---|---|---|
| `pre_llm_call` | `{"context": str}` | Appended to the user message, never the system prompt. Skipped in cron runs and for our own continue message. Cards are listed on the first turn, on return from idle, or when the board fingerprint changed, to keep context small |
| `pre_tool_call` | `{"action": "approve", "message", "rule_key"}` | `rule_key` is `proactive-continuity:<tool>`, so "always allow" is per tool. A raised exception or timeout blocks the tool (Hermes fails closed) |
| `post_tool_call` | none | Records the last failure per tool for the turn; a later success of the same tool clears it |
| `transform_llm_output` | replacement string or `None` | Strips the marker; replaces an empty reply with the failure list |
| `on_session_end` | none | Fires after every turn. Sends the capped continue message; carries unreported failures to the next turn |

Public imports only: `gateway.session_context.get_session_env` and `hermes_constants.get_hermes_home`. `hermes plugins validate` reports no private imports, no core override, security scan `safe`.

## Catalog rules checked

- Rule 3: no self-update code.
- Rule 6: `provides_hooks` matches what `register()` registers (validated).
- Rule 9: no rebinding of core; public surfaces only.
- Rule 11: no credential reads. The risk gate matches credential *paths* in tool arguments; it opens none of them.
- Rule 12: approvals are only added, never granted or bypassed. In cron the gate follows `approvals.cron_mode`.
- Rule 13: reads and writes are listed in the README.
- Rule 14: `requires_hermes: ">=0.23.1"` in `plugin.yaml` and the catalog entry.
- Rule 17: source only; no binaries or bytecode committed (`.gitignore` excludes `__pycache__`).

## What the live test changed

- **Workers are not the user.** A Kanban worker, a delegated sub-agent and a cron run all fire `pre_llm_call` in the same profile. They used to update the "last seen" state, which made the catch-up report nothing. `before_turn` now returns early for them (`HERMES_KANBAN_TASK` in the worker's environment, `parent_session_id` in the payload, `ctx.current_cron_execution()`).
- **Workers do not get the "create a card" rule.** A worker told to record multi-step work on the board could create a copy of its own card. Inside a worker the rule is replaced with "do the work yourself", and the continue marker is off (goal-mode cards have their own loop).
- **Catch-up is keyed per chat, not per session.** Every local session has its own session key, so the state is now one scope for local sessions and `platform:chat_id` for messaging chats.
- **The card rule is about duration, not step count.** The model finished a five-step task in one turn without a card, which is the right call. The rule now says "more than a few minutes, or must survive this chat ending".
- **The Kanban board is shared across profiles.** A card created in one profile is visible in all of them. The card list the plugin injects is therefore the whole board, not only this profile's cards.

## Confirmed live on v0.23.1

- `get_session_env("HERMES_SESSION_KEY")` resolves inside `on_session_end`, and `ctx.inject_message(..., session_key=...)` is admitted for a local session.
- `transform_llm_output` removes the marker from the delivered reply in `chat -q`.
- `ctx.dispatch_tool("kanban_list")` works from a `pre_llm_call` hook.
- `{"action": "approve"}` from `pre_tool_call` blocks the call when nobody can answer.
- `rm -rf` of a folder is caught by the built-in guard.

## Open items

1. **Smart approval mode: confirmed safe.** In `approvals.mode: smart` the plugin's approval card still goes to a person; the smart judge only handles Hermes' own dangerous-command findings. `setup --manual-approvals` is therefore optional.
2. **Should the injected card list be filtered to this profile's cards?** The board is shared, so on a multi-profile install the agent sees other profiles' cards too.
3. **Not built:** "notify on unusually long silence" for the heartbeat, and storing a run id on the card beyond the `idempotency_key` the rule asks for.
4. **No local model run.**
5. **`/v1/skills` HTTP 500** is left alone, as the brief asked.
