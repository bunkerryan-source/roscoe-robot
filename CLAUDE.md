# roscoe-robot — Claude Project Notes

Personal capture-and-route system. Single ingest point (Telegram bot "Roscoe") collects text/photos/links/voice during the day; a batch processor classifies, enriches, and files each item to Obsidian (Dropbox), Todoist, and Supabase. Target cost: $5–8/month.

## Where to look first

Read these in order before doing any non-trivial work:

1. [docs/superpowers/specs/2026-05-05-personal-os-design.md](docs/superpowers/specs/2026-05-05-personal-os-design.md) — canonical design (architecture, data model, session roadmap).
2. [docs/superpowers/plans/](docs/superpowers/plans/) — per-session implementation plans (Session 2 capture, Session 3 process).
3. Your memory directory for this project — droplet SSH, Supabase ID, Todoist endpoint, classifier quirks, env-deploy gotchas.

Don't duplicate the spec here; just pointer.

## Current state — 2026-05-09

**Session 3 (process pipeline) shipped.** `/process` is live in production. Telegram bot accepts captures, processor drains the queue via Anthropic Haiku 4.5 (with prompt caching), files to Obsidian + Todoist + Supabase. End-to-end smoke test passed: 3 items processed, 1 needs_review, $0.04, Todoist tasks created in correct projects.

**Now: validation week.** Ryan runs `/process` daily on real captures. Each misclassification gets logged as a rule in `_meta/rules.md`. Goal is to gather correction data from real use before Session 4 begins. **Do not start Session 4 work without explicit instruction.**

**Next sessions (per spec):**
- **Session 4** — Daily summary at 6:30/21:00 + B+ triage UI (inline keyboard), corrections table, vision wiring for image items, **Apify X scraper** in the enrichment layer (replaces failed OG fetch on `x.com`/`twitter.com` URLs; pulls full text + images + author). Adds `APIFY_API_TOKEN` to `.env`.
- **Session 5** — Autonomy: cron on droplet, 12:00 PM silent run, daily cost cap.
- **Session 6** — Weekly digest (Sonnet 4.6), monthly rule consolidation pass, research-thread clustering.
- **Session 7** — `/find` polish, OA-wiki feeders, multi-source ingest.

## Operator runbook (how Ryan uses the bot)

- **Capture:** forward/send anything to the Telegram bot. Bot acks with 👍 and writes to Supabase `items` (status=pending). No Claude call at this stage.
- **Process:** in Telegram, send `/process`. Bot acks 👍, runs the batch in the background (≤50 items at a time), replies with `processed N · $X.XX · M needs review`.
- **Review misclassifications:** open Obsidian vault `personal-os` — notes appear in `<project>/<YYYY-MM-DD>-<slug>.md`. If something's misfiled, edit `_meta/rules.md` in the git repo with the rule (e.g., "Tweets from @user X go to project Y"), commit, push, and redeploy.
- **Vault location:** `Dropbox (Personal)\Apps\roscoe-robot\personal-os\` (App-folder-scoped Dropbox token). Obsidian opens this exact path as a vault — Obsidian Sync is OFF; Dropbox is the only sync layer.

## Key invariants (don't violate without discussion)

- **Todoist filing uses `project_id`, not parent tasks.** Ryan's Todoist is structured as separate projects per area; tasks are top-level inside each project. Env vars are `TODOIST_PROJECT_*`.
- **Todoist API: use `/api/v1/sync` with `item_add` commands.** REST v2 returns 410 Gone (deprecated 2025).
- **Only `bot/main.py` knows FastAPI / asyncio exists.** `bot/processor.py` and helpers are pure-sync. Async work happens in main.py and crosses into the processor via `asyncio.to_thread(run_batch, ...)`. Never call `asyncio.run()` inside the processor.
- **Classifier output parsing must tolerate markdown fences.** Haiku 4.5 wraps JSON in ` ```json ... ``` ` even when the prompt forbids it. `bot/llm.py:_extract_json` strips fences and falls back to first `{...}` regex. Reuse this helper for any new LLM-output parser.
- **Per-item failures must not kill the batch.** `process_item` never raises; failures land in the result dict and the run continues.
- **Capture layer never calls Claude.** Telegram → Supabase + Dropbox only. No classification at intake.

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

- **TDD throughout.** New behavior gets a failing test first; tests live in `tests/`. Run with `pytest -q`. Current count: 85 passing.
- **No new files at repo root** beyond what's already there. New code goes in `bot/` (runtime), `tests/` (tests), or `docs/superpowers/{specs,plans}/` (design docs).
- **Plans go under `docs/superpowers/plans/`** with filename `YYYY-MM-DD-session-N-<slug>.md`. Specs are in `docs/superpowers/specs/`. Don't recreate `spec.md` at root — it was deliberately removed (see commit `8e5e271`).
- **Commit style** mirrors existing log: `feat(session-N): ...` / `fix(session-N): ...` / `refactor(session-N): ...` / `docs(plan|spec): ...`. Sign commits with `Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>`.

## Things that are NOT done yet

- **Vision (image classification).** Image-only items currently get `needs_review` because the classifier can't see them. Wired in Session 4.
- **X / Twitter scraping.** OG fetch on `x.com` URLs returns nothing because Twitter blocks unauthenticated scrapers. X URLs without user-typed context land in `needs_review`. Apify scraper added in Session 4 will close this gap. Until then, capture habit is: paste tweet text or screenshot the image with a descriptive caption (see operator runbook).
- **Voice transcription on droplet.** Code path is built (`transcribe_voice` in `bot/enrichment.py`) but unverified end-to-end on droplet — no real voice memo has been processed yet.
- **Cron / autonomy.** `/process` is manual only. Session 5 adds the 12:00 PM silent run and daily cost cap.
- **`/find` retrieval.** No retrieval surface yet beyond Obsidian native search and Supabase queries. Session 7.
- **Daily summary + triage UI.** Session 4 builds this.
