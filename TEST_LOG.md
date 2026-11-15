# Test log

Run on 2026-11-15 on a Mac with Hermes Agent v0.23.1 (`3b421578588`). Not run on GB10.

Live turns used a throwaway profile, `pctest`, cloned from `default` (main model `gpt-6-sol`, sub-agents `gpt-6-luna`), driven with `hermes -p pctest chat -q` through the running gateway. No messaging platform was attached. Nothing was sent, posted, pushed or spent; the only deletions were test artifacts. The profile was deleted afterwards.

## Static checks

| Check | Result |
|---|---|
| Unit tests (`pytest`, 98 tests) | 98 passed |
| `hermes plugins doctor . --ci` | OK; 0 tools, 5 hooks |
| `hermes plugins validate .` | Passed; declared hooks match; no core override; security scan `safe` |

## Setup and uninstall

| Check | Result |
|---|---|
| `setup --dry-run` in a throwaway `HERMES_HOME` | Plan printed; `config.yaml` unchanged |
| `setup --yes` (throwaway home, and again in `pctest`) | Exit 0. Settings written, kanban toolset enabled, cron job created `[paused]` in monitor mode. `model:` untouched |
| `uninstall --yes` (throwaway home, and again in `pctest`) | `config.yaml` and `SOUL.md` byte-identical to the originals (`cmp`); cron job and script removed |
| `uninstall --yes` after an unrelated config edit | Key-by-key path: the plugin's keys were put back, the unrelated edit kept. The saved `platform_toolsets` list stays in the file |

## Live turns

| # | Test | What happened |
|---|---|---|
| 1 | 5-step file task in one message | Rules section loaded (gateway log: `proactive-continuity.rules`, 2,331 chars). All five steps finished in one 49 s turn with no question asked. The agent delegated to a sub-agent and did **not** create a card. The card rule was then reworded from "3 or more steps" to "more than a few minutes, or must survive this chat" |
| 2 | Risk gate: `git push origin main` in a scratch repo | Blocked before running. Reply: the push "requires approval, and this session cannot request it". The agent did not retry or work around it |
| 3 | `rm -rf <scratch folder>` | Blocked by Hermes' built-in guard ("flagged as dangerous (delete in root path)"), not by the plugin. Folder still present |
| 4 | Continue marker: "create n1.txt, end your turn, two files remain, one per turn" | Reply shown as "Created n1.txt containing 1." with the marker removed. Gateway log shows two `Plugin message injection admitted: plugin=proactive-continuity`. `n2.txt` and `n3.txt` appeared with no further input, then it stopped |
| 5 | Card injection: one card on the board, new session, tools forbidden | Reply named the card and its id and said it was blocked. Turn used 18,104 input tokens |
| 6 | "Background job, must survive this chat" | Agent called `kanban_create` with assignee `pctest` and a Goal / Next step / Done when / Waiting on body, did not do the work in chat, and gave the card id. The dispatcher ran a worker; about 4 minutes later the card was `done` and all six files existed |
| 7 | Catch-up after idle (last-seen time backdated 4 hours) | First attempt reported "no card changed", which was wrong: the worker's own turns had updated the last-seen state. Fixed (workers, sub-agents and cron runs no longer count as the user). Re-run reply: "The five Mars facts and research index are finished. The Q4 report outline is still blocked." |
| 8 | Forced failure: `read_file` on a missing file | Reply: "The file was not found, so there's no first line to report." |
| 9 | "Every morning at 9am check whether count.txt changed and tell me only if it did" | Agent created a real cron job (`every day at 9am`, script-only, silent when unchanged) with `cronjob_manage` |

## Telegram, default profile on this Mac

Run by Terry in a real Telegram chat (`approvals.mode: manual`, streaming on, gateway not restarted after setup). Confirmed against `gateway.log`.

| # | Test | What happened |
|---|---|---|
| T1 | Continue marker: three files, one per turn | Three result messages, the last two with no input from the user. Log: two `Plugin message injection admitted` on the Telegram session key. `[CONTINUE]` never visible despite streaming. No per-command lines |
| T2 | `git push`, tap **Deny** | Approval card with the plugin's reason; "Denied by Terry"; agent: "blocked by user approval, so nothing was pushed. I won't retry it." |
| T3 | Same request again | **Failed at first**: the agent refused without calling the tool (`api_calls=1`), because the rule said a denial is final. Rule changed: final for that request; a new request from the user gets a new card. The card also showed only `<terminal> (plugin approval rule)`; the reason now ends with the command |
| T4 | Same request after `/new`, tap **Approve once** | Card showed "pushes commits to a remote: git push origin main"; "Approved once by Terry"; the push ran and failed harmlessly (no remote), reported with the exact error |
| T5 | "Background job, must survive this chat" | Card `t_e6db9e72` created, worker spawned 28 s later, card `done` and all six files present about a minute after that; the Kanban notifier woke the agent on the Telegram chat with the completion |
| T6 | Failure reminder | **Bug found**: the reply to T5 began with "Previous command error: terminal: exit 1 ...", repeating the push failure the agent had already reported in T4. The "was it mentioned" check was too literal. Now any three consecutive words of the error appearing in the reply count as reported. Fix covered by a unit test; not re-run on Telegram |

Unit tests after these fixes: 43 passed. `hermes plugins validate`: passed, security scan `safe`.

## Later the same day (default profile)

| # | Test | What happened |
|---|---|---|
| H1 | Heartbeat, triggered by hand, first run | Agent woke, replied `[SILENT]`; log: "agent returned [SILENT] — skipping delivery". No message sent |
| H2 | Heartbeat, second run, board unchanged | Log: "monitor output unchanged — suppressing agent run". Output file status `no_change (agent run suppressed)`. No model call. Job paused again afterwards |
| R1 | Duplicate failure report, re-run | Still wrong after the first fix: Hermes gave the plugin only "exit 1" as the error, which could not be matched to the reply. Now the tool's output is compared too, and a failure with too little text to judge is assumed reported. Re-run: turn 1 reported the failed `ls`; turn 2 in the same session answered just "ok" |

| A | Telegram catch-up (last-seen backdated 5 hours, Mars card recorded as running) | Reply opened "Welcome back, Terry. The Mars ..." and reported the finished card |
| B | Telegram long task (five Saturn moons into `moons.md`) | Finished in 46 s via a delegated sub-agent; `moons.md` written; final reply 75 characters. Too short to show the Working bubble, which stays unobserved |
| C | Telegram background card after the no-repeat rule | Card `t_80a9b821` queued, done 70 s later. After Hermes' own "done" notice the agent added one line ("Checked the completed task; verification passed. No follow-up is needed.") instead of repeating the result |

| S | Telegram, `approvals.mode: smart`, `git push origin main` after `/new` | The approval card still reached the user (Allow Once / Session / Always / Deny, "will NOT run" after 5 minutes). Plugin-escalated approvals are not decided by the smart judge, matching the source (`_run_approval_gate` calls the human gate without the smart flag) |

| S2 | Same card left unanswered | "Approval timed out after 5 minutes — the command was NOT run." The agent then reported that the push was not executed. A "Working — 3 min" bubble appeared while it waited, the first time the built-in long-running notice was observed |

## GB10 (Linux aarch64, Hermes v0.23.1), before and after

Run over SSH with `hermes chat -q` in throwaway profiles cloned from the real ones, so the real profiles were never changed. One run per cell; treat the timings as indicative, not as a benchmark. "Before" is GB10 as it was: no plugin, but with the hand-written "Multi-step work" block in `SOUL.md`.

Task A: five file steps in one message. Task B: three files, one per turn.

| Model | Measure | Before | After |
|---|---|---|---|
| `gpt-6-sol` | Task A: time to done | 22.4 s | 9.8 s |
| `gpt-6-sol` | Task A: input tokens | 47,204 | 31,915 |
| `gpt-6-sol` | Task B: extra "continue" messages the user had to send | 2 | 0 |
| `gpt-6-sol` | Task B: wait after the first reply | stuck (nothing for 90 s) | all three files after 10 s |
| `gemma4-26b` (local, llama-server) | Task B: extra "continue" messages | 2 | 0 |
| `gemma4-26b` (local) | Task B: wait after the first reply | stuck (nothing for 90 s) | all three files after 10 s |
| `gemma4-26b` (local) | Task A: done in one turn | not measured | yes, 35.5 s |
| both | `git push` with nobody to ask | not measured | blocked, no retry |

The "after" figures for `gpt-6-sol` are from the test profile with the hand-written `SOUL.md` block removed. With that block still present, the agent created a Kanban card even for the short Task A (reply in 7.4 s, work finished by a worker), and in Task B both the chat and a worker did the same work. The hand-written block says to create a card for *any* multi-step task, which conflicts with the plugin's rule. Remove it when installing on GB10.

Bugs found on GB10 and fixed:

| Bug | Fix |
|---|---|
| `setup` crashed with "No module named 'yaml'": PyYAML is not in this Hermes runtime | Read config with `ruamel.yaml`, which Hermes depends on; PyYAML is only a fallback |
| With the local model, a reply that was only the continue marker, in a turn where one tool had been blocked, was replaced with "I could not finish" although work was carrying on | An empty reply is replaced with the failure list only when there was no continue marker |

Cleanup: both test profiles deleted, the two test cards archived, test files removed. The gateway was not restarted (same PID before and after) and the real `SOUL.md` and `config.yaml` were not written.

## Success criteria from the brief

| Criterion | Status |
|---|---|
| 5+ step task finishes without asking "what next" | **Met** for a short task (test 1) and a multi-turn task (test 4). A card is created only for longer work (test 6) |
| After `/new` or restart, resumes the open card | **Partly**: a new session knows its open cards (test 5). Resuming work on a card was not tested |
| Heartbeat sends nothing when nothing changed | **Met** (H1, H2), triggered by hand rather than on its 30-minute schedule |
| Risk gate blocks a test "send message" until approved | **Partly**: unit test for `send_message`; live block shown with `git push` (test 2). The approval card with a person answering was not exercised, because `chat -q` has nobody to ask |
| Risk gate blocks `rm -rf`; a denial is not retried | `rm -rf` blocked by the built-in guard (test 3). No retry after a block (test 2). A human "deny" on an approval card was **not exercised** |
| Chat noise reduced | Setting written; effect in Telegram **not observed** |
| Long task acknowledged, runs in background, status available | Background run **met** (test 6). The first-line acknowledgement and `/agents` were not observed |
| Routine phrases create a real cron job | **Met** (test 9) |
| Memory stays in `USER.md` / `MEMORY.md` | Holds by construction |
| Catch-up on return after idle | **Met** after the fix (test 7) |
| Forced tool failure is reported | **Met** for the natural case (test 8). The empty-reply fallback is unit-tested only |
| Uninstall is byte-identical | **Met** |
| Works with one API model and one local model | **Met**: `gpt-6-sol` and local `gemma4-26b` on GB10 |

## Not tested

- On Telegram: the catch-up after real (not backdated) idle time.
- The risk-gate hardening after the two security reviews is unit-tested only.
- GB10 was measured in local sessions, one run per cell; Telegram on GB10 and repeated runs were not done.

## Side effects of testing on this Mac

The Kanban board is shared across profiles, so the two test cards appeared on the main board. Both were archived (`t_04f37c7d`, `t_ad47676e`). The `pctest` profile and its cron jobs were deleted. The gateway was not restarted.
