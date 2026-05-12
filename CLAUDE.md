# roscoe-robot — Claude Project Notes

Personal capture-and-route system. Single ingest point (Telegram bot "Roscoe") collects text/photos/links/voice during the day; a batch processor classifies, enriches, and files each item to Obsidian (Dropbox), Todoist, and Supabase. Target cost: $5–8/month.

## Where to look first

Read these in order before doing any non-trivial work:

1. [docs/superpowers/specs/2026-05-05-personal-os-design.md](docs/superpowers/specs/2026-05-05-personal-os-design.md) — canonical design (architecture, data model, session roadmap).
2. [docs/superpowers/plans/](docs/superpowers/plans/) — per-session implementation plans (Session 2 capture, Session 3 process).
3. Your memory directory for this project — droplet SSH, Supabase ID, Todoist endpoint, classifier quirks, env-deploy gotchas.

Don't duplicate the spec here; just pointer.

## Current state — 2026-05-12

**Session 5 (cron autonomy + daily cost cap) shipped.** The bot now runs itself three times a day:
- **Three APScheduler cron jobs** at 06:30 / 12:00 / 21:00 America/Los_Angeles, all in the FastAPI lifespan. Morning + evening run the batch first, then send the daily summary. Noon is a silent batch — only speaks on `items_failed > 0` or a cap hit.
- **Daily cost cap** (default 200¢ via `DAILY_COST_CAP_CENTS`). The processor checks `today_already_spent_cents + counts["total_cost_cents"]` before each item and halts mid-loop when it would cross the cap. Halt fires a Telegram alert: `⛔ halted at $X.XX — N items remain pending`. Remaining items stay `pending`.
- **`/process` bypasses the cap** — passes `daily_cap_cents=None` to `run_batch`. It's the explicit operator escape hatch when the cap has already paused autonomy.
- **Kill switch:** set `DAILY_COST_CAP_CENTS=0` in `.env` and restart. Every cron run sees `today_spend >= 0 >= cap` and skips silently; `/process` still works.
- **Trigger values** written to `runs.trigger`: `scheduled-630`, `scheduled-1200`, `scheduled-2100`, plus the existing `on-demand`.

Smoke test on the droplet: a manual `_noon_cron()` against production state processed 2 X-URL items end-to-end (Apify → vision → Dropbox → Supabase), wrote a `runs` row with `trigger='scheduled-1200'`, no Telegram message — exactly the silent-success contract.

**Now: validation week #3 — let cron live.** Watch a few full days of autonomous runs. If 200¢ feels too loose or too tight, tune `DAILY_COST_CAP_CENTS` on the droplet (no code change). Do not start Session 6 without explicit instruction.

**Next sessions (per spec):**
- **Session 6** — Weekly digest (Sonnet 4.6), monthly rule consolidation pass, research-thread clustering.
- **Session 7** — `/find` polish, OA-wiki feeders, multi-source ingest.

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

- **TDD throughout.** New behavior gets a failing test first; tests live in `tests/`. Run with `pytest -q`. Current count: 211 passing.
- **No new files at repo root** beyond what's already there. New code goes in `bot/` (runtime), `tests/` (tests), or `docs/superpowers/{specs,plans}/` (design docs).
- **Plans go under `docs/superpowers/plans/`** with filename `YYYY-MM-DD-session-N-<slug>.md`. Specs are in `docs/superpowers/specs/`. Don't recreate `spec.md` at root — it was deliberately removed (see commit `8e5e271`).
- **Commit style** mirrors existing log: `feat(session-N): ...` / `fix(session-N): ...` / `refactor(session-N): ...` / `docs(plan|spec): ...`. Sign commits with `Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>`.

## Things that are NOT done yet

- **Voice transcription on droplet.** Code path is built (`transcribe_voice` in `bot/enrichment.py`) but unverified end-to-end on droplet — no real voice memo has been processed yet.
- **`/find` retrieval.** No retrieval surface yet beyond Obsidian native search and Supabase queries. Session 7.
- **Mark-as-Todo doesn't push to Todoist.** The 📝 triage button flips `type=todo` and writes a correction, but does NOT call the Todoist API — Ryan adds the task manually or re-runs `/process`. Intentional cost discipline; can be revisited if the manual step becomes friction.
- **Weekly digest + monthly rule consolidation.** Session 6 — Sonnet 4.6 weekly summary on Saturdays, automatic rule pass once a month, research-thread clustering.
