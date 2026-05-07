# Session 3 — Process Pipe Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Drain the `items` queue. For each pending row: enrich it, classify it via Anthropic API (Haiku 4.5 with prompt caching), file it to Obsidian + Dropbox + Todoist, mark it processed in Supabase. Triggered manually by `/process` in Telegram. No cron, no daily cost cap, no triage UI yet — those come in Sessions 4-5.

**Architecture:** A new `bot/processor.py` module orchestrates the pipeline. Per item, it calls enrichment helpers (OG fetch, YouTube transcript, Whisper) → classifier (`bot/llm.py` wraps Anthropic API with prompt caching) → filers (`bot/filers.py` writes Obsidian note via Dropbox API, creates Todoist task, moves Dropbox media). The classifier system prompt is assembled fresh each run from a static block (project list + tag vocab + rules) plus a dynamic block (last 30 corrections). `bot/main.py` adds a `/process` command handler that schedules a run as a FastAPI background task. The processor logs a row to `runs` and replies to Telegram with a one-line summary when it finishes.

**Tech Stack:** Python 3.11+, anthropic 0.39+ (official SDK with prompt caching), youtube-transcript-api, openai 1.50+ (Whisper), dropbox SDK (already pinned), httpx (already pinned for OG fetch and Todoist REST). Tests: pytest, pytest-asyncio, pytest-mock, respx — already configured.

---

## Spec reference

This plan implements the **Processing (per item, in batch)**, **Storage destinations**, **Project taxonomy**, and **Cost model + controls** sections of [the design spec](../specs/2026-05-05-personal-os-design.md), constrained to what Session 3 delivers per the **Build sequence** section.

What this session does **not** ship (deferred per spec):

- Cron / scheduled batches → Session 5
- Daily summary message + B+ triage flow + corrections UI → Session 4
- Daily cost cap enforcement → Session 5
- Weekly digest + research threads → Session 6
- Monthly rule consolidation → Session 6

What this session **does** ship:

- Working classifier on real captures
- Manual `/process` trigger from Telegram
- All filers operating end-to-end
- Per-item cost tracking populated on `items.api_cost_cents`
- Per-run row written to `runs` table

---

## Decisions baked into this plan (override before starting if you disagree)

These resolve the spec's "Open items" plus a few new ones surfaced by Session 2's deploy reality.

| Decision | Choice | Why |
|---|---|---|
| Classifier model | `claude-haiku-4-5-20251001` | Per spec |
| Prompt caching | Mandatory; static system block cached | Per spec — without it, budget unrealistic |
| Voice transcription | OpenAI Whisper API (`whisper-1`) | $0.006/min; one HTTP call. whisper.cpp is free but adds 1-2GB deps + CPU load to a small droplet |
| Obsidian vault location | `[Dropbox]/Apps/roscoe-robot/personal-os/` | Stays inside existing "App folder" scope — no Dropbox permission upgrade needed; user points local Obsidian at this path |
| `_meta/rules.md` location | In repo at `_meta/rules.md`, read by processor at runtime | Vault sync deferred to Session 6's monthly rule pass |
| `_meta/tag_vocab.md` location | Same as above | Same |
| YouTube transcripts | `youtube-transcript-api` (free, scrapes public captions) | No API key needed |
| OG metadata | Hand-rolled httpx fetcher + `<meta>` regex | Avoids an extra dep; OG parsing is ~30 lines |
| Todoist auth | Personal API token (Settings → Integrations → API token) | Single-user system; OAuth overkill |
| Todoist project structure | Existing `#[Project]` parent tasks already in user's Todoist (`#Acute`, `#ABP`, etc.) | Per spec; user creates any missing parents during prereqs |
| `/process` mechanism | Bot schedules a FastAPI background task that runs `bot.processor.run_batch()` in-process | Same droplet, same systemd service, no new infra |
| Telegram reply format | One-line summary on completion: `"processed N items in Xs · $0.YZ · M needs review"` | Detailed B+ format is Session 4 |
| Vision skip threshold | Caption length > 8 chars AND matches a known project keyword | Per spec, exact rule |
| Cost tracking unit | Cents (integer), 1 cent = 0.01 USD; round half-up at the item level | Matches existing `api_cost_cents` column |

If you want to swap any of these, edit this section before starting Task 1.

---

## Prerequisites (one-time setup the operator does before starting Task 1)

These are operator actions, not code tasks. Each yields a value that goes into `.env`.

- [ ] **P1: Get an Anthropic API key.** Sign in at https://console.anthropic.com → Settings → API Keys → Create Key. Name it `roscoe-robot`. Save:
  - `ANTHROPIC_API_KEY` (starts with `sk-ant-`)
  - **Also load $20-25 of credits** in Billing if the account is empty. At ~30 items/day with prompt caching, $5/month covers Haiku classification but you want a buffer for the Sonnet weekly digest and any one-off testing.

- [ ] **P2: Get an OpenAI API key (Whisper).** Sign in at https://platform.openai.com → API keys → Create new secret key. Name it `roscoe-robot-whisper`. Save:
  - `OPENAI_API_KEY` (starts with `sk-proj-` or `sk-`)
  - **Load $5 of credits** in Billing. At ~5 voice memos/day × 30 sec each, that's ~$0.05/month — $5 lasts a very long time.

- [ ] **P3: Get a Todoist API token.** Open Todoist → Settings → Integrations → Developer → "API token" → Copy. Save:
  - `TODOIST_API_TOKEN`

- [ ] **P4: Confirm Todoist parent-task convention.** Open your Todoist Inbox or whichever project you keep `#Acute` / `#ABP` / etc. in. Confirm there's an existing task for each Personal OS project named exactly:
  - `#Acute`
  - `#ABP`
  - `#Lake-Arrowhead`
  - `#Church`
  - `#Claude-Build`
  - `#Design`
  - `#Personal`

  If any are missing, create them now. They are the parents under which classified todos will land. Capture each parent's task ID by opening the task and copying the task ID from the URL (the long number at the end). Save them as `TODOIST_PARENT_<PROJECT>` env vars (see `.env.example` in Task 1).

- [ ] **P5: Confirm Obsidian vault location.** On your laptop, your Obsidian vault for Personal OS should be at `<Dropbox>/Apps/roscoe-robot/personal-os/` — i.e., the same Dropbox app-folder the bot already writes media to. If you don't have an Obsidian vault there yet:
  1. Open Obsidian → File → Open vault → Open folder as vault.
  2. Navigate to `<Dropbox>/Apps/roscoe-robot/` and create a new folder `personal-os`.
  3. Open that folder as the vault.
  4. Inside, create empty subfolders: `acute/`, `abp/`, `lake-arrowhead/`, `church/`, `claude-build/`, `design/`, `personal/`, `_meta/`.

- [ ] **P6: Confirm Dropbox app permissions still cover this session.** No change from Session 2. The existing scopes (`files.content.write`, `files.content.read`, `files.metadata.write`, `files.metadata.read`) are sufficient for: writing `.md` notes, moving media, reading `_meta/rules.md` from disk on the droplet (we don't actually round-trip it through Dropbox in Session 3).

- [ ] **P7: Confirm v2 bot is still running and healthy.** From PowerShell:

  ```powershell
  ssh root@64.23.170.115 'systemctl is-active personal-os-v2 && curl -sk https://localhost:8443/healthz'
  ```

  Expected: `active` then `{"ok":true}`.

After P1-P7, you should have these new values written down:

```
ANTHROPIC_API_KEY=sk-ant-...
OPENAI_API_KEY=sk-...
TODOIST_API_TOKEN=...
TODOIST_PARENT_ACUTE=...                  (long integer task ID)
TODOIST_PARENT_ABP=...
TODOIST_PARENT_LAKE_ARROWHEAD=...
TODOIST_PARENT_CHURCH=...
TODOIST_PARENT_CLAUDE_BUILD=...
TODOIST_PARENT_DESIGN=...
TODOIST_PARENT_PERSONAL=...
```

These get appended to the existing 8 vars in `.env` and `.env.example`.

---

## File structure

What this plan creates and modifies. Map of what lives where:

```
roscoe-robot/
├── .env.example                   ← MODIFIED: append new vars
├── requirements.txt               ← MODIFIED: add anthropic, openai, youtube-transcript-api
├── _meta/
│   ├── rules.md                   ← NEW: seed learned-rules file (empty + format example)
│   └── tag_vocab.md               ← NEW: seed tag vocabulary
├── bot/
│   ├── config.py                  ← MODIFIED: add new env-var fields
│   ├── llm.py                     ← NEW: Anthropic API wrapper + classifier prompt assembly
│   ├── enrichment.py              ← NEW: OG fetch, YouTube transcript, Whisper
│   ├── filers.py                  ← NEW: Obsidian writer, Todoist creator, Dropbox mover
│   ├── processor.py               ← NEW: per-item orchestrator + run_batch()
│   ├── db.py                      ← MODIFIED: add fetch_pending_items(), update_classified(), insert_run()
│   └── main.py                    ← MODIFIED: add /process command handler in webhook
├── tests/
│   ├── fixtures/
│   │   ├── og_html_basic.html
│   │   ├── og_html_no_meta.html
│   │   ├── youtube_transcript.json
│   │   ├── whisper_response.json
│   │   ├── classifier_response_text.json
│   │   ├── classifier_response_design.json
│   │   ├── classifier_response_todo.json
│   │   └── update_process_command.json
│   ├── test_llm.py                ← NEW
│   ├── test_enrichment.py         ← NEW
│   ├── test_filers.py             ← NEW
│   ├── test_processor.py          ← NEW
│   └── test_webhook.py            ← MODIFIED: add /process handler tests
```

The hard rule from Session 2 still applies: **only `main.py` knows FastAPI exists.** `processor.py`, `llm.py`, `enrichment.py`, `filers.py`, and `db.py` are all pure-Python and unit-testable without spinning up an HTTP server. This is how Session 5 will be able to swap the FastAPI trigger for a cron trigger without touching the processing logic.

---

## Task 1: Add new deps + env vars + seed `_meta/` files

**Files:**
- Modify: `requirements.txt`, `.env.example`, `bot/config.py`, `tests/conftest.py`
- Create: `_meta/rules.md`, `_meta/tag_vocab.md`

- [ ] **Step 1: Append new runtime deps to `requirements.txt`**

Add to the existing file:

```
anthropic==0.39.0
openai==1.54.0
youtube-transcript-api==0.6.2
```

(httpx and dropbox already pinned from Session 2.)

- [ ] **Step 2: Append new env-var stubs to `.env.example`**

Append below the existing block:

```
# Anthropic — classifier model (Haiku 4.5)
ANTHROPIC_API_KEY=

# OpenAI — Whisper voice transcription only
OPENAI_API_KEY=

# Todoist — single-user personal API token
TODOIST_API_TOKEN=
TODOIST_PARENT_ACUTE=
TODOIST_PARENT_ABP=
TODOIST_PARENT_LAKE_ARROWHEAD=
TODOIST_PARENT_CHURCH=
TODOIST_PARENT_CLAUDE_BUILD=
TODOIST_PARENT_DESIGN=
TODOIST_PARENT_PERSONAL=

# Obsidian vault — relative to Dropbox app folder root
OBSIDIAN_VAULT_DROPBOX_PATH=/personal-os
```

- [ ] **Step 3: Extend `Config` dataclass with new fields**

In `bot/config.py`, the existing `Config.from_env()` validates required vars by reading them from env and raising `ValueError` on missing/malformed. Add these new fields with the same pattern. Show full file after edit:

```python
import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    # Existing — Session 2
    bot_token: str
    my_telegram_id: int
    webhook_secret: str
    supabase_url: str
    supabase_service_key: str
    dropbox_app_key: str
    dropbox_app_secret: str
    dropbox_refresh_token: str

    # New — Session 3
    anthropic_api_key: str
    openai_api_key: str
    todoist_api_token: str
    todoist_parents: dict[str, str]   # project name → parent task ID
    obsidian_vault_dropbox_path: str

    @classmethod
    def from_env(cls) -> "Config":
        required = [
            "BOT_TOKEN", "MY_TELEGRAM_ID", "WEBHOOK_SECRET",
            "SUPABASE_URL", "SUPABASE_SERVICE_KEY",
            "DROPBOX_APP_KEY", "DROPBOX_APP_SECRET", "DROPBOX_REFRESH_TOKEN",
            "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "TODOIST_API_TOKEN",
            "TODOIST_PARENT_ACUTE", "TODOIST_PARENT_ABP",
            "TODOIST_PARENT_LAKE_ARROWHEAD", "TODOIST_PARENT_CHURCH",
            "TODOIST_PARENT_CLAUDE_BUILD", "TODOIST_PARENT_DESIGN",
            "TODOIST_PARENT_PERSONAL",
            "OBSIDIAN_VAULT_DROPBOX_PATH",
        ]
        missing = [k for k in required if not os.getenv(k)]
        if missing:
            raise ValueError(f"missing required env vars: {missing}")

        try:
            my_id = int(os.environ["MY_TELEGRAM_ID"])
        except ValueError as e:
            raise ValueError("MY_TELEGRAM_ID must be an integer") from e

        return cls(
            bot_token=os.environ["BOT_TOKEN"],
            my_telegram_id=my_id,
            webhook_secret=os.environ["WEBHOOK_SECRET"],
            supabase_url=os.environ["SUPABASE_URL"],
            supabase_service_key=os.environ["SUPABASE_SERVICE_KEY"],
            dropbox_app_key=os.environ["DROPBOX_APP_KEY"],
            dropbox_app_secret=os.environ["DROPBOX_APP_SECRET"],
            dropbox_refresh_token=os.environ["DROPBOX_REFRESH_TOKEN"],
            anthropic_api_key=os.environ["ANTHROPIC_API_KEY"],
            openai_api_key=os.environ["OPENAI_API_KEY"],
            todoist_api_token=os.environ["TODOIST_API_TOKEN"],
            todoist_parents={
                "acute": os.environ["TODOIST_PARENT_ACUTE"],
                "abp": os.environ["TODOIST_PARENT_ABP"],
                "lake-arrowhead": os.environ["TODOIST_PARENT_LAKE_ARROWHEAD"],
                "church": os.environ["TODOIST_PARENT_CHURCH"],
                "claude-build": os.environ["TODOIST_PARENT_CLAUDE_BUILD"],
                "design": os.environ["TODOIST_PARENT_DESIGN"],
                "personal": os.environ["TODOIST_PARENT_PERSONAL"],
            },
            obsidian_vault_dropbox_path=os.environ["OBSIDIAN_VAULT_DROPBOX_PATH"],
        )
```

- [ ] **Step 4: Update `tests/conftest.py` `env` fixture**

In the `env` fixture, add these lines after the existing setenv calls:

```python
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test_anthropic_key")
    monkeypatch.setenv("OPENAI_API_KEY", "test_openai_key")
    monkeypatch.setenv("TODOIST_API_TOKEN", "test_todoist_token")
    monkeypatch.setenv("TODOIST_PARENT_ACUTE", "1000001")
    monkeypatch.setenv("TODOIST_PARENT_ABP", "1000002")
    monkeypatch.setenv("TODOIST_PARENT_LAKE_ARROWHEAD", "1000003")
    monkeypatch.setenv("TODOIST_PARENT_CHURCH", "1000004")
    monkeypatch.setenv("TODOIST_PARENT_CLAUDE_BUILD", "1000005")
    monkeypatch.setenv("TODOIST_PARENT_DESIGN", "1000006")
    monkeypatch.setenv("TODOIST_PARENT_PERSONAL", "1000007")
    monkeypatch.setenv("OBSIDIAN_VAULT_DROPBOX_PATH", "/personal-os")
```

- [ ] **Step 5: Run existing config tests; expect them to still pass**

Run: `pytest tests/test_config.py -v`
Expected: all existing tests still pass (the `env` fixture provides the new vars). If any fail because they construct `Config` manually with positional args, update them to use keyword args.

- [ ] **Step 6: Add a config test for the new fields**

In `tests/test_config.py`, append:

```python
def test_config_loads_new_session_3_fields(env):
    from bot.config import Config

    cfg = Config.from_env()

    assert cfg.anthropic_api_key == "test_anthropic_key"
    assert cfg.openai_api_key == "test_openai_key"
    assert cfg.todoist_api_token == "test_todoist_token"
    assert cfg.todoist_parents["acute"] == "1000001"
    assert cfg.todoist_parents["personal"] == "1000007"
    assert cfg.obsidian_vault_dropbox_path == "/personal-os"


def test_config_raises_on_missing_anthropic_key(env, monkeypatch):
    from bot.config import Config

    monkeypatch.delenv("ANTHROPIC_API_KEY")

    import pytest
    with pytest.raises(ValueError, match="ANTHROPIC_API_KEY"):
        Config.from_env()
```

Run: `pytest tests/test_config.py -v`
Expected: PASS for both new tests.

- [ ] **Step 7: Create `_meta/rules.md` (seed)**

```markdown
# Personal OS — Learned Rules

Hand-editable. The classifier reads this file at every run.
The monthly rule-consolidation pass (Session 6) appends here based on the
prior month's `corrections` table.

## Format

Each rule is a bullet. Be specific enough that a fresh classifier can apply it.

## Current rules

(none yet — this file will grow as the system learns)

## Examples (delete once real rules accumulate)

- Photos with kitchen tile, bathroom tile, or stone vanity → `project: lake-arrowhead`.
- X / Twitter links containing "claude code" or "anthropic" → `project: claude-build`, `tag: ai`.
- Voice memos starting with "remind me" or "todo" → `type: todo`.
- Forwards from `@acutelogistics_team` → `project: acute`.
```

- [ ] **Step 8: Create `_meta/tag_vocab.md` (seed)**

```markdown
# Personal OS — Tag Vocabulary

Hand-editable. The classifier sees this list and prefers existing tags before
inventing new ones. New tags it does invent get appended here during the
monthly rule pass (Session 6).

## Active tags

### claude-build
- ai
- mcp
- agents
- prompt-engineering
- skills
- automation
- developer-tools

### acute
- freight
- ltl
- truckload
- prospecting
- cold-outreach
- pricing
- carriers
- customers

### abp
- c3bank
- cre
- bridge-loan
- construction-loan
- compliance
- governance

### design
- hero
- nav
- pricing-page
- dashboard
- typography
- color-palette
- branding
- mobile
- dark-mode
- minimalist
- brutalist

### lake-arrowhead
- kitchen
- bathroom
- exterior
- landscaping
- furniture

### church
- come-follow-me
- talk
- gospel-study
- family-history
- service

### personal
- surfing
- woodworking
- leatherworking
- family
- reading
- video-to-watch
```

- [ ] **Step 9: Reinstall deps in venv**

```powershell
.venv\Scripts\activate
pip install -r requirements.txt
```

Expected: `anthropic`, `openai`, `youtube-transcript-api` installed alongside existing packages.

- [ ] **Step 10: Commit**

```bash
git add requirements.txt .env.example bot/config.py tests/conftest.py tests/test_config.py _meta/
git commit -m "feat(session-3): add deps, env vars, and seed _meta/ files"
```

---

## Task 2: Anthropic API wrapper + classifier prompt assembly (TDD)

**Files:**
- Create: `bot/llm.py`, `tests/test_llm.py`
- Create: `tests/fixtures/classifier_response_text.json`, `tests/fixtures/classifier_response_design.json`

`bot/llm.py` is the only module that imports `anthropic`. It exposes:

- `build_classifier_system_prompt(rules_md: str, tag_vocab_md: str, recent_corrections: list[dict]) -> list[dict]` — returns a 2-element list-of-content-blocks suitable for Anthropic's `system=` parameter, with a `cache_control` marker on the static block.
- `classify_item(client, system_blocks, item_payload) -> dict` — sends the message, returns `{project, subdomain, type, tags, visual_subtype, summary, confidence, _cost_cents}`.
- `cost_cents_from_usage(usage_dict) -> int` — converts an Anthropic `usage` block (with `input_tokens`, `output_tokens`, `cache_creation_input_tokens`, `cache_read_input_tokens`) into cents.

The classifier system prompt is structured as **two blocks** so the static block can be cached:

1. **Static block (cached):** projects, types, visual subtypes, output schema, full `_meta/rules.md`, full `_meta/tag_vocab.md`. Marked with `cache_control={"type": "ephemeral"}`.
2. **Dynamic block (not cached):** last 30 corrections from Supabase, formatted as few-shot.

The user message is the per-item payload.

- [ ] **Step 1: Write the failing test for `cost_cents_from_usage`**

`tests/test_llm.py`:

```python
import pytest

from bot.llm import cost_cents_from_usage


# Haiku 4.5 pricing (per 1M tokens): input $1, output $5,
# cache write $1.25, cache read $0.10. Source: anthropic.com/pricing.
# We compute cost_cents = ceil(total_usd * 100).


def test_cost_cents_only_input_and_output():
    usage = {
        "input_tokens": 1_000,
        "output_tokens": 200,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 0,
    }
    # 1000 * $1 / 1M + 200 * $5 / 1M = $0.001 + $0.001 = $0.002 = 0.2 cents → 1 cent
    assert cost_cents_from_usage(usage) == 1


def test_cost_cents_with_cache_read():
    # Realistic case: classifier system prompt is ~5000 tokens, mostly cached.
    usage = {
        "input_tokens": 500,                         # only the dynamic block + user msg
        "output_tokens": 150,                        # JSON response
        "cache_creation_input_tokens": 0,            # cache hit, no write
        "cache_read_input_tokens": 5_000,            # static system block read from cache
    }
    # input  500   * $1.00 / 1M = $0.0005
    # output 150   * $5.00 / 1M = $0.00075
    # cache_r 5000 * $0.10 / 1M = $0.0005
    # total = $0.00175 → 0.175 cents → 1 cent (ceil)
    assert cost_cents_from_usage(usage) == 1


def test_cost_cents_with_cache_creation():
    # First call of the day: cache write happens.
    usage = {
        "input_tokens": 500,
        "output_tokens": 150,
        "cache_creation_input_tokens": 5_000,
        "cache_read_input_tokens": 0,
    }
    # input  500   * $1.00 / 1M = $0.0005
    # output 150   * $5.00 / 1M = $0.00075
    # cache_w 5000 * $1.25 / 1M = $0.00625
    # total = $0.0075 → 0.75 cents → 1 cent (ceil)
    assert cost_cents_from_usage(usage) == 1


def test_cost_cents_handles_missing_keys():
    usage = {"input_tokens": 1_000_000, "output_tokens": 0}
    # 1M * $1 / 1M = $1.00 = 100 cents
    assert cost_cents_from_usage(usage) == 100
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_llm.py -v`
Expected: ImportError or "cost_cents_from_usage not found".

- [ ] **Step 3: Implement `cost_cents_from_usage`**

`bot/llm.py`:

```python
import math


# Haiku 4.5 prices in USD per 1M tokens.
HAIKU_INPUT_PRICE = 1.00
HAIKU_OUTPUT_PRICE = 5.00
HAIKU_CACHE_WRITE_PRICE = 1.25
HAIKU_CACHE_READ_PRICE = 0.10


def cost_cents_from_usage(usage: dict) -> int:
    """Convert an Anthropic usage block to integer cents (ceiling)."""
    input_t = usage.get("input_tokens", 0)
    output_t = usage.get("output_tokens", 0)
    cache_w = usage.get("cache_creation_input_tokens", 0)
    cache_r = usage.get("cache_read_input_tokens", 0)

    usd = (
        input_t  * HAIKU_INPUT_PRICE       / 1_000_000
        + output_t * HAIKU_OUTPUT_PRICE    / 1_000_000
        + cache_w  * HAIKU_CACHE_WRITE_PRICE / 1_000_000
        + cache_r  * HAIKU_CACHE_READ_PRICE   / 1_000_000
    )
    return math.ceil(usd * 100)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_llm.py -v`
Expected: 4 PASS.

- [ ] **Step 5: Write the failing test for `build_classifier_system_prompt`**

Append to `tests/test_llm.py`:

```python
from bot.llm import build_classifier_system_prompt


def test_system_prompt_has_two_blocks():
    blocks = build_classifier_system_prompt(
        rules_md="rule one\nrule two",
        tag_vocab_md="### claude-build\n- ai\n- mcp",
        recent_corrections=[],
    )
    assert len(blocks) == 2
    assert blocks[0]["type"] == "text"
    assert blocks[1]["type"] == "text"


def test_system_prompt_static_block_is_cached():
    blocks = build_classifier_system_prompt(
        rules_md="r",
        tag_vocab_md="t",
        recent_corrections=[],
    )
    assert blocks[0].get("cache_control") == {"type": "ephemeral"}


def test_system_prompt_dynamic_block_is_not_cached():
    blocks = build_classifier_system_prompt(
        rules_md="r",
        tag_vocab_md="t",
        recent_corrections=[],
    )
    assert "cache_control" not in blocks[1]


def test_system_prompt_static_block_includes_seven_projects():
    blocks = build_classifier_system_prompt(
        rules_md="",
        tag_vocab_md="",
        recent_corrections=[],
    )
    text = blocks[0]["text"]
    for p in ["acute", "abp", "lake-arrowhead", "church", "claude-build", "design", "personal"]:
        assert p in text


def test_system_prompt_static_block_includes_rules_and_vocab():
    blocks = build_classifier_system_prompt(
        rules_md="my-rule-marker-xyz",
        tag_vocab_md="my-tag-marker-abc",
        recent_corrections=[],
    )
    text = blocks[0]["text"]
    assert "my-rule-marker-xyz" in text
    assert "my-tag-marker-abc" in text


def test_system_prompt_dynamic_block_includes_corrections():
    corrections = [
        {
            "original_class": {"project": "personal", "type": "idea"},
            "corrected_class": {"project": "claude-build", "type": "todo"},
        },
    ]
    blocks = build_classifier_system_prompt(
        rules_md="",
        tag_vocab_md="",
        recent_corrections=corrections,
    )
    dyn = blocks[1]["text"]
    assert "personal" in dyn and "claude-build" in dyn
    assert "idea" in dyn and "todo" in dyn


def test_system_prompt_dynamic_block_handles_empty_corrections():
    blocks = build_classifier_system_prompt(
        rules_md="",
        tag_vocab_md="",
        recent_corrections=[],
    )
    # Empty correction list still yields a valid text block (sentinel string OK).
    assert isinstance(blocks[1]["text"], str)
    assert len(blocks[1]["text"]) > 0
```

- [ ] **Step 6: Run tests to verify they fail**

Run: `pytest tests/test_llm.py -v`
Expected: 7 new tests fail with "build_classifier_system_prompt not found".

- [ ] **Step 7: Implement `build_classifier_system_prompt`**

Append to `bot/llm.py`:

```python
import json


STATIC_INSTRUCTIONS_HEADER = """\
You are the Personal OS classifier for Ryan Bunker. Given one captured item,
output a single JSON object describing how to file it. Output ONLY the JSON,
no prose, no markdown fences.

# Projects (pick exactly one)

- acute: Acute Logistics — sales, operations, freight content, customer research.
- abp: ABP Capital and C3bank. Use the `subdomain` field to specify "c3bank" or "abp".
- lake-arrowhead: Lake Arrowhead cabin and personal real estate.
- church: Come Follow Me lessons, talks, gospel study, family history.
- claude-build: Tools and automations being built (incl. this Personal OS).
- design: Visual inspiration library — heroes, nav, typography, brand, etc.
- personal: Catch-all — surfing, woodworking, family, miscellaneous.

# Type (pick exactly one)

- article, video, image, todo, idea, voice, link

# Visual subtype (only set when type=image; otherwise null)

- hero, nav, pricing-page, dashboard, typography, color-palette, branding,
  mobile, dark-mode, minimalist, brutalist, or a new descriptor if needed.

# Output schema

```json
{
  "project": "acute" | "abp" | "lake-arrowhead" | "church" | "claude-build" | "design" | "personal",
  "subdomain": null | "c3bank" | "abp",
  "type": "article" | "video" | "image" | "todo" | "idea" | "voice" | "link",
  "tags": ["..."],
  "visual_subtype": null | "hero" | ...,
  "summary": "1-2 sentence factual summary",
  "confidence": 0.0-1.0
}
```

`confidence` is your honest self-rating: 0.9+ when project + type are obvious;
0.6-0.8 when reasonable but ambiguous; below 0.6 when you're guessing.
The system surfaces low-confidence items for review.
"""


def build_classifier_system_prompt(
    rules_md: str,
    tag_vocab_md: str,
    recent_corrections: list[dict],
) -> list[dict]:
    """Assemble the classifier system prompt as two cache-aware blocks."""
    static_text = (
        STATIC_INSTRUCTIONS_HEADER
        + "\n\n# Tag vocabulary (prefer these; invent only when nothing fits)\n\n"
        + (tag_vocab_md or "(none)")
        + "\n\n# Learned rules (apply these unless they contradict the input)\n\n"
        + (rules_md or "(none)")
    )

    if recent_corrections:
        examples = []
        for i, c in enumerate(recent_corrections, start=1):
            examples.append(
                f"Example {i}:\n"
                f"  Originally classified as: {json.dumps(c['original_class'])}\n"
                f"  User corrected to:        {json.dumps(c['corrected_class'])}"
            )
        dynamic_text = (
            "# Recent corrections (newest first) — learn from these\n\n"
            + "\n\n".join(examples)
        )
    else:
        dynamic_text = "# Recent corrections\n\n(none yet)"

    return [
        {
            "type": "text",
            "text": static_text,
            "cache_control": {"type": "ephemeral"},
        },
        {
            "type": "text",
            "text": dynamic_text,
        },
    ]
```

- [ ] **Step 8: Run tests to verify they pass**

Run: `pytest tests/test_llm.py -v`
Expected: 11 PASS total.

- [ ] **Step 9: Save fixture files for `classify_item` tests**

`tests/fixtures/classifier_response_text.json` (mimics the SDK's `Message` object's relevant fields):

```json
{
  "id": "msg_test_text_01",
  "type": "message",
  "role": "assistant",
  "model": "claude-haiku-4-5-20251001",
  "stop_reason": "end_turn",
  "content": [
    {
      "type": "text",
      "text": "{\"project\":\"claude-build\",\"subdomain\":null,\"type\":\"idea\",\"tags\":[\"ai\",\"mcp\"],\"visual_subtype\":null,\"summary\":\"Idea: build an MCP server for Todoist that supports parent-task auto-grouping.\",\"confidence\":0.88}"
    }
  ],
  "usage": {
    "input_tokens": 480,
    "output_tokens": 120,
    "cache_creation_input_tokens": 0,
    "cache_read_input_tokens": 4800
  }
}
```

`tests/fixtures/classifier_response_design.json`:

```json
{
  "id": "msg_test_design_01",
  "type": "message",
  "role": "assistant",
  "model": "claude-haiku-4-5-20251001",
  "stop_reason": "end_turn",
  "content": [
    {
      "type": "text",
      "text": "{\"project\":\"design\",\"subdomain\":null,\"type\":\"image\",\"tags\":[\"hero\",\"dark-mode\",\"gradient\"],\"visual_subtype\":\"hero\",\"summary\":\"Hero section with dark gradient background and oversized serif headline.\",\"confidence\":0.93}"
    }
  ],
  "usage": {
    "input_tokens": 520,
    "output_tokens": 95,
    "cache_creation_input_tokens": 0,
    "cache_read_input_tokens": 4800
  }
}
```

- [ ] **Step 10: Write the failing test for `classify_item`**

Append to `tests/test_llm.py`:

```python
import json
from pathlib import Path
from unittest.mock import MagicMock

from bot.llm import classify_item


def _load_fixture(name: str) -> dict:
    return json.loads(
        (Path(__file__).parent / "fixtures" / name).read_text(encoding="utf-8")
    )


def test_classify_item_returns_parsed_json_plus_cost(mocker):
    fixture = _load_fixture("classifier_response_text.json")

    fake_message = MagicMock()
    fake_message.content = [MagicMock(type="text", text=fixture["content"][0]["text"])]
    fake_message.usage = MagicMock(
        input_tokens=fixture["usage"]["input_tokens"],
        output_tokens=fixture["usage"]["output_tokens"],
        cache_creation_input_tokens=fixture["usage"]["cache_creation_input_tokens"],
        cache_read_input_tokens=fixture["usage"]["cache_read_input_tokens"],
    )

    fake_client = MagicMock()
    fake_client.messages.create.return_value = fake_message

    system_blocks = [
        {"type": "text", "text": "static", "cache_control": {"type": "ephemeral"}},
        {"type": "text", "text": "dynamic"},
    ]
    payload = "Idea: build an MCP server for Todoist."

    result = classify_item(fake_client, system_blocks, payload)

    assert result["project"] == "claude-build"
    assert result["type"] == "idea"
    assert "ai" in result["tags"]
    assert result["confidence"] == 0.88
    assert isinstance(result["_cost_cents"], int)
    assert result["_cost_cents"] >= 1


def test_classify_item_passes_system_blocks_through(mocker):
    fake_message = MagicMock()
    fake_message.content = [MagicMock(type="text", text='{"project":"personal","subdomain":null,"type":"idea","tags":[],"visual_subtype":null,"summary":"x","confidence":0.5}')]
    fake_message.usage = MagicMock(
        input_tokens=10, output_tokens=10,
        cache_creation_input_tokens=0, cache_read_input_tokens=0,
    )
    fake_client = MagicMock()
    fake_client.messages.create.return_value = fake_message

    system_blocks = [
        {"type": "text", "text": "STATIC_MARKER", "cache_control": {"type": "ephemeral"}},
        {"type": "text", "text": "DYN_MARKER"},
    ]

    classify_item(fake_client, system_blocks, "x")

    call_kwargs = fake_client.messages.create.call_args.kwargs
    assert call_kwargs["system"] == system_blocks
    assert call_kwargs["model"] == "claude-haiku-4-5-20251001"
    assert call_kwargs["messages"][0]["content"] == "x"


def test_classify_item_raises_on_non_json_response(mocker):
    fake_message = MagicMock()
    fake_message.content = [MagicMock(type="text", text="I cannot classify this item.")]
    fake_message.usage = MagicMock(
        input_tokens=10, output_tokens=10,
        cache_creation_input_tokens=0, cache_read_input_tokens=0,
    )
    fake_client = MagicMock()
    fake_client.messages.create.return_value = fake_message

    with pytest.raises(ValueError, match="not valid JSON"):
        classify_item(fake_client, [{"type": "text", "text": "s"}], "x")
```

- [ ] **Step 11: Run tests to verify they fail**

Run: `pytest tests/test_llm.py -v -k classify_item`
Expected: 3 FAIL with "classify_item not found".

- [ ] **Step 12: Implement `classify_item`**

Append to `bot/llm.py`:

```python
def classify_item(
    client,
    system_blocks: list[dict],
    item_payload: str,
    *,
    model: str = "claude-haiku-4-5-20251001",
    max_tokens: int = 600,
) -> dict:
    """Send one classification request and return parsed JSON + cost."""
    response = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=system_blocks,
        messages=[{"role": "user", "content": item_payload}],
    )

    text_blocks = [b.text for b in response.content if b.type == "text"]
    raw = "".join(text_blocks).strip()

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(f"classifier response is not valid JSON: {raw[:200]}") from e

    parsed["_cost_cents"] = cost_cents_from_usage({
        "input_tokens": response.usage.input_tokens,
        "output_tokens": response.usage.output_tokens,
        "cache_creation_input_tokens": getattr(response.usage, "cache_creation_input_tokens", 0) or 0,
        "cache_read_input_tokens": getattr(response.usage, "cache_read_input_tokens", 0) or 0,
    })
    return parsed
```

- [ ] **Step 13: Run all `test_llm.py` tests**

Run: `pytest tests/test_llm.py -v`
Expected: 14 PASS total.

- [ ] **Step 14: Commit**

```bash
git add bot/llm.py tests/test_llm.py tests/fixtures/classifier_response_*.json
git commit -m "feat(session-3): Anthropic API wrapper with prompt caching + classifier prompt assembly"
```

---

## Task 3: Enrichment helpers — OG fetch, YouTube transcript, Whisper (TDD)

**Files:**
- Create: `bot/enrichment.py`, `tests/test_enrichment.py`
- Create: `tests/fixtures/og_html_basic.html`, `tests/fixtures/og_html_no_meta.html`, `tests/fixtures/youtube_transcript.json`, `tests/fixtures/whisper_response.json`

`bot/enrichment.py` exposes three pure functions:

- `fetch_og_metadata(url: str) -> dict` — returns `{title, description, image, site_name}`. Uses `httpx`. Falls back gracefully (returns whatever it finds; missing keys → empty string).
- `fetch_youtube_transcript(url: str) -> str` — extracts video ID from URL, calls `youtube-transcript-api`, returns joined transcript text. Raises `ValueError` if no transcript available.
- `transcribe_voice(openai_api_key: str, audio_bytes: bytes, *, file_extension: str = ".ogg") -> str` — POSTs to OpenAI Whisper. Returns transcript.

- [ ] **Step 1: Save OG HTML fixture**

`tests/fixtures/og_html_basic.html`:

```html
<!DOCTYPE html>
<html>
<head>
  <title>Page Title (HTML)</title>
  <meta property="og:title" content="OG Title — overrides HTML title" />
  <meta property="og:description" content="A short OG description." />
  <meta property="og:image" content="https://example.com/cover.jpg" />
  <meta property="og:site_name" content="Example Blog" />
</head>
<body>...</body>
</html>
```

`tests/fixtures/og_html_no_meta.html`:

```html
<!DOCTYPE html>
<html>
<head>
  <title>Just an HTML title</title>
</head>
<body>nothing else</body>
</html>
```

- [ ] **Step 2: Write failing tests for `fetch_og_metadata`**

`tests/test_enrichment.py`:

```python
from pathlib import Path

import pytest
import respx
from httpx import Response

from bot.enrichment import fetch_og_metadata


FIXTURES = Path(__file__).parent / "fixtures"


@respx.mock
async def test_og_metadata_extracts_all_fields_when_present():
    html = (FIXTURES / "og_html_basic.html").read_text(encoding="utf-8")
    respx.get("https://example.com/post").mock(return_value=Response(200, text=html))

    result = await fetch_og_metadata("https://example.com/post")

    assert result["title"] == "OG Title — overrides HTML title"
    assert result["description"] == "A short OG description."
    assert result["image"] == "https://example.com/cover.jpg"
    assert result["site_name"] == "Example Blog"


@respx.mock
async def test_og_metadata_falls_back_to_html_title_when_no_og():
    html = (FIXTURES / "og_html_no_meta.html").read_text(encoding="utf-8")
    respx.get("https://example.com/bare").mock(return_value=Response(200, text=html))

    result = await fetch_og_metadata("https://example.com/bare")

    assert result["title"] == "Just an HTML title"
    assert result["description"] == ""
    assert result["image"] == ""


@respx.mock
async def test_og_metadata_returns_empty_dict_on_http_error():
    respx.get("https://example.com/down").mock(return_value=Response(500))

    result = await fetch_og_metadata("https://example.com/down")

    assert result == {"title": "", "description": "", "image": "", "site_name": ""}


@respx.mock
async def test_og_metadata_handles_network_failure():
    respx.get("https://example.com/timeout").mock(side_effect=Exception("connection refused"))

    result = await fetch_og_metadata("https://example.com/timeout")

    assert result == {"title": "", "description": "", "image": "", "site_name": ""}
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `pytest tests/test_enrichment.py -v`
Expected: 4 FAIL with ImportError.

- [ ] **Step 4: Implement `fetch_og_metadata`**

`bot/enrichment.py`:

```python
import re

import httpx


_OG_RE = {
    "title": re.compile(r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)["\']', re.I),
    "description": re.compile(r'<meta[^>]+property=["\']og:description["\'][^>]+content=["\']([^"\']+)["\']', re.I),
    "image": re.compile(r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']', re.I),
    "site_name": re.compile(r'<meta[^>]+property=["\']og:site_name["\'][^>]+content=["\']([^"\']+)["\']', re.I),
}
_TITLE_RE = re.compile(r"<title[^>]*>([^<]+)</title>", re.I)


async def fetch_og_metadata(url: str) -> dict:
    """Fetch OG metadata for a URL. Returns empty strings for any field missing or on error."""
    out = {"title": "", "description": "", "image": "", "site_name": ""}
    try:
        async with httpx.AsyncClient(timeout=8.0, follow_redirects=True) as client:
            response = await client.get(url, headers={"User-Agent": "roscoe-robot/0.3"})
            if response.status_code >= 400:
                return out
            html = response.text
    except Exception:
        return out

    for key, pattern in _OG_RE.items():
        m = pattern.search(html)
        if m:
            out[key] = m.group(1)

    if not out["title"]:
        m = _TITLE_RE.search(html)
        if m:
            out["title"] = m.group(1).strip()

    return out
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_enrichment.py -v`
Expected: 4 PASS.

- [ ] **Step 6: Save YouTube transcript fixture**

`tests/fixtures/youtube_transcript.json`:

```json
[
  {"text": "Hi everyone, today we're going to look at MCP servers.", "start": 0.0, "duration": 4.5},
  {"text": "MCP stands for Model Context Protocol.", "start": 4.5, "duration": 3.2},
  {"text": "It's the new standard from Anthropic for connecting tools to LLMs.", "start": 7.7, "duration": 5.0}
]
```

- [ ] **Step 7: Write failing tests for `fetch_youtube_transcript`**

Append to `tests/test_enrichment.py`:

```python
import json

from bot.enrichment import fetch_youtube_transcript


def test_youtube_transcript_extracts_video_id_from_watch_url(mocker):
    fixture = json.loads((FIXTURES / "youtube_transcript.json").read_text())
    mock_get = mocker.patch(
        "bot.enrichment.YouTubeTranscriptApi.get_transcript",
        return_value=fixture,
    )

    result = fetch_youtube_transcript("https://www.youtube.com/watch?v=abcDEF12345")

    mock_get.assert_called_once_with("abcDEF12345", languages=("en",))
    assert "MCP servers" in result
    assert "Anthropic" in result


def test_youtube_transcript_extracts_video_id_from_short_url(mocker):
    fixture = json.loads((FIXTURES / "youtube_transcript.json").read_text())
    mock_get = mocker.patch(
        "bot.enrichment.YouTubeTranscriptApi.get_transcript",
        return_value=fixture,
    )

    fetch_youtube_transcript("https://youtu.be/abcDEF12345?t=42")

    mock_get.assert_called_once_with("abcDEF12345", languages=("en",))


def test_youtube_transcript_raises_on_unparseable_url():
    with pytest.raises(ValueError, match="could not extract YouTube video ID"):
        fetch_youtube_transcript("https://example.com/not-youtube")


def test_youtube_transcript_raises_on_api_failure(mocker):
    mocker.patch(
        "bot.enrichment.YouTubeTranscriptApi.get_transcript",
        side_effect=Exception("no captions"),
    )

    with pytest.raises(ValueError, match="no transcript available"):
        fetch_youtube_transcript("https://www.youtube.com/watch?v=abcDEF12345")
```

- [ ] **Step 8: Run tests to verify they fail**

Run: `pytest tests/test_enrichment.py -v -k youtube`
Expected: 4 FAIL.

- [ ] **Step 9: Implement `fetch_youtube_transcript`**

Append to `bot/enrichment.py`:

```python
from youtube_transcript_api import YouTubeTranscriptApi


_YT_ID_RE = re.compile(
    r"(?:youtube\.com/watch\?(?:.*&)?v=|youtu\.be/)([A-Za-z0-9_-]{11})"
)


def fetch_youtube_transcript(url: str) -> str:
    """Extract a YouTube transcript. Raises ValueError if URL is not YouTube
    or if no transcript is available."""
    m = _YT_ID_RE.search(url)
    if not m:
        raise ValueError(f"could not extract YouTube video ID from: {url}")
    video_id = m.group(1)

    try:
        segments = YouTubeTranscriptApi.get_transcript(video_id, languages=("en",))
    except Exception as e:
        raise ValueError(f"no transcript available for {video_id}: {e}") from e

    return " ".join(s["text"] for s in segments)
```

- [ ] **Step 10: Run tests to verify they pass**

Run: `pytest tests/test_enrichment.py -v -k youtube`
Expected: 4 PASS.

- [ ] **Step 11: Save Whisper response fixture**

`tests/fixtures/whisper_response.json`:

```json
{"text": "Remind me to follow up with the Walmart contact tomorrow morning."}
```

- [ ] **Step 12: Write failing tests for `transcribe_voice`**

Append to `tests/test_enrichment.py`:

```python
from bot.enrichment import transcribe_voice


def test_transcribe_voice_calls_openai_with_audio_bytes(mocker):
    fixture_text = "Remind me to follow up with the Walmart contact tomorrow morning."

    fake_response = mocker.MagicMock()
    fake_response.text = fixture_text

    fake_client = mocker.MagicMock()
    fake_client.audio.transcriptions.create.return_value = fake_response

    fake_openai_class = mocker.patch("bot.enrichment.OpenAI", return_value=fake_client)

    result = transcribe_voice("test-key", b"fake-audio-bytes", file_extension=".ogg")

    fake_openai_class.assert_called_once_with(api_key="test-key")
    call_kwargs = fake_client.audio.transcriptions.create.call_args.kwargs
    assert call_kwargs["model"] == "whisper-1"
    # The bytes get wrapped in a tuple (filename, bytes) — check the bytes part.
    file_arg = call_kwargs["file"]
    assert file_arg[1] == b"fake-audio-bytes"
    assert file_arg[0].endswith(".ogg")
    assert result == fixture_text
```

- [ ] **Step 13: Run test to verify it fails**

Run: `pytest tests/test_enrichment.py -v -k transcribe`
Expected: 1 FAIL.

- [ ] **Step 14: Implement `transcribe_voice`**

Append to `bot/enrichment.py`:

```python
from openai import OpenAI


def transcribe_voice(
    openai_api_key: str,
    audio_bytes: bytes,
    *,
    file_extension: str = ".ogg",
) -> str:
    """Transcribe a voice clip via OpenAI Whisper. Returns the transcript text."""
    client = OpenAI(api_key=openai_api_key)
    result = client.audio.transcriptions.create(
        model="whisper-1",
        file=(f"audio{file_extension}", audio_bytes),
    )
    return result.text
```

- [ ] **Step 15: Run all enrichment tests**

Run: `pytest tests/test_enrichment.py -v`
Expected: 9 PASS total.

- [ ] **Step 16: Commit**

```bash
git add bot/enrichment.py tests/test_enrichment.py tests/fixtures/og_html_*.html tests/fixtures/youtube_transcript.json tests/fixtures/whisper_response.json
git commit -m "feat(session-3): enrichment helpers (OG fetch, YouTube transcript, Whisper)"
```

---

## Task 4: Filers — Obsidian writer, Todoist creator, Dropbox mover (TDD)

**Files:**
- Create: `bot/filers.py`, `tests/test_filers.py`

`bot/filers.py` exposes three functions. All three accept already-built clients/factories so the caller (processor) can wire them up once.

- `write_obsidian_note(dropbox_client, vault_root, item_id, classification, raw_text, media_dropbox_path) -> str` — writes a `.md` file to `<vault_root>/<project>/<YYYY-MM-DD>-<slug>.md`. Returns the obsidian_path (path within the vault).
- `create_todoist_task(api_token, parent_task_id, content, *, description=None) -> str` — POSTs to Todoist REST API. Returns the new task ID as a string.
- `move_dropbox_media(dropbox_client, *, from_path, to_path) -> str` — calls `files_move_v2`. Returns the destination path.

- [ ] **Step 1: Write failing tests for `write_obsidian_note`**

`tests/test_filers.py`:

```python
from datetime import date
from unittest.mock import MagicMock

import pytest

from bot.filers import write_obsidian_note


def test_obsidian_note_path_format():
    classification = {
        "project": "claude-build",
        "type": "idea",
        "tags": ["ai", "mcp"],
        "visual_subtype": None,
        "summary": "Build a Todoist MCP server with parent-task auto-grouping.",
        "confidence": 0.88,
    }
    fake_dbx = MagicMock()

    obsidian_path = write_obsidian_note(
        dropbox_client=fake_dbx,
        vault_root="/personal-os",
        item_id="abcd-1234",
        classification=classification,
        raw_text="Idea: build a Todoist MCP server.",
        media_dropbox_path=None,
        capture_date=date(2026, 5, 6),
    )

    # Path inside the vault.
    assert obsidian_path.startswith("claude-build/")
    assert obsidian_path.endswith(".md")
    assert "2026-05-06" in obsidian_path
    # Slug is derived from summary (or raw_text fallback).
    assert "todoist" in obsidian_path.lower() or "mcp" in obsidian_path.lower()


def test_obsidian_note_body_contains_frontmatter_and_summary():
    classification = {
        "project": "design",
        "type": "image",
        "tags": ["hero", "dark-mode"],
        "visual_subtype": "hero",
        "summary": "Hero with dark gradient background.",
        "confidence": 0.93,
        "subdomain": None,
    }
    fake_dbx = MagicMock()

    write_obsidian_note(
        dropbox_client=fake_dbx,
        vault_root="/personal-os",
        item_id="img-9999",
        classification=classification,
        raw_text="",
        media_dropbox_path="/inspiration/design/img-9999.jpg",
        capture_date=date(2026, 5, 6),
    )

    upload_call = fake_dbx.files_upload.call_args
    note_bytes = upload_call.kwargs["f"] if "f" in upload_call.kwargs else upload_call.args[0]
    note_text = note_bytes.decode("utf-8")

    # Frontmatter
    assert note_text.startswith("---\n")
    assert "id: img-9999" in note_text
    assert "type: image" in note_text
    assert "project: design" in note_text
    assert "visual_subtype: hero" in note_text
    assert "confidence: 0.93" in note_text
    # Body
    assert "Hero with dark gradient background." in note_text
    # Image embed (Obsidian-flavor)
    assert "![[" in note_text or "![" in note_text


def test_obsidian_note_writes_to_correct_dropbox_path():
    classification = {
        "project": "acute",
        "type": "todo",
        "tags": ["prospecting"],
        "visual_subtype": None,
        "summary": "Follow up with Walmart contact.",
        "confidence": 0.9,
        "subdomain": None,
    }
    fake_dbx = MagicMock()

    obsidian_path = write_obsidian_note(
        dropbox_client=fake_dbx,
        vault_root="/personal-os",
        item_id="todo-1",
        classification=classification,
        raw_text="follow up with walmart",
        media_dropbox_path=None,
        capture_date=date(2026, 5, 6),
    )

    upload_call = fake_dbx.files_upload.call_args
    # path kwarg should be vault_root + obsidian_path
    full_path = upload_call.kwargs.get("path") or upload_call.args[1]
    assert full_path == f"/personal-os/{obsidian_path}"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_filers.py -v`
Expected: 3 FAIL with ImportError.

- [ ] **Step 3: Implement `write_obsidian_note`**

`bot/filers.py`:

```python
import re
from datetime import date as _date
from datetime import datetime, timezone


_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slugify(text: str, max_len: int = 60) -> str:
    s = _SLUG_RE.sub("-", text.lower()).strip("-")
    if not s:
        s = "untitled"
    return s[:max_len].rstrip("-")


def _format_frontmatter(item_id: str, classification: dict, media_dropbox_path: str | None) -> str:
    lines = ["---"]
    lines.append(f"id: {item_id}")
    lines.append(f"created: {datetime.now(timezone.utc).isoformat()}")
    lines.append(f"project: {classification.get('project', '')}")
    if classification.get("subdomain"):
        lines.append(f"subdomain: {classification['subdomain']}")
    lines.append(f"type: {classification.get('type', '')}")
    if classification.get("visual_subtype"):
        lines.append(f"visual_subtype: {classification['visual_subtype']}")
    tags = classification.get("tags") or []
    if tags:
        lines.append("tags:")
        for t in tags:
            lines.append(f"  - {t}")
    if media_dropbox_path:
        lines.append(f"dropbox_path: {media_dropbox_path}")
    lines.append(f"confidence: {classification.get('confidence', 0.0)}")
    lines.append("source: telegram")
    lines.append("---")
    return "\n".join(lines)


def write_obsidian_note(
    dropbox_client,
    vault_root: str,
    item_id: str,
    classification: dict,
    raw_text: str,
    media_dropbox_path: str | None,
    *,
    capture_date: _date | None = None,
) -> str:
    """Write a `.md` note for one item to the Obsidian vault.
    Returns the obsidian_path (relative to vault root)."""
    capture_date = capture_date or datetime.now(timezone.utc).date()
    project = classification.get("project") or "personal"

    summary = (classification.get("summary") or "").strip()
    slug_source = summary or raw_text or item_id
    slug = _slugify(slug_source)

    obsidian_path = f"{project}/{capture_date.isoformat()}-{slug}.md"

    frontmatter = _format_frontmatter(item_id, classification, media_dropbox_path)
    body_parts = [frontmatter, "", summary]
    if raw_text and raw_text != summary:
        body_parts += ["", "## Raw capture", "", raw_text]
    if media_dropbox_path and classification.get("type") == "image":
        body_parts += ["", f"![Captured image]({media_dropbox_path})"]
    note = "\n".join(body_parts).encode("utf-8")

    full_path = f"{vault_root}/{obsidian_path}"
    dropbox_client.files_upload(f=note, path=full_path, mode="overwrite")

    return obsidian_path
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_filers.py -v`
Expected: 3 PASS.

- [ ] **Step 5: Write failing tests for `create_todoist_task`**

Append to `tests/test_filers.py`:

```python
import respx
from httpx import Response

from bot.filers import create_todoist_task


@respx.mock
def test_create_todoist_task_posts_to_correct_endpoint():
    respx.post("https://api.todoist.com/rest/v2/tasks").mock(
        return_value=Response(200, json={"id": "8888888888"})
    )

    task_id = create_todoist_task(
        api_token="test-token",
        parent_task_id="1000001",
        content="Follow up with Walmart contact",
    )

    assert task_id == "8888888888"

    request = respx.calls.last.request
    assert request.headers["Authorization"] == "Bearer test-token"
    body = json.loads(request.content)
    assert body["content"] == "Follow up with Walmart contact"
    assert body["parent_id"] == "1000001"


@respx.mock
def test_create_todoist_task_includes_description_when_provided():
    respx.post("https://api.todoist.com/rest/v2/tasks").mock(
        return_value=Response(200, json={"id": "9999999999"})
    )

    create_todoist_task(
        api_token="t",
        parent_task_id="100",
        content="Call vendor",
        description="See raw capture for context",
    )

    body = json.loads(respx.calls.last.request.content)
    assert body["description"] == "See raw capture for context"


@respx.mock
def test_create_todoist_task_raises_on_http_error():
    respx.post("https://api.todoist.com/rest/v2/tasks").mock(
        return_value=Response(500, text="server error")
    )

    with pytest.raises(RuntimeError, match="Todoist"):
        create_todoist_task(api_token="t", parent_task_id="1", content="x")
```

- [ ] **Step 6: Implement `create_todoist_task`**

Append to `bot/filers.py`:

```python
import httpx


def create_todoist_task(
    api_token: str,
    parent_task_id: str,
    content: str,
    *,
    description: str | None = None,
) -> str:
    """POST a new Todoist task under the given parent. Returns the new task ID."""
    body: dict = {"content": content, "parent_id": parent_task_id}
    if description:
        body["description"] = description

    response = httpx.post(
        "https://api.todoist.com/rest/v2/tasks",
        headers={"Authorization": f"Bearer {api_token}"},
        json=body,
        timeout=10.0,
    )
    if response.status_code >= 400:
        raise RuntimeError(f"Todoist API failed ({response.status_code}): {response.text}")

    return str(response.json()["id"])
```

- [ ] **Step 7: Run tests**

Run: `pytest tests/test_filers.py -v -k todoist`
Expected: 3 PASS.

- [ ] **Step 8: Write failing tests for `move_dropbox_media`**

Append to `tests/test_filers.py`:

```python
from bot.filers import move_dropbox_media


def test_move_dropbox_media_calls_files_move_v2():
    fake_dbx = MagicMock()
    fake_dbx.files_move_v2.return_value = MagicMock(
        metadata=MagicMock(path_display="/inspiration/design/img-9999.jpg")
    )

    result = move_dropbox_media(
        fake_dbx,
        from_path="/personal-os-inbox/2026-05-06/img-9999.jpg",
        to_path="/inspiration/design/img-9999.jpg",
    )

    fake_dbx.files_move_v2.assert_called_once_with(
        from_path="/personal-os-inbox/2026-05-06/img-9999.jpg",
        to_path="/inspiration/design/img-9999.jpg",
        autorename=False,
    )
    assert result == "/inspiration/design/img-9999.jpg"


def test_move_dropbox_media_propagates_errors():
    from dropbox.exceptions import ApiError

    fake_dbx = MagicMock()
    fake_dbx.files_move_v2.side_effect = ApiError("req-id", "user-msg", "err-summary", None)

    with pytest.raises(ApiError):
        move_dropbox_media(fake_dbx, from_path="/a", to_path="/b")
```

- [ ] **Step 9: Implement `move_dropbox_media`**

Append to `bot/filers.py`:

```python
def move_dropbox_media(dropbox_client, *, from_path: str, to_path: str) -> str:
    """Move a file in Dropbox. Returns the destination path."""
    result = dropbox_client.files_move_v2(
        from_path=from_path,
        to_path=to_path,
        autorename=False,
    )
    return result.metadata.path_display
```

- [ ] **Step 10: Run all filer tests**

Run: `pytest tests/test_filers.py -v`
Expected: 8 PASS total.

- [ ] **Step 11: Commit**

```bash
git add bot/filers.py tests/test_filers.py
git commit -m "feat(session-3): filers (Obsidian, Todoist, Dropbox mover)"
```

---

## Task 5: Extend `bot/db.py` with new helpers (TDD)

**Files:**
- Modify: `bot/db.py`, `tests/test_db.py`

Add three new helpers:

- `fetch_pending_items(client, limit=50) -> list[dict]` — `select * from items where status='pending' order by created_at asc limit N`.
- `update_classified(client, item_id, *, classification, obsidian_path, todoist_task_id, api_cost_cents, status='processed') -> None` — patches the row.
- `insert_run(client, *, trigger, items_processed, items_needs_review, items_failed, total_cost_cents, started_at, completed_at) -> dict` — inserts and returns the row.
- `fetch_recent_corrections(client, limit=30) -> list[dict]` — for the classifier prompt's dynamic block.

- [ ] **Step 1: Write failing tests**

Append to `tests/test_db.py`:

```python
from bot.db import (
    fetch_pending_items,
    fetch_recent_corrections,
    insert_run,
    update_classified,
)


def test_fetch_pending_items_filters_by_status_pending():
    mock_client = MagicMock()
    mock_client.table.return_value.select.return_value.eq.return_value.order.return_value.limit.return_value.execute.return_value.data = [
        {"id": "a", "status": "pending"},
        {"id": "b", "status": "pending"},
    ]

    result = fetch_pending_items(mock_client, limit=10)

    mock_client.table.assert_called_with("items")
    mock_client.table.return_value.select.assert_called_with("*")
    mock_client.table.return_value.select.return_value.eq.assert_called_with("status", "pending")
    mock_client.table.return_value.select.return_value.eq.return_value.order.assert_called_with("created_at")
    mock_client.table.return_value.select.return_value.eq.return_value.order.return_value.limit.assert_called_with(10)
    assert len(result) == 2


def test_update_classified_patches_all_classification_fields():
    mock_client = MagicMock()
    mock_eq = mock_client.table.return_value.update.return_value.eq
    mock_eq.return_value.execute.return_value.data = [{"id": "abc"}]

    classification = {
        "project": "design",
        "subdomain": None,
        "type": "image",
        "tags": ["hero"],
        "visual_subtype": "hero",
        "summary": "x",
        "confidence": 0.9,
        "_cost_cents": 1,
    }

    update_classified(
        mock_client,
        item_id="abc",
        classification=classification,
        obsidian_path="design/2026-05-06-x.md",
        todoist_task_id=None,
        api_cost_cents=1,
    )

    mock_client.table.assert_called_with("items")
    update_payload = mock_client.table.return_value.update.call_args.args[0]
    assert update_payload["status"] == "processed"
    assert update_payload["project"] == "design"
    assert update_payload["type"] == "image"
    assert update_payload["tags"] == ["hero"]
    assert update_payload["obsidian_path"] == "design/2026-05-06-x.md"
    assert update_payload["api_cost_cents"] == 1
    assert update_payload["confidence"] == 0.9
    assert "processed_at" in update_payload
    mock_eq.assert_called_with("id", "abc")


def test_insert_run_writes_summary_row():
    from datetime import datetime, timezone

    mock_client = MagicMock()
    mock_client.table.return_value.insert.return_value.execute.return_value.data = [
        {"id": "run-1"}
    ]

    started = datetime(2026, 5, 6, 21, 0, tzinfo=timezone.utc)
    completed = datetime(2026, 5, 6, 21, 0, 12, tzinfo=timezone.utc)

    result = insert_run(
        mock_client,
        trigger="on-demand",
        items_processed=5,
        items_needs_review=1,
        items_failed=0,
        total_cost_cents=4,
        started_at=started,
        completed_at=completed,
    )

    payload = mock_client.table.return_value.insert.call_args.args[0]
    assert payload["trigger"] == "on-demand"
    assert payload["items_processed"] == 5
    assert payload["total_cost_cents"] == 4
    assert payload["started_at"] == started.isoformat()
    assert payload["completed_at"] == completed.isoformat()
    assert result["id"] == "run-1"


def test_fetch_recent_corrections_orders_newest_first():
    mock_client = MagicMock()
    mock_client.table.return_value.select.return_value.order.return_value.limit.return_value.execute.return_value.data = [
        {"original_class": {}, "corrected_class": {}},
    ]

    result = fetch_recent_corrections(mock_client, limit=30)

    mock_client.table.assert_called_with("corrections")
    mock_client.table.return_value.select.return_value.order.assert_called_with(
        "created_at", desc=True
    )
    assert len(result) == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_db.py -v -k "fetch_pending or update_classified or insert_run or fetch_recent_corrections"`
Expected: 4 FAIL with ImportError.

- [ ] **Step 3: Implement the new helpers**

Append to `bot/db.py`:

```python
from datetime import datetime, timezone


def fetch_pending_items(client, limit: int = 50) -> list[dict]:
    response = (
        client.table("items")
        .select("*")
        .eq("status", "pending")
        .order("created_at")
        .limit(limit)
        .execute()
    )
    return response.data or []


def update_classified(
    client,
    item_id: str,
    *,
    classification: dict,
    obsidian_path: str,
    todoist_task_id: str | None,
    api_cost_cents: int,
    status: str = "processed",
) -> None:
    payload = {
        "status": status,
        "processed_at": datetime.now(timezone.utc).isoformat(),
        "project": classification.get("project"),
        "subdomain": classification.get("subdomain"),
        "type": classification.get("type"),
        "tags": classification.get("tags") or [],
        "visual_subtype": classification.get("visual_subtype"),
        "summary": classification.get("summary"),
        "obsidian_path": obsidian_path,
        "todoist_task_id": todoist_task_id,
        "classified_by": "claude-haiku-4-5-20251001",
        "confidence": classification.get("confidence"),
        "api_cost_cents": api_cost_cents,
    }
    client.table("items").update(payload).eq("id", item_id).execute()


def insert_run(
    client,
    *,
    trigger: str,
    items_processed: int,
    items_needs_review: int,
    items_failed: int,
    total_cost_cents: int,
    started_at: datetime,
    completed_at: datetime,
) -> dict:
    payload = {
        "trigger": trigger,
        "items_processed": items_processed,
        "items_needs_review": items_needs_review,
        "items_failed": items_failed,
        "total_cost_cents": total_cost_cents,
        "started_at": started_at.isoformat(),
        "completed_at": completed_at.isoformat(),
    }
    response = client.table("runs").insert(payload).execute()
    return response.data[0]


def fetch_recent_corrections(client, limit: int = 30) -> list[dict]:
    response = (
        client.table("corrections")
        .select("original_class, corrected_class")
        .order("created_at", desc=True)
        .limit(limit)
        .execute()
    )
    return response.data or []
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_db.py -v`
Expected: all PASS (existing + 4 new).

- [ ] **Step 5: Commit**

```bash
git add bot/db.py tests/test_db.py
git commit -m "feat(session-3): db helpers for fetch_pending/update_classified/insert_run/fetch_recent_corrections"
```

---

## Task 6: The processor orchestrator (TDD)

**Files:**
- Create: `bot/processor.py`, `tests/test_processor.py`

`bot/processor.py` exposes:

- `enrich_item(item: dict, *, openai_api_key: str) -> str` — given an item row, returns the textual payload that goes to the classifier (raw_text + enrichment text). For voice: transcribes. For link: fetches OG (or YouTube transcript). For image with weak caption: signals to caller that vision is needed (returns `(payload, needs_vision: bool)` actually — see signature below).
- `process_item(item, *, anthropic_client, dropbox_client, openai_api_key, todoist_token, todoist_parents, vault_root, system_blocks) -> dict` — runs the full pipeline for one item. Returns `{status, classification, obsidian_path, todoist_task_id, api_cost_cents, error}`.
- `run_batch(*, supabase_client, anthropic_client, dropbox_client_factory, openai_api_key, todoist_token, todoist_parents, vault_root, rules_md, tag_vocab_md, trigger='on-demand', limit=50) -> dict` — the entry point. Pulls pending items, builds system prompt, processes each, writes a `runs` row. Returns `{items_processed, items_needs_review, items_failed, total_cost_cents}`.

Key rule: **`run_batch` must not raise on a single-item failure.** A bad item gets `status=failed` and the run continues. Only a setup-time failure (can't fetch corrections, can't build system prompt) aborts.

This task is intentionally bigger because the orchestrator is the spec's heart. We test the happy path, the vision-skip rule, the failure isolation, and the cost rollup.

- [ ] **Step 1: Write failing tests for `enrich_item`**

`tests/test_processor.py`:

```python
import pytest
from unittest.mock import MagicMock

from bot.processor import enrich_item


def test_enrich_text_item_returns_raw_text():
    item = {"media_type": "text", "raw_text": "Idea: build an MCP server.", "media_dropbox_path": None}
    payload, needs_vision = enrich_item(item, openai_api_key="x")
    assert "MCP server" in payload
    assert needs_vision is False


def test_enrich_forward_item_returns_raw_text():
    item = {"media_type": "forward", "raw_text": "Forwarded: cool article", "media_dropbox_path": None}
    payload, needs_vision = enrich_item(item, openai_api_key="x")
    assert "cool article" in payload
    assert needs_vision is False


def test_enrich_voice_item_transcribes(mocker):
    item = {"media_type": "voice", "raw_text": "", "media_dropbox_path": "/personal-os-inbox/x.ogg"}
    mocker.patch("bot.processor._download_dropbox_bytes", return_value=b"fake-ogg")
    mocker.patch("bot.processor.transcribe_voice", return_value="follow up with walmart")

    payload, needs_vision = enrich_item(item, openai_api_key="x", dropbox_client=MagicMock())
    assert "follow up with walmart" in payload
    assert needs_vision is False


def test_enrich_youtube_link_uses_transcript(mocker):
    item = {"media_type": "link", "raw_text": "https://youtu.be/abcDEF12345", "media_dropbox_path": None}
    mocker.patch("bot.processor.fetch_youtube_transcript", return_value="MCP servers explained")
    mocker.patch("bot.processor.fetch_og_metadata")  # should NOT be called

    payload, needs_vision = enrich_item(item, openai_api_key="x")
    assert "MCP servers explained" in payload
    assert "abcDEF12345" in payload  # original URL still present


def test_enrich_non_youtube_link_uses_og(mocker):
    item = {"media_type": "link", "raw_text": "https://example.com/post", "media_dropbox_path": None}
    yt_mock = mocker.patch("bot.processor.fetch_youtube_transcript")
    og_mock = mocker.patch(
        "bot.processor.fetch_og_metadata",
        return_value={"title": "Post", "description": "About something", "image": "", "site_name": ""},
    )

    payload, needs_vision = enrich_item(item, openai_api_key="x")
    assert "Post" in payload
    assert "About something" in payload
    yt_mock.assert_not_called()


def test_enrich_image_with_short_caption_needs_vision():
    item = {"media_type": "image", "raw_text": "x", "media_dropbox_path": "/personal-os-inbox/x.jpg"}
    payload, needs_vision = enrich_item(item, openai_api_key="x")
    assert needs_vision is True


def test_enrich_image_with_decisive_caption_skips_vision():
    item = {
        "media_type": "image",
        "raw_text": "lake arrowhead kitchen tile inspiration",
        "media_dropbox_path": "/personal-os-inbox/x.jpg",
    }
    payload, needs_vision = enrich_item(item, openai_api_key="x")
    assert needs_vision is False
    assert "lake arrowhead" in payload.lower()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_processor.py -v`
Expected: 7 FAIL with ImportError.

- [ ] **Step 3: Implement `enrich_item`**

`bot/processor.py`:

```python
"""Per-item processing orchestrator. Pure-Python; no FastAPI imports."""
import logging
from datetime import datetime, timezone

from bot.enrichment import (
    fetch_og_metadata,
    fetch_youtube_transcript,
    transcribe_voice,
)
from bot.filers import (
    create_todoist_task,
    move_dropbox_media,
    write_obsidian_note,
)
from bot.llm import classify_item

logger = logging.getLogger(__name__)


# Project keywords used for the "decisive caption → skip vision" rule.
_PROJECT_KEYWORDS = {
    "acute": ("acute", "freight", "ltl", "truckload", "walmart", "shipping", "carrier"),
    "abp": ("abp", "c3bank", "bridge loan", "construction loan"),
    "lake-arrowhead": ("lake arrowhead", "cabin", "kitchen tile", "bathroom tile"),
    "church": ("come follow me", "gospel", "church", "talk", "sunday"),
    "claude-build": ("claude", "anthropic", "mcp", "skill", "agent"),
    "design": ("hero", "nav", "typography", "color palette", "branding"),
    "personal": ("surf", "woodwork", "leather", "carlsbad"),
}

_VISION_SKIP_MIN_CAPTION_LEN = 8


def _caption_is_decisive(caption: str) -> bool:
    """Per spec: caption length > 8 chars AND matches a known project keyword."""
    if not caption or len(caption) <= _VISION_SKIP_MIN_CAPTION_LEN:
        return False
    lowered = caption.lower()
    for keywords in _PROJECT_KEYWORDS.values():
        for kw in keywords:
            if kw in lowered:
                return True
    return False


def _download_dropbox_bytes(dropbox_client, path: str) -> bytes:
    _, response = dropbox_client.files_download(path=path)
    return response.content


def enrich_item(
    item: dict,
    *,
    openai_api_key: str,
    dropbox_client=None,
) -> tuple[str, bool]:
    """Return (classifier_payload_text, needs_vision)."""
    media_type = item["media_type"]
    raw_text = item.get("raw_text") or ""

    if media_type in ("text", "forward"):
        return raw_text, False

    if media_type == "voice":
        if dropbox_client is None or not item.get("media_dropbox_path"):
            return raw_text, False
        try:
            audio_bytes = _download_dropbox_bytes(dropbox_client, item["media_dropbox_path"])
            transcript = transcribe_voice(openai_api_key, audio_bytes, file_extension=".ogg")
            return f"[voice transcript]\n{transcript}", False
        except Exception as e:
            logger.warning("voice transcription failed: %s", e)
            return raw_text, False

    if media_type == "link":
        url = raw_text.strip()
        try:
            transcript = fetch_youtube_transcript(url)
            return f"[link]\nURL: {url}\n[YouTube transcript]\n{transcript}", False
        except ValueError:
            pass  # not a YouTube link or no transcript

        try:
            import asyncio
            og = asyncio.run(fetch_og_metadata(url))
            return (
                f"[link]\nURL: {url}\nTitle: {og['title']}\nDescription: {og['description']}\nSite: {og['site_name']}",
                False,
            )
        except Exception as e:
            logger.warning("OG fetch failed for %s: %s", url, e)
            return f"[link]\nURL: {url}", False

    if media_type == "image":
        caption = raw_text
        if _caption_is_decisive(caption):
            return f"[image with decisive caption]\nCaption: {caption}", False
        return f"[image]\nCaption: {caption}\n(vision required)", True

    if media_type == "video":
        return f"[video]\nCaption: {raw_text}", False

    return raw_text, False
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_processor.py -v`
Expected: 7 PASS.

- [ ] **Step 5: Write failing test for `process_item` (happy path)**

Append to `tests/test_processor.py`:

```python
from bot.processor import process_item


def _fake_classify_response(project, type_, tags, summary, cost=1):
    return {
        "project": project,
        "subdomain": None,
        "type": type_,
        "tags": tags,
        "visual_subtype": None,
        "summary": summary,
        "confidence": 0.9,
        "_cost_cents": cost,
    }


def test_process_item_happy_path_text(mocker):
    item = {
        "id": "item-1",
        "media_type": "text",
        "raw_text": "Idea: build a Todoist MCP server.",
        "media_dropbox_path": None,
    }

    mocker.patch("bot.processor.classify_item", return_value=_fake_classify_response(
        "claude-build", "idea", ["ai", "mcp"], "Build a Todoist MCP server."
    ))
    mocker.patch("bot.processor.write_obsidian_note", return_value="claude-build/2026-05-06-build-todoist-mcp.md")
    mocker.patch("bot.processor.create_todoist_task")  # NOT called for type=idea

    result = process_item(
        item,
        anthropic_client=MagicMock(),
        dropbox_client=MagicMock(),
        openai_api_key="x",
        todoist_token="t",
        todoist_parents={"claude-build": "1000005"},
        vault_root="/personal-os",
        system_blocks=[{"type": "text", "text": "s"}],
    )

    assert result["status"] == "processed"
    assert result["classification"]["project"] == "claude-build"
    assert result["obsidian_path"] == "claude-build/2026-05-06-build-todoist-mcp.md"
    assert result["todoist_task_id"] is None
    assert result["api_cost_cents"] == 1


def test_process_item_creates_todoist_task_when_type_is_todo(mocker):
    item = {
        "id": "item-2",
        "media_type": "text",
        "raw_text": "follow up with walmart",
        "media_dropbox_path": None,
    }

    mocker.patch("bot.processor.classify_item", return_value=_fake_classify_response(
        "acute", "todo", ["prospecting"], "Follow up with Walmart contact."
    ))
    mocker.patch("bot.processor.write_obsidian_note", return_value="acute/2026-05-06-walmart.md")
    mocker.patch("bot.processor.create_todoist_task", return_value="todoist-task-9999")

    result = process_item(
        item,
        anthropic_client=MagicMock(),
        dropbox_client=MagicMock(),
        openai_api_key="x",
        todoist_token="t",
        todoist_parents={"acute": "1000001"},
        vault_root="/personal-os",
        system_blocks=[{"type": "text", "text": "s"}],
    )

    assert result["todoist_task_id"] == "todoist-task-9999"


def test_process_item_moves_media_for_image(mocker):
    item = {
        "id": "item-3",
        "media_type": "image",
        "raw_text": "design hero with dark gradient",
        "media_dropbox_path": "/personal-os-inbox/2026-05-06/item-3.jpg",
    }

    mocker.patch("bot.processor.classify_item", return_value=_fake_classify_response(
        "design", "image", ["hero", "dark-mode"], "Hero with dark gradient."
    ))
    mocker.patch("bot.processor.write_obsidian_note", return_value="design/2026-05-06-hero.md")
    move_mock = mocker.patch(
        "bot.processor.move_dropbox_media",
        return_value="/inspiration/design/item-3.jpg",
    )

    result = process_item(
        item,
        anthropic_client=MagicMock(),
        dropbox_client=MagicMock(),
        openai_api_key="x",
        todoist_token="t",
        todoist_parents={"design": "1000006"},
        vault_root="/personal-os",
        system_blocks=[{"type": "text", "text": "s"}],
    )

    assert result["status"] == "processed"
    move_mock.assert_called_once()
    move_kwargs = move_mock.call_args.kwargs
    assert move_kwargs["from_path"] == "/personal-os-inbox/2026-05-06/item-3.jpg"
    assert move_kwargs["to_path"].startswith("/inspiration/design/")


def test_process_item_marks_failed_on_classifier_error(mocker):
    item = {
        "id": "item-4",
        "media_type": "text",
        "raw_text": "x",
        "media_dropbox_path": None,
    }
    mocker.patch("bot.processor.classify_item", side_effect=ValueError("not valid JSON"))

    result = process_item(
        item,
        anthropic_client=MagicMock(),
        dropbox_client=MagicMock(),
        openai_api_key="x",
        todoist_token="t",
        todoist_parents={},
        vault_root="/personal-os",
        system_blocks=[{"type": "text", "text": "s"}],
    )

    assert result["status"] == "failed"
    assert "not valid JSON" in result["error"]


def test_process_item_marks_needs_review_on_low_confidence(mocker):
    item = {
        "id": "item-5",
        "media_type": "text",
        "raw_text": "x",
        "media_dropbox_path": None,
    }
    low_conf = _fake_classify_response("personal", "idea", [], "Unclear")
    low_conf["confidence"] = 0.4

    mocker.patch("bot.processor.classify_item", return_value=low_conf)
    mocker.patch("bot.processor.write_obsidian_note", return_value="personal/2026-05-06-x.md")

    result = process_item(
        item,
        anthropic_client=MagicMock(),
        dropbox_client=MagicMock(),
        openai_api_key="x",
        todoist_token="t",
        todoist_parents={"personal": "1000007"},
        vault_root="/personal-os",
        system_blocks=[{"type": "text", "text": "s"}],
    )

    assert result["status"] == "needs_review"
```

- [ ] **Step 6: Implement `process_item`**

Append to `bot/processor.py`:

```python
NEEDS_REVIEW_THRESHOLD = 0.6


def _post_classify_dropbox_path(item_id: str, project: str, type_: str, original_path: str) -> str:
    """Compute where media should live after classification.
    Design captures → /inspiration/<project>/. Project media → /projects/<project>/media/."""
    ext = "." + original_path.rsplit(".", 1)[-1] if "." in original_path else ""
    if project == "design":
        return f"/inspiration/design/{item_id}{ext}"
    return f"/projects/{project}/media/{item_id}{ext}"


def process_item(
    item: dict,
    *,
    anthropic_client,
    dropbox_client,
    openai_api_key: str,
    todoist_token: str,
    todoist_parents: dict,
    vault_root: str,
    system_blocks: list[dict],
) -> dict:
    """Run the full pipeline for one item. Never raises — failures land in the result."""
    out = {
        "status": "processed",
        "classification": None,
        "obsidian_path": None,
        "todoist_task_id": None,
        "api_cost_cents": 0,
        "error": None,
    }

    try:
        payload, _needs_vision = enrich_item(
            item,
            openai_api_key=openai_api_key,
            dropbox_client=dropbox_client,
        )
        # Note: vision-call wiring is deferred — for now, even if needs_vision is True,
        # we send text-only and rely on the caption. Session 4 wires up vision.

        classification = classify_item(anthropic_client, system_blocks, payload)
        out["classification"] = classification
        out["api_cost_cents"] = classification.get("_cost_cents", 0)

        # Move media to its post-classify home.
        new_media_path = item.get("media_dropbox_path")
        if item.get("media_dropbox_path") and item["media_type"] in ("image", "video", "voice"):
            try:
                new_media_path = _post_classify_dropbox_path(
                    item["id"],
                    classification.get("project") or "personal",
                    classification.get("type") or "image",
                    item["media_dropbox_path"],
                )
                move_dropbox_media(
                    dropbox_client,
                    from_path=item["media_dropbox_path"],
                    to_path=new_media_path,
                )
            except Exception as e:
                logger.warning("media move failed for item %s: %s", item["id"], e)
                new_media_path = item["media_dropbox_path"]

        obsidian_path = write_obsidian_note(
            dropbox_client=dropbox_client,
            vault_root=vault_root,
            item_id=item["id"],
            classification=classification,
            raw_text=item.get("raw_text") or "",
            media_dropbox_path=new_media_path,
        )
        out["obsidian_path"] = obsidian_path

        if classification.get("type") == "todo":
            project = classification.get("project") or "personal"
            parent_id = todoist_parents.get(project)
            if parent_id:
                try:
                    out["todoist_task_id"] = create_todoist_task(
                        api_token=todoist_token,
                        parent_task_id=parent_id,
                        content=classification.get("summary") or item.get("raw_text") or "(no content)",
                        description=item.get("raw_text") if classification.get("summary") else None,
                    )
                except Exception as e:
                    logger.warning("Todoist create failed for item %s: %s", item["id"], e)

        if (classification.get("confidence") or 0.0) < NEEDS_REVIEW_THRESHOLD:
            out["status"] = "needs_review"

    except Exception as e:
        logger.exception("process_item failed for %s", item.get("id"))
        out["status"] = "failed"
        out["error"] = str(e)

    return out
```

- [ ] **Step 7: Run tests**

Run: `pytest tests/test_processor.py -v -k process_item`
Expected: 5 PASS.

- [ ] **Step 8: Write failing test for `run_batch`**

Append to `tests/test_processor.py`:

```python
from bot.processor import run_batch


def test_run_batch_processes_all_pending_items(mocker):
    pending = [
        {"id": "i1", "media_type": "text", "raw_text": "first", "media_dropbox_path": None},
        {"id": "i2", "media_type": "text", "raw_text": "second", "media_dropbox_path": None},
    ]

    mocker.patch("bot.processor.fetch_pending_items", return_value=pending)
    mocker.patch("bot.processor.fetch_recent_corrections", return_value=[])

    process_mock = mocker.patch(
        "bot.processor.process_item",
        side_effect=[
            {"status": "processed", "classification": {"project": "personal", "type": "idea", "tags": [], "summary": "x", "confidence": 0.9}, "obsidian_path": "personal/x.md", "todoist_task_id": None, "api_cost_cents": 1, "error": None},
            {"status": "processed", "classification": {"project": "personal", "type": "idea", "tags": [], "summary": "y", "confidence": 0.9}, "obsidian_path": "personal/y.md", "todoist_task_id": None, "api_cost_cents": 2, "error": None},
        ],
    )
    update_mock = mocker.patch("bot.processor.update_classified")
    insert_run_mock = mocker.patch("bot.processor.insert_run", return_value={"id": "run-x"})

    fake_dropbox_factory = MagicMock(return_value=MagicMock())

    result = run_batch(
        supabase_client=MagicMock(),
        anthropic_client=MagicMock(),
        dropbox_client_factory=fake_dropbox_factory,
        openai_api_key="x",
        todoist_token="t",
        todoist_parents={"personal": "1000007"},
        vault_root="/personal-os",
        rules_md="",
        tag_vocab_md="",
    )

    assert result["items_processed"] == 2
    assert result["items_failed"] == 0
    assert result["items_needs_review"] == 0
    assert result["total_cost_cents"] == 3
    assert process_mock.call_count == 2
    assert update_mock.call_count == 2
    insert_run_mock.assert_called_once()


def test_run_batch_isolates_failures(mocker):
    pending = [
        {"id": "i1", "media_type": "text", "raw_text": "ok", "media_dropbox_path": None},
        {"id": "i2", "media_type": "text", "raw_text": "broken", "media_dropbox_path": None},
        {"id": "i3", "media_type": "text", "raw_text": "ok again", "media_dropbox_path": None},
    ]

    mocker.patch("bot.processor.fetch_pending_items", return_value=pending)
    mocker.patch("bot.processor.fetch_recent_corrections", return_value=[])
    mocker.patch(
        "bot.processor.process_item",
        side_effect=[
            {"status": "processed", "classification": {"project": "personal", "type": "idea", "tags": [], "summary": "a", "confidence": 0.9}, "obsidian_path": "p/a.md", "todoist_task_id": None, "api_cost_cents": 1, "error": None},
            {"status": "failed",    "classification": None, "obsidian_path": None, "todoist_task_id": None, "api_cost_cents": 0, "error": "boom"},
            {"status": "processed", "classification": {"project": "personal", "type": "idea", "tags": [], "summary": "c", "confidence": 0.9}, "obsidian_path": "p/c.md", "todoist_task_id": None, "api_cost_cents": 1, "error": None},
        ],
    )
    mocker.patch("bot.processor.update_classified")
    mocker.patch("bot.processor.insert_run", return_value={"id": "run-x"})

    result = run_batch(
        supabase_client=MagicMock(),
        anthropic_client=MagicMock(),
        dropbox_client_factory=lambda: MagicMock(),
        openai_api_key="x",
        todoist_token="t",
        todoist_parents={},
        vault_root="/personal-os",
        rules_md="",
        tag_vocab_md="",
    )

    assert result["items_processed"] == 2
    assert result["items_failed"] == 1
    assert result["items_needs_review"] == 0


def test_run_batch_handles_empty_queue(mocker):
    mocker.patch("bot.processor.fetch_pending_items", return_value=[])
    mocker.patch("bot.processor.fetch_recent_corrections", return_value=[])
    insert_run_mock = mocker.patch("bot.processor.insert_run", return_value={"id": "run-x"})

    result = run_batch(
        supabase_client=MagicMock(),
        anthropic_client=MagicMock(),
        dropbox_client_factory=lambda: MagicMock(),
        openai_api_key="x",
        todoist_token="t",
        todoist_parents={},
        vault_root="/personal-os",
        rules_md="",
        tag_vocab_md="",
    )

    assert result["items_processed"] == 0
    assert result["items_failed"] == 0
    insert_run_mock.assert_called_once()
```

- [ ] **Step 9: Implement `run_batch`**

Append to `bot/processor.py`:

```python
from bot.db import (
    fetch_pending_items,
    fetch_recent_corrections,
    insert_run,
    update_classified,
)
from bot.llm import build_classifier_system_prompt


def run_batch(
    *,
    supabase_client,
    anthropic_client,
    dropbox_client_factory,
    openai_api_key: str,
    todoist_token: str,
    todoist_parents: dict,
    vault_root: str,
    rules_md: str,
    tag_vocab_md: str,
    trigger: str = "on-demand",
    limit: int = 50,
) -> dict:
    started = datetime.now(timezone.utc)

    pending = fetch_pending_items(supabase_client, limit=limit)
    corrections = fetch_recent_corrections(supabase_client, limit=30)
    system_blocks = build_classifier_system_prompt(rules_md, tag_vocab_md, corrections)

    counts = {"items_processed": 0, "items_needs_review": 0, "items_failed": 0, "total_cost_cents": 0}

    dropbox_client = dropbox_client_factory()

    for item in pending:
        result = process_item(
            item,
            anthropic_client=anthropic_client,
            dropbox_client=dropbox_client,
            openai_api_key=openai_api_key,
            todoist_token=todoist_token,
            todoist_parents=todoist_parents,
            vault_root=vault_root,
            system_blocks=system_blocks,
        )

        counts["total_cost_cents"] += result["api_cost_cents"] or 0
        if result["status"] == "failed":
            counts["items_failed"] += 1
            try:
                supabase_client.table("items").update(
                    {"status": "failed", "error": result["error"], "processed_at": datetime.now(timezone.utc).isoformat()}
                ).eq("id", item["id"]).execute()
            except Exception:
                logger.exception("could not mark item %s failed", item["id"])
            continue

        if result["status"] == "needs_review":
            counts["items_needs_review"] += 1
        else:
            counts["items_processed"] += 1

        try:
            update_classified(
                supabase_client,
                item_id=item["id"],
                classification=result["classification"],
                obsidian_path=result["obsidian_path"],
                todoist_task_id=result["todoist_task_id"],
                api_cost_cents=result["api_cost_cents"],
                status=result["status"],
            )
        except Exception:
            logger.exception("update_classified failed for %s", item["id"])

    completed = datetime.now(timezone.utc)

    try:
        insert_run(
            supabase_client,
            trigger=trigger,
            items_processed=counts["items_processed"],
            items_needs_review=counts["items_needs_review"],
            items_failed=counts["items_failed"],
            total_cost_cents=counts["total_cost_cents"],
            started_at=started,
            completed_at=completed,
        )
    except Exception:
        logger.exception("insert_run failed")

    counts["duration_seconds"] = (completed - started).total_seconds()
    return counts
```

- [ ] **Step 10: Run all processor tests**

Run: `pytest tests/test_processor.py -v`
Expected: all PASS (7 enrich + 5 process_item + 3 run_batch).

- [ ] **Step 11: Commit**

```bash
git add bot/processor.py tests/test_processor.py
git commit -m "feat(session-3): processor orchestrator + run_batch with failure isolation"
```

---

## Task 7: `/process` command in webhook (TDD)

**Files:**
- Modify: `bot/main.py`, `tests/test_webhook.py`
- Create: `tests/fixtures/update_process_command.json`

When the user texts `/process` to the bot, the webhook should:

1. Insert the `/process` text as an item normally? **No** — it's a command, not a capture. Skip the `insert_item` call when `raw_text` starts with `/process`.
2. Schedule a `run_batch` background task.
3. Reply with 👍 immediately.
4. After the batch completes, reply (in the same chat, replying to the original `/process` message) with a one-liner: `processed N items in Xs · $0.YZ · M needs review`.

- [ ] **Step 1: Save the test fixture**

`tests/fixtures/update_process_command.json`:

```json
{
  "update_id": 100100,
  "message": {
    "message_id": 200,
    "from": {"id": 12345, "is_bot": false, "first_name": "Ryan"},
    "chat": {"id": 12345, "type": "private"},
    "date": 1746565000,
    "text": "/process"
  }
}
```

- [ ] **Step 2: Write failing tests**

Append to `tests/test_webhook.py`:

```python
def test_process_command_does_not_insert_item(client, mock_sb, load_fixture):
    update = load_fixture("update_process_command.json")
    response = client.post("/webhook/test_secret", json=update)
    assert response.status_code == 200
    # The bot should NOT have called insert on items for the /process command itself.
    insert_calls = [
        c for c in mock_sb.table.return_value.insert.call_args_list
        if "source_message_id" in (c.args[0] if c.args else c.kwargs.get("data", {}))
    ]
    assert insert_calls == []


def test_process_command_schedules_run_batch(mocker, client, mock_sb, load_fixture):
    run_batch_mock = mocker.patch(
        "bot.main.run_batch",
        return_value={"items_processed": 2, "items_needs_review": 0, "items_failed": 0,
                      "total_cost_cents": 3, "duration_seconds": 4.2},
    )
    mocker.patch("bot.main.send_message")  # avoid real Telegram call

    update = load_fixture("update_process_command.json")
    response = client.post("/webhook/test_secret", json=update)
    assert response.status_code == 200

    run_batch_mock.assert_called_once()


def test_process_command_replies_with_summary(mocker, client, mock_sb, load_fixture):
    mocker.patch(
        "bot.main.run_batch",
        return_value={"items_processed": 2, "items_needs_review": 1, "items_failed": 0,
                      "total_cost_cents": 4, "duration_seconds": 6.0},
    )
    send_mock = mocker.patch("bot.main.send_message")

    update = load_fixture("update_process_command.json")
    client.post("/webhook/test_secret", json=update)

    # send_message should have been called twice: once for 👍 ack, once for summary.
    assert send_mock.call_count >= 2
    summary_call = send_mock.call_args_list[-1]
    summary_text = summary_call.kwargs.get("text") or summary_call.args[2]
    assert "2" in summary_text          # items_processed
    assert "$0.04" in summary_text      # cost
    assert "1 needs review" in summary_text or "1 review" in summary_text
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `pytest tests/test_webhook.py -v -k process`
Expected: 3 FAIL.

- [ ] **Step 4: Implement the `/process` handler in `bot/main.py`**

Refactor the existing `webhook` handler to branch on `/process`. Add a `send_message` helper, and a `_run_batch_and_reply` background task.

Show the relevant chunks of `bot/main.py` after the change:

```python
# At top with other imports:
from bot.processor import run_batch

# After config / supabase / dropbox_factory / app setup, add Anthropic client lazily:
import anthropic
anthropic_client = anthropic.Anthropic(api_key=config.anthropic_api_key)

# Read seed files at startup so the processor doesn't re-read on every batch.
from pathlib import Path
RULES_MD = Path("_meta/rules.md").read_text(encoding="utf-8") if Path("_meta/rules.md").exists() else ""
TAG_VOCAB_MD = Path("_meta/tag_vocab.md").read_text(encoding="utf-8") if Path("_meta/tag_vocab.md").exists() else ""


async def send_message(chat_id: int, reply_to: int, text: str) -> None:
    try:
        async with httpx.AsyncClient(timeout=10.0) as http:
            await http.post(
                f"https://api.telegram.org/bot{config.bot_token}/sendMessage",
                json={
                    "chat_id": chat_id,
                    "text": text,
                    "reply_to_message_id": reply_to,
                },
            )
    except Exception:
        logger.exception("send_message failed for chat %s", chat_id)


async def _run_batch_and_reply(chat_id: int, reply_to: int) -> None:
    try:
        result = run_batch(
            supabase_client=supabase,
            anthropic_client=anthropic_client,
            dropbox_client_factory=dropbox_factory.get_client,
            openai_api_key=config.openai_api_key,
            todoist_token=config.todoist_api_token,
            todoist_parents=config.todoist_parents,
            vault_root=config.obsidian_vault_dropbox_path,
            rules_md=RULES_MD,
            tag_vocab_md=TAG_VOCAB_MD,
        )
        cost_dollars = result["total_cost_cents"] / 100.0
        text = (
            f"processed {result['items_processed']} items "
            f"in {result['duration_seconds']:.1f}s · "
            f"${cost_dollars:.2f} · "
            f"{result['items_needs_review']} needs review"
        )
        if result["items_failed"]:
            text += f" · {result['items_failed']} failed"
    except Exception:
        logger.exception("/process run failed")
        text = "/process failed — check journalctl"

    await send_message(chat_id, reply_to, text)
```

In the `webhook` handler, replace the existing flow:

```python
@app.post("/webhook/{secret}")
async def webhook(secret: str, request: Request, background_tasks: BackgroundTasks):
    if secret != config.webhook_secret:
        raise HTTPException(status_code=403, detail="invalid webhook secret")

    update = await request.json()

    msg = update.get("message") or update.get("channel_post") or {}
    sender_id = (msg.get("from") or {}).get("id")
    if sender_id != config.my_telegram_id:
        logger.info("dropping update from unauthorized sender id=%s", sender_id)
        return {"ok": True}

    chat_id = msg["chat"]["id"]
    message_id = msg["message_id"]
    text = (msg.get("text") or "").strip()

    # Command handling
    if text == "/process":
        background_tasks.add_task(send_ack, chat_id=chat_id, reply_to=message_id)
        background_tasks.add_task(_run_batch_and_reply, chat_id=chat_id, reply_to=message_id)
        return {"ok": True}

    # Normal capture flow (Session 2 logic, unchanged)
    try:
        intake = parse_update(update)
    except ValueError as exc:
        logger.warning("could not parse update: %s", exc)
        return {"ok": True}

    item = insert_item(
        supabase,
        source_message_id=str(message_id),
        raw_text=intake["raw_text"],
        media_type=intake["media_type"],
        media_telegram_file_id=intake["media_telegram_file_id"],
    )

    if intake["media_telegram_file_id"]:
        background_tasks.add_task(
            handle_media,
            item_id=item["id"],
            file_id=intake["media_telegram_file_id"],
            ext=ext_for_media_type(intake["media_type"]),
        )

    background_tasks.add_task(send_ack, chat_id=chat_id, reply_to=message_id)
    return {"ok": True}
```

- [ ] **Step 5: Run all webhook tests**

Run: `pytest tests/test_webhook.py -v`
Expected: all PASS — including the 3 new `/process` tests and all existing Session 2 tests.

- [ ] **Step 6: Commit**

```bash
git add bot/main.py tests/test_webhook.py tests/fixtures/update_process_command.json
git commit -m "feat(session-3): /process command runs an on-demand batch"
```

---

## Task 8: Full test-suite green check

- [ ] **Step 1: Run the entire suite**

Run: `pytest -v`
Expected: 100% PASS, no warnings about deprecated APIs, no flakiness on the webhook timing test.

- [ ] **Step 2: If anything fails, fix before deploying**

The most likely regressions are:
- `test_webhook.py` Session 2 tests broke because the new `webhook` flow rearranged the `/process` branch. Fix by ensuring the non-`/process` path is byte-identical to Session 2.
- `Config.from_env()` test broke because the `env` fixture in conftest.py wasn't fully updated. Re-check Task 1 Step 4.

---

## Task 9: Deploy + smoke test (operator-driven)

This task is operator-driven — Ryan runs the commands; the agent reads the output. Same shape as Session 2's Task 12, but smaller because we're updating an already-deployed service rather than cutting over from a v1.

- [ ] **Step 1: Push the branch to GitHub**

```powershell
git push origin main
```

- [ ] **Step 2: Pull + redeploy on the droplet**

```powershell
ssh root@64.23.170.115 'cd /opt/personal-os-v2 && git pull && venv/bin/pip install -r requirements.txt && systemctl restart personal-os-v2 && sleep 3 && systemctl status personal-os-v2 --no-pager | head -10'
```

Expected: `Active: active (running)` and recent log lines.

- [ ] **Step 3: Append new env vars to droplet's .env**

You added 11 new vars during prerequisites. They need to land on the droplet. From your local repo (which has the new vars in `.env`):

```powershell
Get-Content .env -Raw | ssh root@64.23.170.115 'tr -d "\r" > /opt/personal-os-v2/.env && chmod 600 /opt/personal-os-v2/.env && systemctl restart personal-os-v2 && sleep 3 && systemctl status personal-os-v2 --no-pager | head -10'
```

(This pipes the entire local `.env` over, replacing the droplet's. That's fine — same 8 old vars plus 11 new ones.)

Expected: `Active: active (running)` after restart.

- [ ] **Step 4: Send a test capture, then /process**

From your phone:
1. Send any text message to Roscoe — e.g., "session 3 smoke test idea — ai".
2. Wait for 👍.
3. Send `/process`.
4. You should get 👍 immediately, then within a few seconds a follow-up: `"processed 1 items in X.Xs · $0.0Y · 0 needs review"`.

- [ ] **Step 5: Verify the row got classified**

```sql
-- Run via Supabase MCP execute_sql
select id, status, project, type, tags, obsidian_path, api_cost_cents, confidence
from items
where created_at > now() - interval '1 hour'
order by created_at desc;
```

Expected: status=`processed`, project + type populated, tags non-empty, obsidian_path populated.

- [ ] **Step 6: Verify the Obsidian note exists**

In Obsidian on your laptop, navigate to `<vault>/<project>/<YYYY-MM-DD>-...md`. You should see the note with frontmatter + summary + raw capture.

(If the note doesn't appear locally for ~1 minute, it's Dropbox sync lag — the note is at the Dropbox path the moment the processor wrote it.)

- [ ] **Step 7: Send a /process todo and verify Todoist**

From your phone:
1. Send "todo: session 3 smoke - call vendor about freight rate".
2. Send `/process`.
3. After the summary reply, open Todoist on your phone or web. The new task should appear under `#Acute` (or whichever project the classifier picked).

If the classifier put the todo under the wrong project — that's expected during the validation week. Take the correction with a screenshot for now (Session 4 wires up the formal correction UI).

- [ ] **Step 8: Send /process with a design image**

1. Find a design screenshot (hero, nav, typography example).
2. Send to Roscoe with no caption.
3. Send `/process`.
4. Expected: classifier returns `project=design`, type=image, visual_subtype set. The `.md` note in `<vault>/design/` embeds the image. The image moved from `/personal-os-inbox/` to `/inspiration/design/` in Dropbox.

- [ ] **Step 9: Verify the runs table has a row**

```sql
select id, trigger, items_processed, items_needs_review, items_failed, total_cost_cents,
       (completed_at - started_at) as duration
from runs
order by started_at desc
limit 5;
```

Expected: at least one row with `trigger='on-demand'`, your processed counts, and a small total_cost_cents (single digits).

---

## Verification gate (Session 3 done-ness)

Session 3 is shippable when **all** of these are true:

- [ ] All unit tests pass locally (`pytest -v`).
- [ ] `/process` round-trip works end-to-end on at least 5 real captures spanning text, link, image, voice, todo.
- [ ] At least one item per project (acute, abp, lake-arrowhead, church, claude-build, design, personal) has been classified correctly. (You may need to invent contrived captures to hit some projects.)
- [ ] At least one `todo`-type item has a Todoist task created.
- [ ] At least one `image` item has its media moved out of `/personal-os-inbox/` into `/inspiration/` or `/projects/`.
- [ ] At least one `link`-type item has OG metadata reflected in the classifier's summary (e.g., the article title appears).
- [ ] At least one `voice`-type item has a transcript stored in `raw_text` or visible in the classifier's summary.
- [ ] `runs` table has at least one row per `/process` invocation.
- [ ] No item ends up with `status='failed'` from the smoke captures.
- [ ] Total cost across the smoke run is under $0.10.
- [ ] Reboot survival still works: `ssh root@... 'reboot'` then 60 seconds later confirm `personal-os-v2` is `active (running)` and `/process` still works.
- [ ] Code committed and pushed to `main`.

After verification, **let the system run live for at least one full week before starting Session 4.** The point of the live week is exactly the spec's instruction: "Refine taxonomy from real captures, not from theory."

During the validation week, do these things daily (no code, just observation):

1. Trigger `/process` manually a few times per day.
2. Spot-check the Obsidian notes — are projects + types right?
3. When something is misclassified, **note it down** (even informally — sticky notes, a Todoist task, voice memo to Roscoe). Session 4 builds the corrections UI; for now, manual notes are the source.
4. Check `select count(*) by media_type, project` once at end of week to see the volume distribution.
5. If a *systematic* misclassification pattern appears (e.g., every C3bank article goes to `personal`), add a rule to `_meta/rules.md` and redeploy. That's the point of `rules.md`.

---

## What Session 4 will add (forward-looking; do not implement now)

- Daily summary sent at 6:30 AM and 9:00 PM (B+ format from spec).
- Tap-to-refile triage flow with inline keyboard buttons.
- `corrections` rows written when user refiles.
- Vision wired up for image classification (currently `needs_vision` is computed but not consumed).
- `/find <query>` command with full-text search.

What Session 5 will add: cron at 6:30 / 12:00 / 21:00, daily cost cap with hard halt, `processed_at` based on cron run boundaries.

---

## Open items to settle during this session (not blockers)

- **Should `/process` reply include a per-item breakdown?** Spec's daily summary is detailed; spec's `/process` reply is unspecified. The plan currently delivers a one-liner. If during the validation week you want more detail, that's a 5-line edit to `_run_batch_and_reply` — pull the per-item statuses from `process_item` results and format. Defer the call until you've used it for a few days.
- **Should the classifier see images directly?** The plan currently sends only the caption + a "(vision required)" marker. Wiring up vision is straightforward (Anthropic SDK supports image content blocks) but adds cost (~3-4x per image) and complexity. The spec defers this to Session 4 explicitly. If the validation week shows the classifier struggling on uncaptioned images, raise to user before forcing it in.
- **What happens if `/process` runs while a previous `/process` is still in flight?** Currently: both run, both write to the same `items` rows. Supabase will accept both updates; whichever finishes second wins. Acceptable for v1 since this is a single user. Add a `runs.status=in_progress` lock if it ever bites.

---

## Self-review (run by the planner, not the executor)

Spec sections covered:
- ✅ Capture (intake) — already shipped Session 2; unchanged here
- ✅ Processing (per item, in batch) — Tasks 2-7
- ✅ Storage destinations — Task 4 (Obsidian, Todoist, Dropbox)
- ✅ Project taxonomy — Task 2 (system prompt) + Task 1 (config parents)
- ✅ Cost model — Task 2 (cost_cents_from_usage), Task 6 (rollup), Task 7 (summary reply)
- ⏸ UX: Daily summary — explicitly deferred to Session 4
- ⏸ UX: /find — explicitly deferred to Session 7
- ⏸ UX: Weekly digest / monthly rules — Session 6
- ⏸ Corrections + rules feedback loop — partial (Task 2 wires corrections into prompt; UI in Session 4)
- ✅ Failure modes — Task 6 (per-item failure isolation), spec's other failure modes (rate limit, cost cap) deferred to Session 5

No placeholder ("TBD", "implement later") strings in tasks. All steps have actual code or explicit commands.

Type / signature consistency check:
- `classify_item` returns dict with `_cost_cents` key — used in `process_item` ✅
- `write_obsidian_note` returns string `obsidian_path` — passed to `update_classified` ✅
- `create_todoist_task` returns string task ID — stored in `todoist_task_id` column ✅
- `move_dropbox_media` return value not used; `_post_classify_dropbox_path` is what gets stored ⚠ — fine, but worth noting the move's return is purely for logging.
- `run_batch` returns `{items_processed, items_needs_review, items_failed, total_cost_cents, duration_seconds}` — matches what `_run_batch_and_reply` reads ✅
