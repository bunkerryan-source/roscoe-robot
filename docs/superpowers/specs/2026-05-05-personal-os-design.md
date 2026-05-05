# Personal OS — Design

**Date:** 2026-05-05
**Owner:** Ryan Bunker
**Status:** Approved design, ready for implementation planning
**Prior context:** `personal-os-handoff.md` (Phase 1 — Telegram bot + DigitalOcean droplet — complete)

## TL;DR

A personal capture-and-route system. Single ingest point (Telegram bot "Roscoe") collects everything thrown at it during the day — text, photos, links, voice memos. Three times daily, an autonomous batch on the DigitalOcean droplet calls the Anthropic API directly to classify, enrich, summarize, and file each item to its right home (Obsidian vault, Dropbox, Todoist, Supabase). Telegram summaries at 6:30 AM and 9:00 PM with a tap-to-refile triage flow. Weekly digest on Saturdays. A learning loop turns user corrections into durable rules. Target cost: $5-8/month.

## Goals

- One frictionless capture point — anything I think of, I forward to Roscoe.
- Autonomous classification and filing — no human-in-the-loop on routine routing.
- Findable later via `/find <query>` in Telegram and Obsidian native search.
- Weekly synthesis (digest + research-thread clustering) so the system is read-back, not just write.
- Stay within $5-8/month at realistic capture volume (~30 items/day).

## Non-goals (v1)

- Real-time / per-message classification. Cost and interruption.
- Multi-source ingest (forwarded email, browser extension). Schema-compatible, deferred.
- Public dashboard / multi-user. Locked to one Telegram user ID.
- OA-wiki integration. Later layer once basics are solid.
- Use of Opus. Haiku 4.5 by default; Sonnet 4.6 for weekly digest only.

## Architecture

```
[Telegram → Roscoe bot]
    ↓ webhook (HTTPS)
[FastAPI bot on droplet]   → media downloaded to Dropbox immediately
    ↓
[Supabase: items table, status=pending]
    ↓ cron 6:30 AM / 12:00 PM / 9:00 PM   +   /process on demand
[Droplet processor → Anthropic API (Haiku 4.5)]
    ↓
 [Obsidian vault]   [Dropbox]   [Todoist]   [Supabase: status=processed]
    ↓ at 6:30 AM and 9:00 PM
[Telegram summary B+ → optional review/triage]
    ↓ Saturday 7 AM
[Weekly digest (Sonnet 4.6)]
    ↓ 1st of each month
[Rule-consolidation pass (Sonnet 4.6)]
```

Three layers, one capture point:

1. **Capture (Telegram bot, dumb).** Writes everything to Supabase `items` queue. Downloads Telegram media to Dropbox before the URL expires. No Claude calls.
2. **Process (batched, 3x/day + on-demand).** Drains the queue. Per item: enrich, classify, file, mark processed. Cost-controlled. Autonomous.
3. **Retrieve (Telegram `/find`, Obsidian search, weekly digest).** The payoff layer.

## Data model (Supabase)

Three tables. No per-category tables (no `youtube_queue`, no `xpost_queue`, no `design_inspo`). Tags + project + type are the axes.

### `items` (canonical row per capture)

| column | type | purpose |
|---|---|---|
| id | uuid | PK |
| created_at | timestamptz | when bot received |
| processed_at | timestamptz | null until processed |
| status | text | `pending` / `processed` / `needs_review` / `failed` |
| source | text | `telegram` (only one in v1) |
| source_message_id | text | Telegram message ID, for linking back |
| raw_text | text | original message text or caption |
| media_type | text | `text` / `image` / `video` / `voice` / `link` / `forward` |
| media_dropbox_path | text | null for text-only |
| media_telegram_file_id | text | original Telegram file reference |
| project | text | one of: `acute`, `abp`, `lake-arrowhead`, `church`, `claude-build`, `design`, `personal` |
| subdomain | text | optional (e.g., `c3bank` under `abp`) |
| type | text | `article` / `video` / `image` / `todo` / `idea` / `voice` / `link` |
| tags | text[] | Claude-assigned, free-ish from a growing controlled vocab |
| visual_subtype | text | for images only (`hero`, `nav`, `pricing`, `dashboard`, `typography`, `color-palette`, `branding`, `mobile`, etc.) |
| summary | text | Claude's generated summary, full-text searchable |
| obsidian_path | text | path to the `.md` note in vault |
| todoist_task_id | text | null unless `type=todo` |
| classified_by | text | model id |
| confidence | float | 0-1 |
| api_cost_cents | int | rolled-up token cost for this item |
| error | text | null unless `failed` |

### `corrections` (feedback loop)

| column | type | purpose |
|---|---|---|
| id | uuid | PK |
| item_id | uuid | FK to `items` |
| original_class | jsonb | `{project, type, tags, visual_subtype}` as classified |
| corrected_class | jsonb | what the user changed it to |
| created_at | timestamptz | |

### `runs` (observability)

| column | type | purpose |
|---|---|---|
| id | uuid | PK |
| started_at | timestamptz | |
| completed_at | timestamptz | |
| trigger | text | `scheduled-630` / `scheduled-1200` / `scheduled-2100` / `on-demand` / `weekly-digest` / `monthly-rules` |
| items_processed | int | |
| items_needs_review | int | |
| items_failed | int | |
| total_cost_cents | int | |
| summary_message_id | text | Telegram message ID of the summary, for replies |

## Project taxonomy (the `project` enum)

Seven projects. Tags carry orthogonal axes (talk, ai, mcp, freight, video-to-watch, etc.).

| project | scope |
|---|---|
| `acute` | Acute Logistics — sales, ops, freight content, customer research |
| `abp` | ABP Capital and C3bank (split via `subdomain` if needed later) |
| `lake-arrowhead` | Cabin and personal real estate |
| `church` | Come Follow Me lessons, talks, gospel study |
| `claude-build` | Tools and automations being built (incl. this Personal OS) |
| `design` | Visual inspiration library — heroes, nav, typography, brand, etc. |
| `personal` | Catch-all — surfing, woodworking, family, miscellaneous |

## Capture (bot intake)

Intake is intentionally dumb — fast, cheap, reliable. No classification, no Claude calls.

1. Verify `sender == MY_TELEGRAM_ID`. Reject everything else.
2. Insert row to `items` with `status=pending`, `raw_text`, `source_message_id`.
3. If photo / video / voice: download from Telegram immediately, upload to Dropbox at `/personal-os-inbox/YYYY-MM-DD/<item-id>.<ext>`, store `media_dropbox_path`. Telegram file URLs expire — this must happen at intake.
4. Ack with 👍 emoji within <2 seconds.

## Processing (per item, in batch)

Triggered by cron at 6:30 / 12:00 / 21:00, plus `/process` on demand.

For each `status=pending` item:

1. **Enrich** based on `media_type`:
   - `link` (X/Twitter, articles): fetch OG metadata (free). For YouTube specifically: pull transcript via `youtube-transcript-api` (free).
   - `image`: prepare for vision call — but skip vision entirely if caption is decisive (caption length > 8 chars and matches a known project keyword).
   - `voice`: Whisper API transcribe (cheap), store as `raw_text`.
   - `text` / `forward`: nothing.

2. **Classify** via a single Haiku 4.5 call. The system prompt (cached via Anthropic prompt caching — required) contains:
   - The 7-project list with descriptions.
   - Current tag vocabulary (growing list).
   - Contents of `_meta/rules.md` (learned rules).
   - Last 30 rows from `corrections` (few-shot signal).
   - Strict JSON output schema: `{project, type, tags, visual_subtype, summary, confidence}`.

3. **File** to all destinations:
   - Write `.md` note to `[vault]/personal-os/<project>/<YYYY-MM-DD>-<slug>.md` with frontmatter (id, created, type, project, tags, visual_subtype, dropbox path, source, caption, confidence) and a short body (Claude's summary + caption + image embed if applicable).
   - Move media (if any) from `/personal-os-inbox/...` to its post-classify Dropbox home: `/inspiration/<project>/` for design captures or `/projects/<project>/media/` for project-scoped media.
   - If `type=todo`: create Todoist task under the existing `#[Project]` parent task convention (`#Acute`, `#ABP`, etc.). Store the task id.
   - Update the `items` row: `status=processed`, all classification fields, `obsidian_path`, `api_cost_cents`.

4. **Track cost**: every API call's token usage rolls into `api_cost_cents` on that item. The classification call's cached input tokens count at the cached rate (~10% of normal).

If an item fails twice: `status=failed`, error captured, surfaces in the next daily summary as "N errors."

## Storage destinations

- **Supabase**: canonical data — `items`, `corrections`, `runs`. Source of truth.
- **Obsidian vault**: human-readable `.md` note per item, flat-by-project + tags. Each project folder has an `index.md` with Dataview queries (e.g., `design/index.md` shows "all hero sections," "items added this week"). `_meta/` holds system files: `rules.md`, `corrections.md`, `weekly-digests/`, `research-threads/`.
- **Dropbox**: all media. Path-mirrored to project after classification.
- **Todoist**: todos only, under existing `#[Project]` parent convention.

## UX: Daily summary (B+ format)

Sent at 6:30 AM and 9:00 PM after the corresponding batch. The 12:00 PM batch is silent.

```
6:30 AM — 12 items processed
ACUTE (3)
  • Todo: "follow up with Walmart contact" → Todoist #Acute
  • Idea: "freight bundling pitch" → Obsidian/acute
  • Article: "LTL pricing trends 2026" → Obsidian/acute
DESIGN (4)
  • Hero section, dark gradient → Dropbox/inspiration/design
  • Color palette, soft beige → Dropbox/inspiration/design
  • Nav pattern, sticky w/ blur → Dropbox/inspiration/design
  • Typography, serif/sans pair → Dropbox/inspiration/design
PERSONAL (5)
  • ...
1 needs review · $0.18

[ Review items ]   [ All good ]
```

Tapping `[Review items]` enters a triage mini-flow. Bot sends one item at a time as a card:

```
[thumbnail if image]
Filed as: design / hero-section / claude-build
Tags: dark-gradient, brutalist
Summary: <Claude's 1-2 sentence summary>

[ ✓ Right ]   [ ↻ Refile ]   [ 🗑 Delete ]   [ Skip ]
```

- `Right` → confirmed, next item.
- `Refile` → bot offers project + type buttons; pick → refiled, **correction logged**, next item.
- `Delete` → trash, next.
- `Skip` → leave as-is, next.

The flow has a hard end ("review complete — N items checked"). User can quit any time with `/done`. Unaddressed items stay filed-as-classified.

## UX: `/find <query>` (any time)

Format A+C: one-shot top-5 with conversational follow-up.

```
/find soft beige bathroom

[5 thumbnails / cards]
1. lake-arrowhead · image · "soft beige stone vanity" — May 4
   [Open in Obsidian] [Open in Dropbox]
2. design · image · "warm neutral palette study" — Apr 18
   ...
```

User can reply conversationally: "the second one — what was the takeaway?" Bot does a follow-up Haiku call against the stored `summary` text and answers in-thread.

Modifiers supported in the query string:
- `/find type:video memory in claude code`
- `/find project:design hero sections`
- `/find tag:mcp`

Search backend: Supabase full-text index over `summary + tags + raw_text + caption`, filtered by any modifiers.

## UX: Weekly digest (Saturday 7 AM, Sonnet 4.6)

Telegram message:
- "This week — 87 items captured."
- Counts by project and type.
- Top 3 emerging topics (highest tag-frequency this week).
- **Watchlist** — 5 items most worth your time (highest-value YouTube transcripts, most-relevant articles given current projects).
- Open todos count + oldest stale todo.
- 1-3 suggested **research threads**: "you've saved 4 things about MCP servers in 2 weeks — bundle as `_meta/research-threads/2026-W18-mcp-servers.md`? [Yes / No]"

Tapping `Yes` triggers a Sonnet call that generates the threaded note linking the items.

## Corrections + rules feedback loop

Two layers of learning:

1. **Real-time, every batch.** Classifier system prompt includes the last 30 rows of `corrections` as few-shot examples. New corrections influence the very next batch.

2. **Monthly consolidation, 1st of each month at 7 AM.** Sonnet reads the past month's corrections and proposes durable rule updates as a diff to `_meta/rules.md`. Telegram message: "I noticed [pattern] — add rule? [Yes / No / Edit]." Approved rules commit to the vault file, which the classifier reads at every run.

`_meta/rules.md` is human-readable markdown. Hand-editable directly. Example entries:

> - Photos with kitchen tile → `project: lake-arrowhead`.
> - X links containing "claude code" or "anthropic" → `project: claude-build, tag: ai`.
> - Voice memos starting with "remind me" → `type: todo`.

## Failure modes

| failure | behavior |
|---|---|
| Telegram media URL expired before Dropbox upload | Item marked `failed`, bot replies "couldn't save your photo, please resend." Should be rare since intake is fast. |
| Dropbox API down at intake | 3x retry with exponential backoff. If still failing, save raw bytes to local disk on droplet at `/opt/personal-os/dropbox-pending/`, retry at next batch. Capture stays alive. |
| Anthropic API rate limit / 5xx at batch | Exponential backoff. If a run can't complete, unfinished items stay `pending`, retried at the next scheduled batch. No data loss. |
| Single item classification crashes | Logged, item marked `failed`, doesn't block the rest of the run. Surfaces as "N errors" in next summary. |
| Daily cost cap hit (default $1.00) | Halt processing immediately. Telegram alert: "halted at $1.00 — N items remain pending." Items stay pending. User raises the cap or runs `/process` manually. |
| Supabase unavailable | (Out of v1.) Add SQLite fallback later only if it ever bites. |

## Cost model + controls

Target: $5-8/month at ~30 items/day.

Rough math at that volume, with Haiku 4.5 throughout and prompt caching enabled:
- ~$0.15-0.25/day for classification + enrichment + summary generation.
- ~$0.20/month for Saturday weekly digest (Sonnet).
- ~$0.05/month for monthly rule pass (Sonnet).

Five cost controls, all built day-one (not deferred):

1. **Prompt caching on the classifier system prompt.** Mandatory. Cuts repeated input cost by ~80-90%. Without this, the budget is unrealistic.
2. **Skip vision when caption is decisive.** If caption length > 8 chars and matches a known project keyword, classify from caption alone — no vision call.
3. **Per-item `api_cost_cents` tracking.** Daily summary footer shows total cost ("$0.18 today").
4. **Daily soft cap, default $1.00.** On hit: auto-halt, Telegram alert, items stay pending.
5. **Monthly cost report** included in weekly digest (rolling 30-day spend + projected monthly).

## Build sequence

Refines the handoff doc's Sessions 2-5 with what we now know.

**Session 2 — Capture pipe to Supabase + Dropbox.** Create the three tables. Update `bot.py` to write to `items` and save media to Dropbox. No processing yet. Ack with 👍. Run live for one full week to validate intake reliability across all media types.

**Session 3 — Processing skill, manually triggered.** Build the classifier prompt, seed `_meta/rules.md` and the tag vocab, build enrichment functions (YouTube transcript, OG fetch, Whisper), build filers (Obsidian writer, Todoist creator, Dropbox mover). `/process` Telegram command runs a one-shot batch from Claude Code. Validate accuracy on real data for one full week. **Refine taxonomy from real captures, not from theory.**

**Session 4 — Daily summary + B+ triage flow.** Bot sends formatted summary at 6:30 / 21:00. Inline-keyboard handlers for `[Review items]`, refile dialog, corrections write to `corrections` table.

**Session 5 — Autonomy.** Cron on droplet calls Anthropic API directly with the `process-inbox` skill content embedded as the system prompt. 12:00 PM silent run. Daily cost cap + cost tracking enforced.

**Session 6 — Weekly digest + research threads + monthly rule pass.** Saturday 7 AM digest (Sonnet). Monthly rule consolidation. Research-thread clustering.

**Session 7 (later, when system is mature)** — `/find` polish, OA-wiki feeders, ingest from beyond Telegram (forwarded email, browser extension).

Each session is shippable on its own. Don't start session N+1 until N has been live for at least a week.

## Out of scope (v1)

- OA-wiki integration. Defer until the basics are solid; design is wiki-compatible.
- Email forward ingestion. Schema-compatible (just another `source` value); build later.
- Browser extension. Same.
- Sharing items with Matt or team.
- Public web dashboard.
- Local SQLite fallback for Supabase outages.
- Search via embeddings / semantic similarity. Full-text + tags first; semantic later if needed.

## Open items (to be settled during implementation, not blockers)

- **Obsidian vault location on the droplet.** The vault lives on the user's local machines (likely Dropbox-synced). The droplet's batch processor needs write access to the vault. Likely answer: vault is in Dropbox, droplet writes via Dropbox API to the vault folder, local Obsidian app sees changes via Dropbox sync. To be confirmed when implementing Session 3.
- **Dropbox auth on the droplet.** Long-lived access token vs. proper OAuth refresh flow. Refresh flow is the right answer; details settled in Session 2.
- **Whisper provider.** OpenAI Whisper API is the obvious default. Worth a quick check on cost vs. running `whisper.cpp` locally on the droplet.
- **Telegram bot send-message capability.** Already in scope per handoff Session 4. Confirms that the same bot token can both receive and send.
- **Privacy boundary.** Don't send privileged client material to Roscoe (it ends up at Anthropic). One-line note in the user's own README at implementation time.

## Notes for implementation

- The `process-inbox` skill should be **portable**. Even though Sessions 3-4 manually trigger it via Claude Code, Session 5 has the droplet calling the Anthropic API directly with the skill content as system prompt. Build for portability from the start — no reliance on Claude Code-specific tools or paths inside the skill body.
- The classifier prompt is the single most important artifact. Iterate on it with real captures during Session 3's one-week live run before locking it in.
- The `rules.md` file is checked into the skill repo (not just the vault) so it's version-controlled. Vault copy is the working copy; skill repo copy is the source of truth committed each time the monthly pass updates it.
