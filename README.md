# proactive-continuity

A Hermes plugin that makes your agent keep working, remember what is pending, and stay quiet until there is something to say.

Without it, a typical agent finishes one step, reports "what's left", and waits for you to say "continue". With it, the agent:

- puts longer work on the Kanban board, where a background worker finishes it even after the chat ends
- carries on to the next step instead of asking "what next?"
- sees its open cards at the start of every new session
- speaks for results, blockers and changed plans, not for every command
- asks for approval before sending, posting, spending, trading, deploying or editing system config, and treats a "no" as final
- gives a short catch-up when you come back after a long gap
- names the tool and the error when something fails, instead of going silent

It is a thin layer over features Hermes already has (Kanban, cron, approvals, memory, skills, delegation). It adds no tool, no scheduler, no memory store and no approval screen of its own, and it works with any model.

Requires Hermes 0.23.1 or newer.

## Install

```bash
hermes plugins install proactive-continuity --enable
```

```bash
hermes proactive-continuity setup
```

The first command installs and enables the plugin. Until the plugin is listed in the Hermes catalog, install it from the repository instead: `hermes plugins install iknowterry/hermes-proactive-continuity --enable`.

The second command shows every change it is about to make and asks before applying. Add `--dry-run` to only look. Then start a new chat (`/new`) so the rules and tools load.

### What setup changes

Setup backs up `config.yaml` and `SOUL.md` to `~/.hermes/backups/proactive-continuity-<time>/` first, then uses Hermes' own commands to:

| Change | Why |
|---|---|
| `approvals.denial_breaker_threshold: 1` | one denial ends the attempt (default is 3) |
| `approvals.timeout: 300` (only if lower) | time to answer an approval on your phone |
| `display.platforms.telegram.tool_progress: off` | no per-command lines in chat |
| `display.platforms.telegram.interim_assistant_messages: true` | the agent can say what it is about to do |
| `display.platforms.telegram.long_running_notifications: true` | the built-in "Working" bubble |
| `plugins.entries.proactive-continuity.allow_gateway_injection: true` | lets the plugin send "continue with the next step" |
| enable the `kanban` toolset for `cli` and the platform | the agent can create and read cards |
| a cron job "Proactive Continuity heartbeat", created **paused** | reports cards that finished or got blocked |

Setup never touches model, provider, fallback or auxiliary settings. It does not edit `SOUL.md`; the rules are added through Hermes' system prompt section API instead. It does not restart the gateway. Some display settings only take effect after a gateway restart, which is yours to do.

Options: `--platform discord` (default `telegram`), `--deliver <target>`, `--every "every 1h"`, `--manual-approvals` (set `approvals.mode: manual`; not needed for this plugin's own approvals, which reach a person in `smart` mode too), `--no-auto-continue`, `--no-cron`, `--no-kanban-tools`, `--yes`.

### Turn on the heartbeat

The heartbeat is installed paused. Setup prints its id:

```bash
hermes cron resume <id>
```

Every 30 minutes Hermes runs a small script that prints the board state. Hermes only wakes the agent when that output changed, so a quiet board costs no model call and sends no message.

## Settings

Set these under `plugins.entries.proactive-continuity.settings` in `config.yaml`, or in the Desktop Plugins tab.

| Key | Default | Meaning |
|---|---|---|
| `rules_enabled` | `true` | add the working rules to each new session |
| `inject_open_cards` | `true` | list open cards at session start and when the board changes |
| `max_cards` | `5` | how many cards to list |
| `catchup_idle_minutes` | `180` | silence after which the next message gets a catch-up; `0` is off |
| `auto_continue` | `true` | continue when the agent ends a turn with steps remaining |
| `auto_continue_max_per_hour` | `6` | hard cap per session |
| `risk_gate` | `true` | ask before risky tool calls |
| `risk_tool_words` | send, post, publish, reply, tweet, email, pay, purchase, buy, trade, order, transfer, withdraw, deploy, delete, remove | a tool whose name contains one of these needs approval |
| `risk_exempt_tools` | `[]` | tools the gate never asks about |
| `risk_command_patterns` | `[]` | extra regular expressions for terminal commands |
| `report_failures` | `true` | make sure a failed tool is named to the user |
| `context_warn_tokens` | `60000` | suggest `/new` past this size; `0` is off |

`/continuity` in any chat shows the open cards and which parts are on. `hermes proactive-continuity status` shows the settings setup manages.

## How the risk gate works

Before every tool call the plugin checks the tool name and arguments. If the call looks like it sends, posts, spends, trades, deploys, or edits system or credential files, the plugin hands it to Hermes' own approval gate. You get the same approval card as for a dangerous shell command, with the same choices and the same timeout. The plugin can only add an approval; it cannot grant one.

For shell commands it asks about `git push`, package publishing, hosting deploys, `kubectl`/`terraform` changes, sending mail, `curl` requests that send data, changes to Hermes itself, and system services. Plain destructive commands such as `rm -rf` are already covered by Hermes' built-in dangerous-command approval, so the plugin does not ask a second time.

In scheduled jobs the gate follows `approvals.cron_mode` (default `deny`).

## Uninstall

```bash
hermes proactive-continuity uninstall
```

```bash
hermes plugins remove proactive-continuity
```

The first command removes the heartbeat job and its script and restores `config.yaml` from the backup, byte for byte. If you changed `config.yaml` yourself after setup, it keeps your changes and puts back only the keys setup changed; in that case the saved toolset list stays in the file. `SOUL.md` is never edited, and uninstall confirms it still matches the backup.

## Model note

The plugin does not choose or change models. It was built to work with any model that can call tools. Small local models follow multi-step rules less reliably: they may skip creating a card or forget the continue marker. For the main agent, use a model that is good at tool calls.

For long tasks you start yourself, the built-in `/goal <text>` is still the strongest option: a judge model checks each turn and keeps the agent going until the goal is met. This plugin covers the everyday case where you did not think to set a goal.

## What the plugin reads and writes

- Reads the Kanban board through the built-in `kanban_list` tool.
- Reads `config.yaml` during setup, status and uninstall. It does not read `.env`, `auth.json` or any credential.
- Writes its own small state (when each chat was last active and the board snapshot at that time) under `~/.hermes/plugin-data/`.
- Setup and uninstall run `hermes config`, `hermes tools` and `hermes cron` commands, and copy one script into `~/.hermes/scripts/`. The heartbeat script runs `hermes kanban list --json`.
- No network calls, no telemetry, no background process.

## Limits

- Under `plugins.isolation: host`, the plugin cannot see which chat a turn belongs to, so automatic continue does not work on messaging platforms in that mode.
- The Kanban board is shared by all profiles, so the open-card list includes cards from other profiles.
- On platforms that stream replies, the `[CONTINUE]` marker may be visible at the end of a message before the next step starts.
- The catch-up reports Kanban changes. For failed scheduled jobs it tells the agent to check `cronjob_manage`; it does not read cron history itself.

## Development

```bash
uv run --no-project --with pytest --with pyyaml python -m pytest -q
```

```bash
hermes plugins validate .
```

Design decisions and the mapping to Hermes APIs are in [IMPLEMENTATION_NOTES.md](IMPLEMENTATION_NOTES.md). Test results are in [TEST_LOG.md](TEST_LOG.md).

## License

MIT
