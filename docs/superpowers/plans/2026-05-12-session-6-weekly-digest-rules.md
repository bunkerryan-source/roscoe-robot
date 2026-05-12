# Session 6 — Weekly Digest + Research Threads + Monthly Rule Pass

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the system from write-only to read-back. Once a week (Saturday 07:00 LA) the bot reads the week's captures and ships a Sonnet-generated digest with a watchlist + 1–3 research-thread suggestions; tap a suggestion and Sonnet writes a synthesizing note into `_meta/research-threads/`. Once a month (day 1, 07:00 LA) the bot reads the prior month's `corrections` and proposes durable rule additions to `_meta/rules.md`, one Telegram Yes/No tap per proposal.

**Architecture:** Reuses Session 5's APScheduler + cron-coro pattern. Two new cron coros (`_weekly_digest_cron`, `_monthly_rules_cron`) registered in lifespan alongside the daily three. Sonnet 4.6 (`claude-sonnet-4-6`) used for all three Session 6 LLM calls; reads cost ~$0.25/month at projected volume. Suggestions and proposals persist in a single polymorphic `lm_proposals` table so callback handlers can recover them by id (Telegram's 64-byte `callback_data` cap forces this — UUIDs in callbacks, payload in DB). Research-thread notes write to the Dropbox-resident vault using the existing `upload_with_fallback` filer.

**Tech Stack:** Existing Python 3.11+ stack. Anthropic SDK already configured for Haiku — same client gets reused with the Sonnet model ID. APScheduler 3.10+ already a dependency. Tests: pytest, pytest-asyncio, pytest-mock, respx.

---

## Spec reference

Implements the **Session 6** entry of the [design spec](../specs/2026-05-05-personal-os-design.md) build sequence (line 297), plus the digest UX section (lines 231–241), the corrections+rules feedback loop monthly pass (line 249), and the cost-control item 5 from the cost-model table (line 283, "Monthly cost report included in weekly digest").

What this session does **not** ship (deferred per spec):

- `/find` Telegram retrieval → Session 7.
- OA-wiki feeders / forwarded-email / browser-extension ingest → Session 7.
- Git auto-push from droplet for vault `_meta/rules.md` changes → see Decision 6 below; punted to a follow-up.

What this session **does** ship:

- Saturday 07:00 LA weekly digest as a Telegram message, with watchlist + thread suggestions (Sonnet 4.6).
- Inline-keyboard handlers for "📚 Build thread: <slug>" buttons; tap → Sonnet writes the threaded note to vault and acks in Telegram.
- 1st-of-month 07:00 LA monthly rule consolidation; one Telegram message per proposed rule with Yes / No buttons.
- `lm_proposals` table persisting digest + rule proposals so callbacks can hydrate them.
- `runs.trigger` extended (already CHECK-allowed: `weekly-digest`, `monthly-rules` — no migration needed for that column).
- Monthly cost report appended to weekly digest body (rolling 30-day spend + projected monthly).

---

## Decisions baked into this plan (override before starting if you disagree)

| Decision | Choice | Why |
|---|---|---|
| Sonnet model ID | `claude-sonnet-4-6` (latest 4.X Sonnet) | Spec says "Sonnet 4.6 for weekly digest only." Same vendor, same SDK call shape as Haiku. |
| Cron timezone | America/Los_Angeles — Saturday 07:00 and 1st-of-month 07:00 | Matches existing crons; all wall-clock times are LA local. |
| Proposal persistence | Single polymorphic `lm_proposals(id uuid, kind text, payload jsonb, status text, created_at, resolved_at)` table | One migration vs. two. `kind ∈ {'research-thread', 'rule-add'}`; payload holds the suggestion text + item IDs / rule text. `status ∈ {'pending', 'accepted', 'rejected'}`. Callback IDs fit in `callback_data` (UUIDs are 36 chars, prefix ≈10, total ≈47 — under the 64-byte cap). |
| Rule proposal UX | **DEFER — pick at start of implementation.** See [Open Decision A](#open-decision-a--rule-proposal-ux-yesno-vs-yesnoedit) below. | Both paths have real tradeoffs — Yes/No is one-day work, Yes/No/Edit is a multi-day conversational-state effort. Worth thinking about with fresh eyes when implementation starts. |
| Rule write-back target | Append to **vault** `_meta/rules.md` (Dropbox path under `obsidian_vault_dropbox_path`) | Source of truth per spec line 325 is the vault copy; the repo copy is the version-controlled mirror. How the repo copy gets synced is Open Decision B below. |
| Repo-side sync of vault rules.md | **DEFER — pick at start of implementation.** See [Open Decision B](#open-decision-b--repo-side-sync-of-vault-rulesmd) below. | Manual sync is simpler operationally; auto-git-push removes a chore but adds a deploy key. Worth choosing with fresh eyes when implementation starts. |
| Watchlist source | Sonnet picks 5 items from the week's `items` (id, project, type, tags, summary, raw_text) — no heuristic pre-filter beyond date window | Sonnet has the most signal; an upstream heuristic would either be overfit or underfit. Pre-filtering would also bias against new pattern types the system hasn't learned yet. |
| Research-thread suggestion count | 1–3 suggestions per digest (Sonnet decides) | Spec says "1-3"; Sonnet is the right judge of "what clusters." If the week is sparse, fewer is honest. |
| Research-thread note format | `_meta/research-threads/<year>-W<week>-<slug>.md` with frontmatter `kind: research-thread`, `week: 2026-W18`, `items: [<id>, <id>, ...]`, body = Sonnet-written synthesis | Mirrors `_meta/weekly-digests/` convention from spec line 164. Wiki-linked item references survive vault moves. |
| Monthly cost report | Pulled from `runs.total_cost_cents` for the past 30 days (rolling) + projection (30-day spend × 12 / number-of-days-into-month for first-of-month delivery accuracy) | One source of truth; no separate cost table. Projection math is back-of-envelope but matches the spec's intent. |
| Sonnet output format | JSON Schema-validated structured output (or first-`{...}` regex fallback, as in `bot/llm.py:_extract_json`) | Same defensive parsing as Haiku — Sonnet can still wrap fences. Reuse `_extract_json`. |
| Sonnet system prompts | Two new prompt strings under `bot/digest_prompts.py` (digest + monthly rules) | Mirrors how classifier prompt lives in `bot/llm.py`. Keep them small and inspectable. |
| Cap interaction | Weekly digest + monthly rules each respect the daily cost cap. If `today_already_spent_cents >= cap` when the Sat 07:00 / 1st-of-month 07:00 jobs fire, they skip silently with a log line | Consistent with daily cron behavior. /process can still run if user wants manual override. |
| Trigger values | `weekly-digest` and `monthly-rules` (already in `runs.trigger` CHECK constraint) | No schema change needed for the `runs` table. |
| Failure mode | Each cron wrapped in try/except; log + send a "weekly digest failed — check journalctl" Telegram message rather than silently swallowing | Operator visibility. Aligns with the cron-coro pattern from Session 5. |

If you want to swap any of these, edit this section before starting Task 1.

---

## File structure (what this plan creates/modifies)

**New files:**
- `bot/digest.py` — weekly digest builder: queries items, calls Sonnet, formats the Telegram message, returns proposals to persist.
- `bot/digest_prompts.py` — Sonnet system prompts (weekly + monthly).
- `bot/research_threads.py` — research-thread note writer (Sonnet → markdown → vault upload).
- `bot/rule_consolidator.py` — monthly rule consolidation: reads corrections, calls Sonnet, returns proposals.
- `migrations/003_lm_proposals.sql` — `lm_proposals` table.
- `tests/test_digest.py`, `tests/test_research_threads.py`, `tests/test_rule_consolidator.py`, `tests/test_session6_cron.py`.

**Modified files:**
- `bot/scheduler.py` — `build_scheduler` accepts two new callables (weekly + monthly); registers `cron_weekly` (Sat 07:00 LA) and `cron_monthly_rules` (day=1, hour=7 LA).
- `bot/main.py` — register the two new cron coros in lifespan; add `_weekly_digest_cron` and `_monthly_rules_cron`; route new callback_data prefixes (`thread:<proposal_id>`, `ruleyes:<proposal_id>`, `ruleno:<proposal_id>`).
- `bot/db.py` — new helpers: `insert_proposal`, `fetch_proposal`, `update_proposal_status`, `fetch_items_for_week`, `fetch_corrections_for_month`, `fetch_runs_for_window`.
- `bot/triage.py` *(or new `bot/digest_keyboard.py`)* — keyboard builders for digest thread buttons and rule Yes/No buttons.
- `tests/test_scheduler.py` — assert 5 jobs now (3 daily + weekly + monthly), correct IDs and times.
- `tests/test_db.py` — coverage for the new fetchers.

**No env-var additions.** Weekly digest cost is bounded by per-call Sonnet input (one call/week, ≤ a week's worth of items in context); monthly rules call is even smaller (≤ 30 corrections rows). Both honor the existing daily cap.

---

## Prerequisites (operator setup before Task 1)

- [ ] **P1: Session 5 has been live on the droplet for at least a few days.** Per CLAUDE.md's standing instruction, don't start the next session until the previous one's been observed for a week of real usage. Confirm there's enough natural cron-fire history in `runs` (at least one of each of `scheduled-630`, `scheduled-1200`, `scheduled-2100`) before starting.
- [ ] **P2: Confirm Anthropic API key has Sonnet access.** Same key as Haiku; if billing tier was provisioned for Haiku-only, Sonnet calls 4xx. Run a one-off probe from the droplet: `PYTHONPATH=/opt/personal-os-v2 venv/bin/python -c "import anthropic, os; from dotenv import load_dotenv; load_dotenv('/opt/personal-os-v2/.env'); c = anthropic.Anthropic(api_key=os.environ['ANTHROPIC_API_KEY']); print(c.messages.create(model='claude-sonnet-4-6', max_tokens=20, messages=[{'role':'user','content':'reply with the word OK'}]).content[0].text)"` — expect to see "OK".
- [ ] **P3: Confirm `_meta/research-threads/` will be created on first write.** The vault uploader (`upload_with_fallback` in `bot/media.py`) creates parent paths implicitly via Dropbox `files/upload`; no manual mkdir needed. Sanity-check that no file exists at that path yet so the first write isn't an unintended overwrite.
- [ ] **P4: Create branch** — `git checkout -b session-6` from current `main`.
- [ ] **P5: Decide on first-run dates.** Day-1-of-month firing means May 12 is mid-cycle — the first real monthly run will be June 1. Saturday firing means the first real weekly digest will land on the Saturday after deploy. Both are fine; spec assumes that cadence. Verify no test data in `runs` would falsely trigger "first weekly digest after deploy is huge" (it picks the past 7 days regardless).

---

## Phase 1 — `lm_proposals` table + DB helpers

### Task 1.1 — Migration

- [ ] Create `migrations/003_lm_proposals.sql`:
  ```sql
  create table public.lm_proposals (
      id              uuid primary key default uuid_generate_v4(),
      kind            text not null
                      check (kind in ('research-thread', 'rule-add')),
      payload         jsonb not null,
      status          text not null default 'pending'
                      check (status in ('pending', 'accepted', 'rejected')),
      created_at      timestamptz not null default now(),
      resolved_at     timestamptz
  );

  create index lm_proposals_kind_status_idx
      on public.lm_proposals (kind, status, created_at desc);
  ```
- [ ] Paste into Supabase dashboard SQL Editor by hand (per [[reference-supabase-migrations]] memory).
- [ ] Verify with: `select column_name, data_type from information_schema.columns where table_name = 'lm_proposals';`

### Task 1.2 — Failing tests for DB helpers

- [ ] In `tests/test_db.py`, add tests for:
  - `insert_proposal(client, kind, payload)` returns the inserted row with id.
  - `fetch_proposal(client, proposal_id)` returns one row or None.
  - `update_proposal_status(client, proposal_id, status)` patches status + sets `resolved_at`.
  - `fetch_items_for_week(client, since, until)` — same shape as `fetch_items_for_summary` but selects `id, project, type, tags, summary, raw_text, created_at`.
  - `fetch_corrections_for_month(client, since)` — selects all corrections rows since timestamp.
  - `fetch_runs_for_window(client, since, until)` — sums for cost-report math.

### Task 1.3 — Implement helpers

- [ ] Add the six new functions to `bot/db.py`. Mirror the existing `insert_correction` / `fetch_recent_corrections` style.
- [ ] Run `pytest tests/test_db.py -q`. All new cases green.

---

## Phase 2 — Weekly digest

### Task 2.1 — Sonnet prompt + builder

- [ ] Create `bot/digest_prompts.py` with `WEEKLY_DIGEST_SYSTEM` — a concise system prompt instructing Sonnet to return JSON shaped like:
  ```json
  {
    "summary": "free-text one-liner about the week",
    "counts_by_project": {"acute": 12, "abp": 4, ...},
    "counts_by_type": {"image": 18, "todo": 5, ...},
    "top_topics": ["mcp servers", "freight pricing", "kitchen reno"],
    "watchlist": [
      {"item_id": "<uuid>", "reason": "high-value YouTube transcript on X"},
      ...
    ],
    "research_threads": [
      {"slug": "mcp-servers", "items": ["<uuid>", "<uuid>", ...], "rationale": "4 items in 2 weeks about MCP"},
      ...
    ],
    "open_todos_count": 7,
    "oldest_stale_todo": {"id": "<uuid>", "summary": "...", "age_days": 14}
  }
  ```
- [ ] Create `bot/digest.py` with `build_weekly_digest(items, runs_30d, sonnet_client) -> tuple[str, list[dict]]`:
  - Sends Sonnet the items + a small framing message.
  - Parses JSON via `_extract_json` (or a copy).
  - Formats the Telegram message text + returns the list of proposals to persist.
  - Appends the monthly cost report line at the end: `"💰 $X.XX in the last 30d (projected $Y.YY / mo)"`.

### Task 2.2 — Failing tests

- [ ] `tests/test_digest.py`:
  - Empty week → "nothing captured this week" placeholder, no Sonnet call.
  - Sonnet returns clean JSON → builder produces expected Telegram lines + the proposals list contains N research-thread entries.
  - Sonnet wraps JSON in markdown fences → builder strips and still succeeds.
  - Sonnet returns malformed JSON twice → builder raises (callers handle exception).

### Task 2.3 — Implement + green

- [ ] Implement `bot/digest.py` until tests pass.
- [ ] Run `pytest tests/test_digest.py -q`.

---

## Phase 3 — Research-thread builder (callback handler)

### Task 3.1 — Note writer

- [ ] Create `bot/research_threads.py` with `write_research_thread(sonnet_client, items, slug, week_label, vault_root, dropbox_client) -> str`:
  - Calls Sonnet with the items' summaries + raw_text + a synthesis prompt.
  - Builds markdown: frontmatter (`kind: research-thread`, `week`, `items` list) + Sonnet body + wiki-links to each item's note.
  - Uploads to `<vault_root>/_meta/research-threads/<year>-W<week>-<slug>.md` via `upload_with_fallback`.
  - Returns the vault path string.

### Task 3.2 — Failing tests

- [ ] `tests/test_research_threads.py`:
  - Happy path: 4 items in, mocked Sonnet returns a body, function writes a markdown file with the right frontmatter and 4 `[[item-id]]` wiki-links in the body.
  - Slug sanitization: spaces/special chars → kebab-case.
  - Sonnet failure → exception bubbles up (caller logs).

### Task 3.3 — Wire callback handler in `bot/main.py`

- [ ] Add a new callback_data prefix `thread:<proposal_id>`. In `_handle_triage_callback`, route to `_handle_build_thread(proposal_id, chat_id, message_id, callback_id)`.
- [ ] `_handle_build_thread`:
  - Loads the proposal (`fetch_proposal`).
  - If status != 'pending', bail with "this thread was already built/rejected" (idempotency guard, same pattern as triage handlers — see [[feedback-telegram-inline-keyboards]]).
  - Loads the items referenced in payload.
  - Calls `write_research_thread(...)`.
  - Updates proposal to `status='accepted', resolved_at=now()`.
  - Replies in Telegram with the vault path.

### Task 3.4 — Tests + green

- [ ] Add callback routing test in `tests/test_session6_cron.py` (or `test_webhook.py`).
- [ ] Run targeted test files; then full sweep.

---

## Phase 4 — Weekly digest cron coro

### Task 4.1 — Cron coro

- [ ] In `bot/main.py`, add `async def _weekly_digest_cron()`:
  - Snapshot today's already-spent cents; if ≥ cap, log + skip.
  - Compute the past-week LA-local window (`now - 7d` → `now`).
  - Fetch items for that window via `fetch_items_for_week`.
  - Fetch the last 30 days of `runs` for the cost report.
  - Call `build_weekly_digest(items, runs_30d, sonnet_client)` in a thread.
  - For each proposal in the returned list, `insert_proposal('research-thread', payload)` — collect the IDs.
  - Build the inline-keyboard with one "📚 Build thread: <slug>" button per proposal, callback_data `thread:<proposal_id>`.
  - Send the digest message + keyboard.
  - Write a `runs` row with `trigger='weekly-digest'`, `items_processed=0` (digest is read-only over items), `total_cost_cents` = the Sonnet call's cost.

### Task 4.2 — Scheduler entry

- [ ] Update `bot/scheduler.py` to accept `weekly_job` and register `cron_weekly` at `CronTrigger(day_of_week='sat', hour=7, minute=0, timezone=...)`, id=`cron_weekly`.
- [ ] Update `bot/main.py`'s `lifespan` to wire it.
- [ ] Tests in `tests/test_scheduler.py` — assert job exists at right day/time/timezone.

---

## Phase 5 — Monthly rule consolidation

### Task 5.1 — Sonnet prompt + consolidator

- [ ] Add `MONTHLY_RULES_SYSTEM` to `bot/digest_prompts.py` — instructs Sonnet to read corrections + current `_meta/rules.md` and return JSON like:
  ```json
  {
    "proposals": [
      {"rule": "Photos with kitchen tile → project: lake-arrowhead",
       "rationale": "5 corrections in the past month moved kitchen-tile photos from claude-build to lake-arrowhead"},
      ...
    ]
  }
  ```
- [ ] Create `bot/rule_consolidator.py` with `propose_rules(sonnet_client, corrections, current_rules_md) -> list[dict]`.

### Task 5.2 — Cron coro

- [ ] In `bot/main.py`, add `async def _monthly_rules_cron()`:
  - Cap check (same as weekly).
  - Past-month window; fetch corrections.
  - Read current vault `_meta/rules.md` via Dropbox (existing client; new fetch helper if needed).
  - Call `propose_rules(...)`.
  - For each proposal: `insert_proposal('rule-add', payload={'rule': ..., 'rationale': ...})`, send a Telegram message with Yes/No keyboard (`ruleyes:<id>` / `ruleno:<id>`).
  - Write a `runs` row with `trigger='monthly-rules'`.

### Task 5.3 — Yes/No callback handlers

- [ ] In `bot/main.py`, route `ruleyes:<proposal_id>` and `ruleno:<proposal_id>`:
  - `_handle_rule_yes`: load proposal, idempotency check, append the rule line to vault `_meta/rules.md` (read → append → upload), mark `status='accepted'`, reply "✅ rule added; sync the repo file from the vault when convenient".
  - `_handle_rule_no`: mark `status='rejected'`, reply "👍 dismissed".

### Task 5.4 — Scheduler entry

- [ ] Update `build_scheduler` to accept `monthly_rules_job`; register `cron_monthly_rules` at `CronTrigger(day=1, hour=7, minute=0, timezone=...)`, id=`cron_monthly_rules`.
- [ ] Update lifespan + tests.

---

## Phase 6 — Deploy + smoke test

### Task 6.1 — Push branch

- [ ] `git push -u origin session-6`.

### Task 6.2 — Apply migration

- [ ] Paste `migrations/003_lm_proposals.sql` into Supabase SQL Editor.
- [ ] Verify `lm_proposals` table + index exist.

### Task 6.3 — Deploy

- [ ] On droplet:
  ```
  cd /opt/personal-os-v2 && git fetch && git checkout session-6 && git pull && systemctl restart personal-os-v2 && sleep 3 && journalctl -u personal-os-v2 -n 30 --no-pager | grep -E "APScheduler|jobs:"
  ```
- [ ] Confirm five jobs registered: `cron_morning`, `cron_noon`, `cron_evening`, `cron_weekly`, `cron_monthly_rules`. Next-fire times should make sense (next Saturday 07:00 LA; next 1st-of-month 07:00 LA).

### Task 6.4 — Smoke test weekly digest manually

- [ ] On droplet:
  ```
  PYTHONPATH=/opt/personal-os-v2 /opt/personal-os-v2/venv/bin/python -c "
  import asyncio
  from dotenv import load_dotenv; load_dotenv('/opt/personal-os-v2/.env')
  from bot.main import _weekly_digest_cron
  asyncio.run(_weekly_digest_cron())
  "
  ```
- [ ] Expect a digest message in Telegram with a few thread-suggestion buttons. Tap one — confirm a `_meta/research-threads/...md` lands in the vault (visible in Obsidian after Dropbox sync) and the bot replies with the path.

### Task 6.5 — Smoke test monthly rules manually (optional but recommended)

- [ ] Same pattern as 6.4 but call `_monthly_rules_cron`. Expect 0–N rule proposals (depends on how many corrections exist). Tap Yes on one to confirm `_meta/rules.md` gets the new line.

### Task 6.6 — Verify run rows

- [ ] `select trigger, total_cost_cents, started_at from runs where trigger in ('weekly-digest', 'monthly-rules') order by started_at desc limit 5;` — confirm both trigger values land correctly.

---

## Phase 7 — Merge

### Task 7.1 — Update project CLAUDE.md

- [ ] Bump "Current state" header to Session 6 shipped paragraph. Mention five cron jobs total, weekly digest + monthly rules + research-thread builder.
- [ ] Update operator runbook with Saturday digest + monthly-1st rule pass routines.
- [ ] Add new key invariants:
  - `lm_proposals` is the source of truth for what's been suggested/accepted/rejected; never delete rows (audit trail).
  - Vault `_meta/rules.md` is the live classifier input; repo copy is updated manually after each monthly cycle.
- [ ] Test count refreshed.
- [ ] "Things NOT done yet" — remove weekly digest + monthly rules entries; add explicit "Git auto-push of rules.md is manual."

### Task 7.2 — Memory updates

- [ ] Save a `reference_lm_proposals.md` memory documenting the polymorphic-table pattern (kind + payload) and why it beats per-kind tables for Telegram-callback workflows.
- [ ] If any non-obvious Sonnet quirk surfaces during smoke test (output format, refusals, fence wrapping), save a `feedback_sonnet_*.md` memory.

### Task 7.3 — Fast-forward merge

- [ ] `git checkout main && git merge --ff-only session-6 && git push origin main`.
- [ ] On droplet: switch back to main, redeploy, confirm five jobs registered.

### Task 7.4 — Branch cleanup (optional)

- [ ] `git branch -d session-6; git push origin --delete session-6` (PowerShell, sequential — no `&&`).

---

## Rollback plan

- **Disable Session 6 jobs only:** comment out the two new `add_job` calls in `bot/scheduler.py`, push, redeploy. Three daily jobs continue running.
- **Disable everything Session 6 brought:** revert the session-6 merge commit on main, `git push`, redeploy. The `lm_proposals` table can stay (it's append-only, no effect on Session 5 paths).
- **Roll back vault `_meta/rules.md` if a bad rule was appended:** vault file is hand-editable; just open in Obsidian and delete the line.

---

## Open decisions (settle at start of implementation, before Phase 5)

### Open Decision A — Rule-proposal UX: Yes/No vs Yes/No/Edit

The spec says monthly rule proposals show **Yes / No / Edit** buttons (line 249). Telegram's inline keyboards have no native inline-text-edit primitive, so "Edit" requires a stateful conversation: the bot has to remember which proposal the next user message is editing.

**Path A1 — Yes / No only** (the "Recommended" path during plan review):
- *Build cost:* ~1 day. Two callback prefixes (`ruleyes:<id>`, `ruleno:<id>`), idempotency guard on each, two simple handlers. Fully stateless — the proposal id is in the callback, the payload lives in `lm_proposals`.
- *Editing path for the user:* open `_meta/rules.md` in Obsidian (or the repo) and hand-edit. No bot involvement.
- *Implication:* small fraction of "almost right" rules will be rejected (No-taps) and need re-proposing next month, or accepted as-is and hand-edited later in the markdown. Most rules are short enough that "edit" is cheap if you do it in Obsidian directly.
- *Risk:* if the typical proposal is 60% right and needs tweaking, this creates friction.

**Path A2 — Yes / No / Edit** (the spec wording):
- *Build cost:* ~2–3 days. Edit-tap puts the chat into a per-user conversation state: bot replies "Send me the corrected rule text. Reply /cancel to abort.", waits for the next user message, validates, persists, sends a final confirmation. State lives in `lm_proposals` (e.g., `status='editing'` with `editing_started_at` timestamp + a "current editor" guard so only the most recent Edit tap is active).
- *Plumbing:* webhook needs to recognize a free-text message *as* an edit reply rather than a capture intake. Simplest signal: if the user has a `status='editing'` proposal whose `editing_started_at` is < 10 minutes old, the next text message is treated as the edit. Older than 10 min → it's an intake again.
- *Implication:* much closer to the spec's intent. Higher accept-rate on the first proposal. Friction lives in code complexity, not user steps.
- *Risk:* the editing-state window is one more thing that can leak (orphan editing rows, race conditions with simultaneous intake messages). Also a behavioral change to the capture path that affects every text message during an editing window — must be tested carefully.

**To decide at implementation start:** how often does an "almost-right" proposal happen in practice? If most rules are simple categorical mappings ("X → project Y"), Yes/No covers 95% of cases. If proposals tend to be multi-clause and nuanced, Edit pays for itself.

---

### Open Decision B — Repo-side sync of vault `_meta/rules.md`

When the user taps Yes on a rule proposal, the bot appends the rule line to the **vault** copy of `_meta/rules.md` (the live classifier input). The **repo** copy is the version-controlled mirror — spec line 325 calls the vault copy the working copy and the repo copy the source of truth committed each time the monthly pass updates.

**Path B1 — Manual sync** (the "Recommended" path during plan review):
- *Bot does:* append line to vault file via Dropbox upload. Reply: "✅ rule added to vault; commit to repo when convenient."
- *User does:* once a month, after the rule cycle wraps, open the vault file, copy the new lines into the repo file, `git commit`, `git push`. Or `git pull` on the workstation if they happen to have edited the repo copy directly.
- *Build cost:* trivial — just the vault append, which we need anyway.
- *Risk:* the repo copy will drift behind the vault copy. Months later, if no one syncs, the repo copy will not reflect rules actively in use. Severity is low because the **vault** copy is what the classifier reads, and the repo copy is just for version control.

**Path B2 — Auto-git-push from droplet:**
- *Bot does:* append line to vault, then `git add /opt/personal-os-v2/_meta/rules.md` (NOT the vault — the repo's `_meta/`)... wait, that's a problem. The repo's `_meta/rules.md` is the *seed* file loaded at boot (see `bot/main.py` line 56). The vault's `_meta/rules.md` is the file in Dropbox the user edits. Auto-sync would mean: bot also writes the same line to the repo's file, then commits and pushes. This requires:
  - SSH deploy key on droplet at `/root/.ssh/` with push access to the GitHub repo.
  - `git config user.email/user.name` set on the droplet.
  - Branch policy decision: push to main? Or push to a `bot-updates` branch and require a PR? Direct-to-main is simpler; PR is safer.
  - A re-deploy step or a `git pull` on the droplet to make the bot read the updated repo file next boot. (The bot loads `RULES_MD` once at process start in `bot/main.py:56`, so a push without restart means the running process still has the stale repo file in memory — but it reads the vault file fresh on each batch via the classifier system prompt builder. Need to double-check that path.)
- *Build cost:* ~half a day for the git plumbing, plus the deploy-key dance with GitHub.
- *Risk:* bot can now push to your repo. If the bot misbehaves (loops, mis-formatted rules, bad commit messages), it shows up in `git log` on main. Mitigations: pre-commit hook on the repo side, or commit messages with a distinct prefix you can filter out.

**To decide at implementation start:** how often will rule additions happen? If it's 0–3 per month, manual sync is fine. If it's 5+, the chore adds up.

---

## Other open questions (lower stakes)

- **Cost report math for "projected $/mo".** Spec says "rolling 30-day spend + projected monthly." If we're mid-month, `30d × 12 / days_so_far` overprojects when usage was front-loaded. Simpler: just show the rolling 30d as the projected monthly. Defer the refinement.
- **Sonnet system-prompt token budget.** A week of 200 items × ~50 tokens of summary each = ~10k tokens of input. Well within Sonnet's window. If it ever bites, summarize-then-summarize.
- **Idempotent re-run.** If `_weekly_digest_cron` runs twice in the same hour (manual trigger + missed cron retry), we'd insert duplicate proposals. Likely fine for v1; pragmatic guard would be "skip if a `weekly-digest` run row already exists for this LA-week." Defer unless it bites.
- **Edge case — first weekly digest after deploy.** If session-6 deploys on a Friday, Saturday's digest covers Saturday → Saturday. Result: a thin digest. Acceptable; recovers naturally.
