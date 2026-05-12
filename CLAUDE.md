# roscoe-robot — Claude Project Notes

Personal capture-and-route system. Single ingest point (Telegram bot "Roscoe") collects text/photos/links/voice during the day; a batch processor classifies, enriches, and files each item to Obsidian (Dropbox), Todoist, and Supabase. Target cost: $5–8/month.

## Where to look first

Read these in order before doing any non-trivial work:

1. [docs/superpowers/specs/2026-05-05-personal-os-design.md](docs/superpowers/specs/2026-05-05-personal-os-design.md) — canonical design (architecture, data model, session roadmap).
2. [docs/superpowers/plans/](docs/superpowers/plans/) — per-session implementation plans (Session 2 capture, Session 3 process).
3. Your memory directory for this project — droplet SSH, Supabase ID, Todoist endpoint, classifier quirks, env-deploy gotchas.

Don't duplicate the spec here; just pointer.

## Current state — 2026-05-12

**Session 4 (vision + X scraper + daily summary + triage UI) shipped.** All four pieces live in production end-to-end:
- **Apify X scraper** (Phase A) — `x.com` / `twitter.com` URLs now pull full text + images + author via Apify; fan-out children classified per image.
- **Vision two-pass** (Phase B) — design+image items run a second-pass with Anthropic vision content blocks; cheap-by-default (only design type triggers it).
- **APScheduler daily summaries** (Phase C) — 06:30 morning + 21:00 evening LA-local cron in the FastAPI lifespan. Brief is plain text grouped by project, with cost and needs_review counts.
- **Triage UI** (Phase D) — Review button on each brief, four-button per-item keyboard (Keep / Refile / → Todo / Discard), refile project picker, auto-advance to next card after each tap, idempotency guard against double-taps. Each action writes a `corrections` row for the future learning pass.

**Now: validation week resumes.** Same posture as after Session 3: Ryan triages real `needs_review` items via the inline-keyboard buttons, corrections rows accumulate, rules go into `_meta/rules.md` for systematic misclassifications. Do not start Session 5 without explicit instruction.

**Next sessions (per spec):**
- **Session 5** — Autonomy: cron on droplet, 12:00 PM silent run, daily cost cap.
- **Session 6** — Weekly digest (Sonnet 4.6), monthly rule consolidation pass, research-thread clustering.
- **Session 7** — `/find` polish, OA-wiki feeders, multi-source ingest.

## Operator runbook (how Ryan uses the bot)

- **Capture:** forward/send anything to the Telegram bot. Bot acks with 👍 and writes to Supabase `items` (status=pending). No Claude call at this stage.
- **Process:** in Telegram, send `/process`. Bot acks 👍, runs the batch in the background (≤50 items at a time), replies with `processed N · $X.XX · M needs review`.
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

- **TDD throughout.** New behavior gets a failing test first; tests live in `tests/`. Run with `pytest -q`. Current count: 185 passing.
- **No new files at repo root** beyond what's already there. New code goes in `bot/` (runtime), `tests/` (tests), or `docs/superpowers/{specs,plans}/` (design docs).
- **Plans go under `docs/superpowers/plans/`** with filename `YYYY-MM-DD-session-N-<slug>.md`. Specs are in `docs/superpowers/specs/`. Don't recreate `spec.md` at root — it was deliberately removed (see commit `8e5e271`).
- **Commit style** mirrors existing log: `feat(session-N): ...` / `fix(session-N): ...` / `refactor(session-N): ...` / `docs(plan|spec): ...`. Sign commits with `Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>`.

## Things that are NOT done yet

- **Voice transcription on droplet.** Code path is built (`transcribe_voice` in `bot/enrichment.py`) but unverified end-to-end on droplet — no real voice memo has been processed yet.
- **Cron / autonomy.** `/process` is manual only. Session 5 adds the 12:00 PM silent run and daily cost cap.
- **`/find` retrieval.** No retrieval surface yet beyond Obsidian native search and Supabase queries. Session 7.
- **Mark-as-Todo doesn't push to Todoist.** The 📝 triage button flips `type=todo` and writes a correction, but does NOT call the Todoist API — Ryan adds the task manually or re-runs `/process`. Intentional cost discipline; can be revisited if the manual step becomes friction.
