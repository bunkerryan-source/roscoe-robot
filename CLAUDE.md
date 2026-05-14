# roscoe-robot — Claude Project Notes

Personal capture-and-route system. Single ingest point (Telegram bot "Roscoe") collects text/photos/links/voice during the day; a batch processor classifies, enriches, and files each item to Obsidian (Dropbox), Todoist, and Supabase. Target cost: $5–8/month.

## Where to look first

Read these in order before doing any non-trivial work:

1. [docs/superpowers/specs/2026-05-05-personal-os-design.md](docs/superpowers/specs/2026-05-05-personal-os-design.md) — canonical design (architecture, data model, session roadmap).
2. [docs/superpowers/plans/](docs/superpowers/plans/) — per-session implementation plans (Sessions 2–6 shipped; Sessions 6b, 6c, 7 drafted).
3. Your memory directory for this project — droplet SSH, Supabase ID, Todoist endpoint, classifier quirks, env-deploy gotchas.

Don't duplicate the spec here; just pointer.

## Current state — 2026-05-14

**Sessions 2–6 shipped.** Capture pipe, process pipe, triage, cron autonomy + cost cap, and X-tweet video storage are all in production. The bot accepts captures via Telegram webhook, processes pending items on three daily cron triggers (06:30 / 12:00 / 21:00 LA) plus on-demand `/process`, and routes results to Obsidian + Todoist + Supabase. Daily cost cap (default 200¢ via `DAILY_COST_CAP_CENTS`) gates autonomy; `/process` bypasses it. Kill switch: `DAILY_COST_CAP_CENTS=0` + restart.

**Live incident — 2026-05-14: 4K-video OOM crash loop.** A 3324×2160 MP4 from an X tweet (`viktoroddy/status/2054658946569535735`) blew the droplet's RAM during the Session 6 video download path (`_download_video_to_dropbox` reads `response.content` whole into memory before pushing to Dropbox). Linux OOM-killed the uvicorn process during the GET, systemd auto-restarted, the same poison item (oldest pending, ORDER BY created_at ASC) re-triggered the crash on the next cron and on `/process`. Result: missed briefs from 2026-05-13 21:00 LA onward, captures kept landing in Supabase but no processing happened, and `/process` appeared unresponsive (👍 ack arrived; the final summary message never did because the background batch crashed). **Crash loop, NOT a cost loop** — OOM happened during the video GET, before any `api.anthropic.com` POST, so zero Anthropic spend on the poison item. Triage taken:
- Poison item `05beffee-b1aa-4f80-92cd-8fc68011797e` quarantined via Supabase MCP: `status='failed'`, error explains the OOM.
- Kill switch armed on droplet: `DAILY_COST_CAP_CENTS=0`, service restarted. **Autonomy is currently paused.** Briefs still arrive (summary path runs even when batch skips) but no batches process.
- Captures still work; 11 other pending items in queue, untouched.
- **Do not run `/process` until Session 6b ships** — it bypasses the cap and will OOM-crash on the next long video in the queue.

**Discovered alongside the incident — Whisper voice transcription is silently broken in production.** Only 2 voice memos ever sent (2026-05-06 and 2026-05-13); both have `raw_text=null` despite `transcribe_voice` being wired in code. The 2026-05-13 voice memo was filed under `project=acute` with a hallucinated summary — `enrich_item`'s voice branch swallows the underlying Whisper exception (silently logs WARNING and returns the null `raw_text`), then the classifier invents a summary from an empty payload. Root cause on the droplet not yet identified (likely env-var, file-extension, or quota).

**Two plans drafted today, both awaiting future-session execution:**
- **Session 6b** — Long-video tutorial routing. Tweet videos >60s skip the Dropbox download entirely, get hardcoded `type='tutorial'` + `project='claude-build'` classification (no Haiku call), Obsidian note + Todoist task. Fixes the OOM root cause. 12 tasks, ~14 new tests. Plan: [docs/superpowers/plans/2026-05-14-session-6b-long-video-tutorials.md](docs/superpowers/plans/2026-05-14-session-6b-long-video-tutorials.md). Ship this first — it's the gate to re-enabling autonomy.
- **Session 6c** — Voice transcription fix. Surfaces transcription failures into `items.error` + `needs_review` (stops the hallucination), then diagnoses + fixes the underlying Whisper failure on the droplet. 9 tasks, ~7 new tests. Plan: [docs/superpowers/plans/2026-05-14-session-6c-voice-transcription-fix.md](docs/superpowers/plans/2026-05-14-session-6c-voice-transcription-fix.md). Ship after 6b.

**Next sessions (per spec):**
- **Session 6b** — Long-video tutorial routing (drafted). **Required before re-enabling autonomy.**
- **Session 6c** — Voice transcription fix (drafted).
- **Session 7** — Weekly digest (Sonnet 4.6), monthly rule consolidation, research-thread clustering. Plan drafted at [docs/superpowers/plans/2026-05-12-session-7-weekly-digest-rules.md](docs/superpowers/plans/2026-05-12-session-7-weekly-digest-rules.md); two open decisions deferred to implementation start (rule-proposal UX, vault rules.md repo sync).
- **Session 8** — `/find` polish, OA-wiki feeders, multi-source ingest.

**Related sibling project:** [../design-dashboard/](../design-dashboard/) — Pinterest-style web viewer for design images this bot captures. Read-only consumer of this project's Supabase + Dropbox. Independent code path / deploy; can be built in parallel.

## Operator runbook (how Ryan uses the bot)

- **Capture:** forward/send anything to the Telegram bot. Bot acks with 👍 and writes to Supabase `items` (status=pending). No Claude call at this stage.
- **Process:** in Telegram, send `/process`. Bot acks 👍, runs the batch in the background (≤50 items at a time), replies with `processed N · $X.XX · M needs review`. **`/process` ignores the daily cost cap** — it's the operator escape hatch.
- **Autonomy:** three cron jobs run daily at 06:30 / 12:00 / 21:00 LA. Morning + evening process pending items then send the daily brief. Noon is silent — only speaks if `items_failed > 0` or the daily cap got hit during the run. If today's cumulative spend already exceeds `DAILY_COST_CAP_CENTS` (default 200¢) when a cron fires, the batch skips and only the summary sends (for morning/evening) or nothing sends (for noon).
- **Daily briefs:** 06:30 LA (yesterday's totals) and 21:00 LA (today's totals) land in Telegram automatically. If any items are still in `needs_review` for that window, the brief carries a **Review N items** button.
- **Triage:** tap the Review button to walk the queue. Each card shows the bot's current project/type/summary and the original text. Four buttons — **✅ Keep** (approve), **📂 Refile** (opens a project picker), **📝 → Todo** (force `type=todo`; not pushed to Todoist), **🗑 Discard** (status=discarded). Each tap auto-advances to the next card; every action writes a `corrections` row for the learning pass.
- **Review misclassifications offline:** open Obsidian vault `personal-os` — notes appear in `<project>/<YYYY-MM-DD>-<slug>.md`. Systemic misfilings still belong in `_meta/rules.md` (commit, push, redeploy).
- **Vault location:** `Dropbox (Personal)\Apps\roscoe-robot\personal-os\` (App-folder-scoped Dropbox token). Obsidian opens this exact path as a vault — Obsidian Sync is OFF; Dropbox is the only sync layer.

## Key invariants (don't violate without discussion)

- **Todoist filing uses `project_id`, not parent tasks.** Ryan's Todoist is structured as separate projects per area; tasks are top-level inside each project. Env vars are `TODOIST_PROJECT_*`.
- **Todoist API: use `/api/v1/sync` with `item_add` commands.** REST v2 returns 410 Gone (deprecated 2025).
- **Only `bot/main.py` knows FastAPI / asyncio exists.** `bot/processor.py` and helpers are pure-sync. Async work happens in main.py and crosses into the processor via `asyncio.to_thread(run_batch, ...)`. Never call `asyncio.run()` inside the processor.
- **Classifier output parsing must tolerate markdown fences.** Haiku 4.5 wraps JSON in ` ```json ... ``` ` even when the prompt forbids it. `bot/llm.py:_extract_json` strips fences and falls back to first `{...}` regex. Reuse this helper for any new LLM-output parser.
- **Per-item failures must not kill the batch.** `process_item` never raises; failures land in the result dict and the run continues.
- **Capture layer never calls Claude.** Telegram → Supabase + Dropbox only. No classification at intake.
- **Triage handlers must be idempotent.** Each terminal handler (`_handle_keep` / `_discard` / `_mark_todo` / `_set_project`) checks `item.status == 'needs_review'` and bails silently if not — Telegram retries and double-taps would otherwise duplicate corrections rows. The shared guard is `_load_for_triage` in `bot/main.py`.
- **Status values are constraint-enforced.** Postgres `items_status_check` allows `pending / processed / needs_review / failed / discarded`. Adding a new status requires a new file in `migrations/` and a manual paste into Supabase SQL Editor (no migration runner in the repo).
- **Cron paths pass `daily_cap_cents`; `/process` does not.** `_run_cron_batch` in `bot/main.py` always passes `daily_cap_cents=config.daily_cost_cap_cents`; `_run_batch_and_reply` (the `/process` path) omits the kwarg so `run_batch` runs unlimited. If you add a new batch entry point, decide which side it belongs to — autonomy or operator override — and follow the same pattern.
- **`run_batch.trigger` values are CHECK-constrained.** `runs.trigger` only accepts `scheduled-630 / scheduled-1200 / scheduled-2100 / on-demand / weekly-digest / monthly-rules` (migration 001). Any new trigger needs a migration first.

## Production environment

- **Droplet:** DigitalOcean, `64.23.170.115`, code at `/opt/personal-os-v2/`, systemd unit `personal-os-v2.service`. SSH with `ssh root@64.23.170.115` (interactive password — Ryan runs commands; agent has no key access).
- **Supabase project ID:** `sqzbdkxbeotmywjdksmd`. Tables: `items`, `runs`, `corrections`. Use Supabase MCP for direct SQL.
- **Webhook URL:** `https://64.23.170.115:8443/telegram/webhook?secret=<WEBHOOK_SECRET>` (self-signed cert, accepted by Telegram).
- **Healthcheck:** `curl -sk https://localhost:8443/healthz` on the droplet.

## Standard redeploy after merging to main

```
ssh root@64.23.170.115 'cd /opt/personal-os-v2 && git pull && systemctl restart personal-os-v2 && sleep 3 && systemctl status personal-os-v2 --no-pager | head -5'
ssh root@64.23.170.115 'journalctl -u personal-os-v2 -n 50 --no-pager'   # check first batch
```

`.env` lives at `/opt/personal-os-v2/.env` (mode 0600). When syncing from Windows, **always base64-encode locally and decode on the droplet** — `tr -d "\r"` over SSH silently strips literal `r` characters from secrets. Pattern is in the env-deploy memory.

## Development workflow

- **TDD throughout.** New behavior gets a failing test first; tests live in `tests/`. Run with `pytest -q`. Current count: ~217 passing (post-Session 6).
- **No new files at repo root** beyond what's already there. New code goes in `bot/` (runtime), `tests/` (tests), or `docs/superpowers/{specs,plans}/` (design docs).
- **Plans go under `docs/superpowers/plans/`** with filename `YYYY-MM-DD-session-N-<slug>.md`. Specs are in `docs/superpowers/specs/`. Don't recreate `spec.md` at root — it was deliberately removed (see commit `8e5e271`).
- **Commit style** mirrors existing log: `feat(session-N): ...` / `fix(session-N): ...` / `refactor(session-N): ...` / `docs(plan|spec): ...`. Sign commits with `Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>`.

## Things that are NOT done yet

- **Long X-tweet video downloads OOM-crash the droplet.** Session 6's `_download_video_to_dropbox` reads the entire MP4 into memory before pushing to Dropbox. A 4K (3324×2160) tweet video exhausts the droplet RAM and Linux OOM-kills uvicorn. Session 6b plan fixes by skipping the download for videos >60s and routing them as tutorial reminders. Until 6b ships, **autonomy is paused** via `DAILY_COST_CAP_CENTS=0` on the droplet.
- **Whisper voice transcription is silently failing on the droplet.** Voice items land with `raw_text=null` and the classifier hallucinates a summary from an empty payload. `transcribe_voice` unit-tests pass; the failure is environmental (env-var, file-format, or quota). Session 6c plan adds defensive failure-surfacing + a diagnostic step + a fix branch.
- **`/find` retrieval.** No retrieval surface yet beyond Obsidian native search and Supabase queries. Session 8.
- **Mark-as-Todo doesn't push to Todoist.** The 📝 triage button flips `type=todo` and writes a correction, but does NOT call the Todoist API — Ryan adds the task manually or re-runs `/process`. Intentional cost discipline; can be revisited if the manual step becomes friction.
- **Weekly digest + monthly rule consolidation.** Session 7 — Sonnet 4.6 weekly summary on Saturdays, automatic rule pass once a month, research-thread clustering.
