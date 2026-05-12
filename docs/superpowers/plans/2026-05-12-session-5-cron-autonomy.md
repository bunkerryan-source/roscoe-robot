# Session 5 — Cron Autonomy + Daily Cost Cap

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the bot run itself. Three scheduled batches per day (06:30 / 12:00 / 21:00 LA), with a daily cost cap that halts processing if spend runs away. The 06:30 and 21:00 jobs absorb today's summary message; 12:00 is a silent run that only speaks on errors or a cap hit. `/process` remains the manual escape hatch and bypasses the cap.

**Architecture:** Single-process autonomy. APScheduler is already running inside the FastAPI lifespan from Session 4 — we add a third job slot, repurpose the existing two from "summary only" to "batch + summary", and inject cap-awareness into `run_batch`. No new infrastructure (no systemd timer, no separate worker). Cap state is derived on the fly from `runs.total_cost_cents` summed across today's LA-local window; no new schema, no separate counter.

**Tech Stack:** Existing Python 3.11+ stack. APScheduler 3.10+ already a dependency. Tests: pytest, pytest-asyncio, pytest-mock — already configured.

---

## Spec reference

Implements the **Session 5** entry of the [design spec](../specs/2026-05-05-personal-os-design.md) build sequence (line 295), plus the cost-control items 3 and 4 from the spec's "Cost model + controls" table (lines 281–282). The spec's failure-mode row "Daily cost cap hit" (line 265) defines the halt + Telegram alert behavior implemented here.

What this session does **not** ship (deferred per spec):

- Weekly digest (Sonnet 4.6) → Session 6.
- Monthly rule consolidation pass → Session 6.
- Research-thread clustering → Session 6.
- `/find` polish, OA-wiki feeders, multi-source ingest → Session 7.

What this session **does** ship:

- Three APScheduler cron jobs: 06:30, 12:00, 21:00 America/Los_Angeles.
- `run_batch` accepts an optional `daily_cap_cents` and halts per-item once cumulative spend crosses the cap, leaving the remaining queue `pending`.
- `bot/cost_cap.py` — one helper that returns today's spend in cents from `runs.total_cost_cents`, windowed to LA-local calendar day.
- Telegram halt alert: `halted at $X.XX — N items remain pending`, fired once by whichever run crosses the cap.
- `/process` ignores the cap entirely (per spec line 282 escape-hatch language).
- Noon run is silent unless `halted_at_cap` or `items_failed > 0`.

---

## Decisions baked into this plan (override before starting if you disagree)

| Decision | Choice | Why |
|---|---|---|
| Combined cron job per slot | 06:30 and 21:00 each run **batch first, then summary** as one job | One control flow per slot, summary reflects the fresh batch, halt alert can be sent before the summary cleanly. |
| Noon job | Batch only, no summary unless `halted_at_cap` or `items_failed > 0` | Spec calls 12:00 the "silent run." Failures still surface so they don't accumulate unnoticed. |
| Cap check granularity | Per-item, inside `run_batch`'s loop, before each `process_item` call | Halts within ≤1¢ of cap. Matches spec wording: "halt processing immediately." Per-batch only would let one runaway run blow through 50× the cap. |
| Cap default | **200¢ ($2.00)** | At expected ~$0.20/day, gives ~10× headroom. Cron only fires alert on a real anomaly. Env var `DAILY_COST_CAP_CENTS` overrides. |
| Cap source-of-truth | Sum of `runs.total_cost_cents` for runs with `started_at` in today's LA-local window, plus the running total of the in-progress batch | Avoids querying `items` (cheaper, fewer rows, already aggregated). Reads on every per-item check are fast but we snapshot the historical part at batch start and only increment in-memory after that. |
| `/process` vs cap | `/process` bypasses cap (passes `daily_cap_cents=None`) | Spec line 282: "User raises the cap or runs `/process` manually." Manual override is the whole point. |
| Cap alert idempotency | Cron job inspects `halted_at_cap` in batch result and fires the alert. If a later cron sees `today_spend >= cap` before starting, it skips the batch silently with a log line | Alert fires exactly once on the run that crosses the threshold; no spam from subsequent cron firings. |
| Trigger values for `runs.trigger` | `scheduled-630` / `scheduled-1200` / `scheduled-2100` | Already in the CHECK constraint (migration 001 line 61). No schema change needed. |
| Cron coro naming | `_morning_cron`, `_noon_cron`, `_evening_cron` (rename of `_send_morning_summary` / `_send_evening_summary`) | Names reflect actual scope now that they do work, not just send a message. |
| Scheduler job IDs | `cron_morning`, `cron_noon`, `cron_evening` (rename of `daily_summary_*`) | Same reason. |
| Test isolation | All new cron coros are coroutine-style helpers in `bot/main.py`; tests exercise them directly with mocks rather than via APScheduler firing | Mirrors how Session 4's summary tests work; APScheduler timing is its own contract, tested at the `build_scheduler` level. |

If you want to swap any of these, edit this section before starting Task 1.

---

## File structure (what this plan creates/modifies)

**New files:**
- `bot/cost_cap.py` — `fetch_today_spend_cents(client, now=None) -> int`.
- `tests/test_cost_cap.py` — windowing + sum behavior.

**Modified files:**
- `bot/processor.py` — `run_batch` accepts `daily_cap_cents` and `today_already_spent_cents`; halts per-item; returns `halted_at_cap` and `items_remaining_pending` in the result dict.
- `bot/scheduler.py` — `build_scheduler` takes three callables (morning / noon / evening), registers three jobs.
- `bot/main.py` — rename existing summary coros to `_morning_cron` / `_evening_cron`; add `_noon_cron`; each combines cap-pre-check + batch + halt-alert + (conditional) summary. Update `lifespan` to register all three.
- `bot/config.py` — add `daily_cost_cap_cents: int` (default 200), read from `DAILY_COST_CAP_CENTS` env var (optional).
- `.env.example` — add `DAILY_COST_CAP_CENTS=200` with a comment.

**Modified tests:**
- `tests/test_scheduler.py` — now expects 3 jobs (`cron_morning`, `cron_noon`, `cron_evening`) at the right wall-clock times.
- `tests/test_processor.py` — new cases: per-item cap halt, `/process`-style unlimited run, `halted_at_cap` in return dict.
- `tests/test_main.py` — new cases: morning/noon/evening cron behaviors (skip when over cap, halt alert format, noon silence on success).
- `tests/test_config.py` — default + env override for `daily_cost_cap_cents`.

**No schema migration.** `runs.trigger` CHECK constraint already accepts the three scheduled values (migration `001_initial_schema.sql:61`); no new columns.

---

## Prerequisites (operator setup before Task 1)

- [ ] **P1: Confirm Session 4 changes are deployed to droplet** — `git pull` on main, `systemctl status personal-os-v2`. APScheduler should already be logging `started — jobs: [...]` on lifespan startup.
- [ ] **P2: Confirm droplet timezone is UTC** — `ssh root@64.23.170.115 'timedatectl | grep "Time zone"'`. APScheduler converts to LA internally.
- [ ] **P3: Confirm tests are green on `main`** — `.venv/Scripts/python.exe -m pytest -q` → 185 passing.
- [ ] **P4: Create branch** — `git checkout -b session-5` from current `main`.

---

## Phase 1 — Cost-cap helper + config

### Task 1.1 — Failing test for `fetch_today_spend_cents`

- [ ] Create `tests/test_cost_cap.py` with these cases (mock the Supabase client like other tests do — see `tests/test_db.py` for the pattern):
  - No runs in DB → returns `0`.
  - One run today (LA-local) with `total_cost_cents=37` → returns `37`.
  - One run yesterday LA-local with `total_cost_cents=100` + one run today with `total_cost_cents=12` → returns `12`.
  - Function accepts an injected `now` for deterministic windowing.
- [ ] Run `pytest tests/test_cost_cap.py -q`. Confirm the file fails to import (module doesn't exist).

### Task 1.2 — Implement `bot/cost_cap.py`

- [ ] Create `bot/cost_cap.py` with:
  ```python
  from datetime import datetime, timezone
  from zoneinfo import ZoneInfo

  LA = ZoneInfo("America/Los_Angeles")


  def fetch_today_spend_cents(client, *, now: datetime | None = None) -> int:
      now_la = (now or datetime.now(timezone.utc)).astimezone(LA)
      start_la = now_la.replace(hour=0, minute=0, second=0, microsecond=0)
      since_utc = start_la.astimezone(timezone.utc).isoformat()
      rows = (
          client.table("runs")
          .select("total_cost_cents")
          .gte("started_at", since_utc)
          .execute()
          .data
          or []
      )
      return sum((r.get("total_cost_cents") or 0) for r in rows)
  ```
- [ ] Run `pytest tests/test_cost_cap.py -q`. All pass.

### Task 1.3 — Config field for cap

- [ ] Open `tests/test_config.py`. Add two cases:
  - With `DAILY_COST_CAP_CENTS=350` env → `Config.from_env().daily_cost_cap_cents == 350`.
  - With env unset → `daily_cost_cap_cents == 200` (default).
- [ ] Add field `daily_cost_cap_cents: int` to `Config` dataclass in `bot/config.py`.
- [ ] In `Config.from_env`, parse `os.environ.get("DAILY_COST_CAP_CENTS", "200")` as int. Validate it's > 0; raise `ValueError` if not.
- [ ] Update `.env.example` — append:
  ```
  # Daily cost cap in cents. When today's cumulative cron spend reaches this,
  # cron batches halt mid-run and Telegram gets a halt alert. /process bypasses.
  DAILY_COST_CAP_CENTS=200
  ```
- [ ] Run `pytest tests/test_config.py tests/test_cost_cap.py -q`. All pass.

---

## Phase 2 — Processor honors per-item cap

### Task 2.1 — Failing test: cap halts mid-batch

- [ ] In `tests/test_processor.py`, add `test_run_batch_halts_when_cap_reached`:
  - Stub `process_item` (via pytest-mock) to return `{"status": "processed", "api_cost_cents": 50, "classification": ..., ...}` for each item.
  - Seed 5 fake pending items.
  - Call `run_batch(..., daily_cap_cents=150, today_already_spent_cents=0, trigger="scheduled-1200")`.
  - Assert `result["items_processed"] == 3` (first 3 items × 50¢ = 150¢ hits the cap).
  - Assert `result["halted_at_cap"] is True`.
  - Assert `result["items_remaining_pending"] == 2`.
  - Assert no `update_item_*` calls for items 4 and 5 (they stay pending in DB).

### Task 2.2 — Failing test: `/process`-style (no cap) is unlimited

- [ ] Add `test_run_batch_unlimited_when_no_cap`:
  - Same 5 items, same 50¢ each.
  - Call `run_batch(...)` without `daily_cap_cents` (or `daily_cap_cents=None`).
  - Assert all 5 processed, `halted_at_cap` is `False`, `items_remaining_pending == 0`.

### Task 2.3 — Failing test: cap respects historical spend

- [ ] Add `test_run_batch_respects_already_spent_today`:
  - Same setup but `today_already_spent_cents=120`, `daily_cap_cents=150`.
  - Assert `items_processed == 1` (1 × 50¢ + 120¢ = 170¢ hits cap on item 2; only 1 processed before the check fires).
  - Wait — re-check the math: cap check fires BEFORE each item using "previous total + previous in-batch total". At item 1: 120 < 150 → process (in-batch=50). At item 2: 120+50=170 ≥ 150 → halt. So `items_processed == 1`, `halted_at_cap=True`, remaining=4.
  - Encode that exact expectation.

### Task 2.4 — Implement cap in `run_batch`

- [ ] Modify `run_batch` signature in `bot/processor.py` to add two keyword args after `trigger`:
  ```python
  daily_cap_cents: int | None = None,
  today_already_spent_cents: int = 0,
  ```
- [ ] At the top of the `while idx < len(pending)` loop, before calling `process_item`:
  ```python
  if daily_cap_cents is not None:
      projected = today_already_spent_cents + counts["total_cost_cents"]
      if projected >= daily_cap_cents:
          break
  ```
- [ ] After the loop, before the `insert_run` call, compute:
  ```python
  halted_at_cap = (
      daily_cap_cents is not None
      and today_already_spent_cents + counts["total_cost_cents"] >= daily_cap_cents
      and idx < len(pending)  # still items left when we broke
  )
  items_remaining_pending = max(0, len(pending) - idx)
  ```
  Hmm — if we broke early `idx` is the index of the next item that didn't run. If we ran to completion, `idx == len(pending)` so `items_remaining_pending == 0`. Good.
- [ ] Add `halted_at_cap` and `items_remaining_pending` to the dict returned by `run_batch` (alongside the existing `items_processed` / `items_needs_review` / `items_failed` / `total_cost_cents` / `duration_seconds`).
- [ ] Run `pytest tests/test_processor.py -q`. All pass (existing and new).

---

## Phase 3 — Scheduler registers three jobs

### Task 3.1 — Failing tests in `tests/test_scheduler.py`

- [ ] Update existing tests in `tests/test_scheduler.py`:
  - `build_scheduler` now takes three callables (`morning_job`, `noon_job`, `evening_job`).
  - Assert `len(jobs) == 3` and `{j.id for j in jobs} == {"cron_morning", "cron_noon", "cron_evening"}`.
  - Three separate hour/minute assertions: (6,30), (12,0), (21,0).
  - All three trigger timezones are `America/Los_Angeles`.

### Task 3.2 — Update `bot/scheduler.py`

- [ ] Modify `build_scheduler` to accept `noon_job` as a third positional/keyword arg.
- [ ] Register three jobs with the new IDs and times. Keep `replace_existing=True`.
- [ ] Run `pytest tests/test_scheduler.py -q`. All pass.

### Task 3.3 — Failing tests for the three cron coros in `tests/test_main.py`

These tests live alongside the existing summary tests. Pattern: mock the Supabase client, `run_batch`, `send_message`, and `fetch_today_spend_cents`; assert on the call sequence.

- [ ] `test_morning_cron_runs_batch_then_summary_when_under_cap`:
  - `fetch_today_spend_cents` → 0.
  - `run_batch` called with `trigger="scheduled-630"`, `daily_cap_cents=200`, `today_already_spent_cents=0`.
  - `run_batch` returns `{"items_processed": 2, "items_needs_review": 0, "items_failed": 0, "total_cost_cents": 30, "halted_at_cap": False, "items_remaining_pending": 0}`.
  - Then the morning summary message gets sent (existing behavior).
  - No halt alert.

- [ ] `test_morning_cron_skips_batch_silently_when_already_over_cap`:
  - `fetch_today_spend_cents` → 250 (cap is 200).
  - `run_batch` NOT called.
  - Summary still sent (it's a daily brief, separate concern).
  - No halt alert.

- [ ] `test_morning_cron_sends_halt_alert_when_batch_hits_cap`:
  - `fetch_today_spend_cents` → 0.
  - `run_batch` returns `halted_at_cap=True`, `items_remaining_pending=3`, `total_cost_cents=200`.
  - Halt alert sent FIRST with text matching `r"halted at \$2\.00.*3 items remain pending"`.
  - Then the summary.

- [ ] `test_noon_cron_is_silent_on_clean_run`:
  - All-zero failures, no halt → `send_message` not called at all.

- [ ] `test_noon_cron_speaks_on_failures`:
  - `items_failed > 0` → one message with text matching `r"noon run.*\d+ failed"`.

- [ ] `test_noon_cron_speaks_on_cap_hit`:
  - `halted_at_cap=True` → halt alert sent.

- [ ] `test_evening_cron_runs_batch_then_summary_when_under_cap`: mirror of morning.

- [ ] `test_evening_cron_skips_batch_when_over_cap`: mirror of morning.

- [ ] `test_process_command_bypasses_cap`:
  - In `_run_batch_and_reply`, confirm `run_batch` is called WITHOUT `daily_cap_cents` (or with `daily_cap_cents=None`).

### Task 3.4 — Implement the three cron coros in `bot/main.py`

- [ ] Add a helper near the top of the cron section:
  ```python
  def _halt_alert_text(remaining: int, cap_cents: int) -> str:
      dollars = cap_cents / 100.0
      noun = "item" if remaining == 1 else "items"
      return f"\U000026D4 halted at ${dollars:.2f} — {remaining} {noun} remain pending"
  ```
  (⛔ U+26D4 is "no entry" — distinct from a regular emoji and reads as an alert.)

- [ ] Add a coroutine `_run_cron_batch(trigger: str) -> dict | None`:
  - Returns `None` if today's spend is already ≥ cap.
  - Otherwise calls `asyncio.to_thread(run_batch, ..., daily_cap_cents=config.daily_cost_cap_cents, today_already_spent_cents=<snapshot>, trigger=trigger)` and returns the result dict.
  - If `halted_at_cap` is true, also sends the halt alert.
  - Logs exceptions with `logger.exception`; returns `None` on exception so caller can decide whether to send a summary.

- [ ] Rewrite `_send_morning_summary` → `_morning_cron`:
  ```python
  async def _morning_cron() -> None:
      await _run_cron_batch(trigger="scheduled-630")
      try:
          # existing morning summary body, unchanged
      except Exception:
          logger.exception("morning summary job failed")
  ```
  The batch runs first; the summary still always sends (it reports yesterday's totals). If the batch raises, the summary still tries to send.

- [ ] Add `_noon_cron`:
  ```python
  async def _noon_cron() -> None:
      result = await _run_cron_batch(trigger="scheduled-1200")
      if result is None:
          return  # over cap; alert already sent (or skipped silently)
      if result.get("items_failed"):
          n = result["items_failed"]
          await send_message(
              config.my_telegram_id, None,
              f"noon run: {n} failed — check journalctl",
          )
      # otherwise silent
  ```

- [ ] Rewrite `_send_evening_summary` → `_evening_cron`: same shape as morning. Batch first, then the evening summary (today's totals).

- [ ] Update the `lifespan` block:
  ```python
  scheduler = build_scheduler(
      morning_job=_morning_cron,
      noon_job=_noon_cron,
      evening_job=_evening_cron,
      timezone="America/Los_Angeles",
  )
  ```

- [ ] Run `pytest tests/test_main.py tests/test_scheduler.py -q`. All pass.

### Task 3.5 — Full test sweep

- [ ] Run `.venv/Scripts/python.exe -m pytest -q`. Confirm count is 185 + new tests, all green.
- [ ] Commit: `feat(session-5): cron autonomy + daily cost cap`.

---

## Phase 4 — Deploy + smoke test on droplet

### Task 4.1 — Push branch

- [ ] `git push -u origin session-5`.

### Task 4.2 — Set the env var on the droplet (optional override)

- [ ] If you want the cap to stay at 200¢, no change needed (default).
- [ ] If overriding: `ssh root@64.23.170.115`, then `nano /opt/personal-os-v2/.env` and add `DAILY_COST_CAP_CENTS=300` (or whatever). Mode stays 0600.
- [ ] If overriding via base64 sync from Windows, follow the env-deploy memory pattern — never `tr -d '\r'` over SSH on Windows-encoded files.

### Task 4.3 — Deploy

- [ ] On droplet:
  ```
  cd /opt/personal-os-v2 && git fetch && git checkout session-5 && git pull && systemctl restart personal-os-v2
  sleep 3 && systemctl status personal-os-v2 --no-pager | head -10
  journalctl -u personal-os-v2 -n 40 --no-pager
  ```
- [ ] Confirm the startup log line shows three jobs with their next-fire times in LA:
  ```
  APScheduler started — jobs: [
    ('cron_morning', '2026-05-13 06:30:00-07:00'),
    ('cron_noon',    '2026-05-13 12:00:00-07:00'),
    ('cron_evening', '2026-05-12 21:00:00-07:00'),
  ]
  ```

### Task 4.4 — Smoke test the cap helper directly

- [ ] On droplet, in a Python REPL inside the venv:
  ```
  /opt/personal-os-v2/venv/bin/python -c "
  from dotenv import load_dotenv; load_dotenv('/opt/personal-os-v2/.env')
  from bot.config import Config
  from bot.db import get_client
  from bot.cost_cap import fetch_today_spend_cents
  c = Config.from_env()
  sb = get_client(c.supabase_url, c.supabase_service_key)
  print('today_cents:', fetch_today_spend_cents(sb))
  print('cap_cents:', c.daily_cost_cap_cents)
  "
  ```
- [ ] Should print a small positive integer (or 0) for `today_cents` and `200` for `cap_cents`. If `today_cents` is suspiciously high, investigate before letting cron fire.

### Task 4.5 — Trigger one cron slot manually

The next cron firing might be hours away. Force one to validate the flow.

- [ ] Send `/process` from Telegram. This confirms `run_batch` still works after the signature change (no cap passed).
- [ ] Then either wait for the next natural cron slot (21:00 if you're working in the afternoon), or simulate via APScheduler:
  ```
  /opt/personal-os-v2/venv/bin/python -c "
  import asyncio
  from dotenv import load_dotenv; load_dotenv('/opt/personal-os-v2/.env')
  from bot.main import _noon_cron
  asyncio.run(_noon_cron())
  "
  ```
  This runs the noon coro inline against production state. If `pending` items exist and nothing fails, you get silence. If `items_failed > 0`, you get the noon alert in Telegram. If `halted_at_cap`, you get the halt alert.

### Task 4.6 — Verify the run row

- [ ] In the Supabase MCP (or dashboard SQL Editor), run:
  ```sql
  select id, trigger, items_processed, total_cost_cents, started_at
  from runs
  where trigger like 'scheduled-%'
  order by started_at desc
  limit 5;
  ```
- [ ] Confirm the new run rows have the right `trigger` value (`scheduled-1200`, `scheduled-630`, or `scheduled-2100`).

### Task 4.7 — Cap-hit simulation (optional but recommended)

- [ ] On droplet, in a Python REPL inside the venv:
  ```
  /opt/personal-os-v2/venv/bin/python -c "
  import asyncio, os
  os.environ['DAILY_COST_CAP_CENTS'] = '1'  # 1 cent — guaranteed to hit
  from dotenv import load_dotenv; load_dotenv('/opt/personal-os-v2/.env', override=False)
  from importlib import reload
  import bot.config; reload(bot.config)
  import bot.main; reload(bot.main)
  asyncio.run(bot.main._noon_cron())
  "
  ```
  Expected: Telegram halt alert ("⛔ halted at $0.01 — N items remain pending"). One run row in `runs` with `halted_at_cap` *not* in the table (it's only in-memory) but `items_processed` should be 0 or 1.
- [ ] Don't leave `DAILY_COST_CAP_CENTS=1` in `.env`. The REPL-set env var dies with the REPL, but double-check `cat /opt/personal-os-v2/.env | grep DAILY` is empty or the right value.

### Task 4.8 — Watch one full natural cron fire

- [ ] Wait for the next scheduled slot (whichever is soonest). Watch `journalctl -u personal-os-v2 -f` during the firing.
- [ ] Confirm the log shows the batch starting, items processing, and the right trigger written to `runs`.

---

## Phase 5 — Merge

### Task 5.1 — Update project CLAUDE.md

- [ ] Bump "Current state" date to today, replace the Session 4 paragraph with a Session 5 shipped paragraph covering:
  - Three cron jobs live (06:30 / 12:00 / 21:00 LA).
  - Daily cost cap (default 200¢) with per-item halt + Telegram alert.
  - `/process` still bypasses the cap.
- [ ] Update the "Things that are NOT done yet" list — remove the "Cron / autonomy" line; everything else stays.
- [ ] Update test count.
- [ ] Update "Next sessions" — Session 5 → Session 6 becomes the active next session.

### Task 5.2 — Memory updates

- [ ] Save a `feedback_*` or `reference_*` memory for any non-obvious gotcha discovered during deploy (e.g., timezone issues, env-var parsing edge cases). If nothing surprising surfaced, skip.

### Task 5.3 — Fast-forward merge to main

- [ ] `git checkout main && git merge --ff-only session-5 && git push origin main`.
- [ ] On droplet: `cd /opt/personal-os-v2 && git checkout main && git pull && systemctl restart personal-os-v2`.
- [ ] Confirm status and that all three jobs registered.

### Task 5.4 — Branch cleanup (optional)

- [ ] `git branch -d session-5` (sequential on PowerShell; no `&&`).
- [ ] `git push origin --delete session-5`.

---

## Rollback plan

If the cron jobs misbehave in production:

1. **Disable autonomy fast:** set `DAILY_COST_CAP_CENTS=0` in `.env` and restart. With cap=0, every cron run will see `today_spend >= 0 >= cap` and skip silently. (`/process` still works.)
2. **Real rollback:** `cd /opt/personal-os-v2 && git checkout main~1 && systemctl restart personal-os-v2`. This drops back to the last commit before Session 5 merged. No schema rollback needed.

---

## Open questions / things to settle during implementation

- Should the halt alert message include a hint about how to raise the cap (`/process` to bypass, or update `DAILY_COST_CAP_CENTS` env)? Lean **yes** for ergonomics, but only after the first natural cap hit teaches us what feels noisy vs. helpful. Defer.
- Should `runs` gain a `halted_at_cap boolean` column? Currently the halt state is only in-memory and visible to whoever reads journalctl after a halt fires. Not blocking for v1; consider in Session 6 if cap hits become frequent enough to warrant a queryable history.
- The 06:30 cron runs BEFORE the morning summary's "yesterday window" closes (we're already in today by 06:30 LA), so it processes today's overnight captures, but the summary message still reports yesterday's totals. This is fine and matches existing Session 4 behavior — call it out in the CLAUDE.md update so a future reader knows.
