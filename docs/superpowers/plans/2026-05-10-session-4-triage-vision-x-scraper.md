# Session 4 — Triage Flow + Vision + X Scraper + Daily Summary

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the biggest gaps left by Session 3: X URLs that classify badly because of missing scraped content, image items that classify badly because of no vision, the absence of any operator-facing summary/triage surface, and the absence of a corrections feedback loop. All four ship together so the daily summary can pull from real classifications that have already benefited from scraping + vision.

**Architecture:** Four phases sharing one schema migration. Phase A adds an Apify-backed scraper for X URLs, a normalized `source_posts` table, and a regex-based Midjourney parameter extractor. Phase B adds a vision pass that fires conditionally (text-only classify first; vision only when the first pass returns `project=design` AND `type=image`). Phase C adds APScheduler running inside the FastAPI process, with daily summary jobs at 06:30 and 21:00 local time. Phase D adds the inline-keyboard triage flow and the `corrections` table that feeds Session 6's rule consolidation. Multi-image X posts fan out into N `items` rows sharing one `source_post_id` so the future "design deep tagger" can grade each image independently.

**Tech Stack:** Python 3.11+, httpx for Apify REST (no extra SDK), Anthropic SDK with vision content blocks, APScheduler 3.10+ for in-process scheduling, existing Supabase + Dropbox + Telegram dependencies. Tests: pytest, pytest-asyncio, pytest-mock, respx — already configured.

---

## Spec reference

This plan implements the **Session 4** entry of the [design spec](../specs/2026-05-05-personal-os-design.md) build sequence, plus four forward-compatibility additions agreed during planning (a `source_posts` table for normalized scrape metadata, Midjourney param extraction, a vision override for design+image items, and strict separation of user captions from scraped post text).

What this session does **not** ship (deferred per spec):

- Cron-scheduled `/process` autonomy → Session 5 (scheduler infrastructure built here will host it).
- Weekly digest + research-thread clustering → Session 6.
- Monthly rule consolidation pass → Session 6.
- Design deep tagger + Pinterest-style viewer → its own future session that consumes the data this session emits.

What this session **does** ship:

- Apify-backed X scraper with caching by URL.
- Normalized `source_posts` table; multi-image posts → N items joined to one source_post.
- Midjourney param extractor over scraped post text.
- Vision pass for design+image items (two-pass flow).
- APScheduler running inside FastAPI process, 06:30 and 21:00 summary jobs.
- Telegram daily-summary message with inline-keyboard triage entry point.
- `corrections` table + refile/correction handlers writing to it.

---

## Decisions baked into this plan (override before starting if you disagree)

| Decision | Choice | Why |
|---|---|---|
| Apify actor | `apidojo/tweet-scraper` | Established, well-maintained, pay-per-result (~$0.40/1k tweets). |
| Apify call mechanism | Direct httpx POST to `run-sync-get-dataset-items` endpoint | Avoids `apify-client` dep; matches existing pattern of using httpx everywhere. |
| Scrape cache policy | Reuse existing `source_posts` row matched by `source_url` | No re-scrape on duplicate captures; saves credits and avoids version skew. |
| Multi-image fan-out timing | At process time inside the processor | Capture stays fast (no Apify blocking the 👍 ack). |
| Multi-image filing | One Obsidian note per item, all in the same project folder, frontmatter `source_post_id` links them | Future deep tagger can grade each image independently. |
| Media storage location | **All inside the vault**: pre-classify staging at `<vault_root>/_inbox/<date>/<uuid>.ext`, post-classify destination at `<vault_root>/<project>/_attachments/<uuid>.ext`. Notes use `![[filename]]` wiki-link syntax. | Set in pre-Session-4 storage refactor (commit 96bca82). Obsidian resolves wiki-links anywhere in the vault — survives any future folder moves. |
| Vision model | Same Haiku 4.5 (`claude-haiku-4-5-20251001`) with image content blocks | One vendor, one cost line, same prompt-caching mechanics. |
| Vision flow | Two-pass: text-only classify → if `project=design` AND `type=image`, run vision pass that refines `tags`, `visual_subtype`, `summary` | Per rule (c). Free for non-design items; ~$0.003 extra per design image. |
| Skip-vision rule (non-design) | Still applies per spec line 280 (caption >8 chars + matches project keyword → no vision) | Cost discipline. |
| Midjourney params extracted | `sref`, `ar`, `style`, `v`, `niji`, `chaos`, `stylize`, `weird` | Core set; jsonb storage lets us add more without migration. |
| Scheduler | APScheduler 3.10+ in-process, started/stopped via FastAPI lifespan | Single process, single deploy, no systemd timer file. Session 5 reuses this. |
| Summary timezone | America/Los_Angeles (Ryan's local) | All cron times in spec are wall-clock, not UTC. |
| Summary content | Morning (06:30): yesterday's processed items grouped by project, today's open todos, count in needs_review with [Review] button. Evening (21:00): today's processed items + needs_review count + [Review] button | Matches spec's "tap-to-refile triage flow." |
| Triage UI | Inline keyboard via `InlineKeyboardMarkup`; callbacks routed by `callback_data` prefix | Standard Telegram Bot API; no third-party UI lib. |
| Triage state | Stateless — each callback encodes the item_id it operates on in `callback_data` | No conversation state in the bot; simpler reasoning. |
| Corrections schema | `(item_id, correction_type, original_value, corrected_value)` plus timestamps | Generic enough to log any kind of correction; Session 6 reads this. |
| `items.raw_text` invariant | Never overwritten by scraped content. Scraped post body lives in `source_posts.post_text`. | Per rule (d). |

If you want to swap any of these, edit this section before starting Task 1.

---

## File structure (what this plan creates/modifies)

**New files:**
- `bot/scraper.py` — Apify HTTP client + multi-image fan-out logic.
- `bot/midjourney.py` — regex extractor for MJ params from scraped text.
- `bot/vision.py` — vision classifier pass (image content block).
- `bot/scheduler.py` — APScheduler setup, jobs registered, FastAPI lifespan integration.
- `bot/summary.py` — daily summary text builder (queries `items` + `runs`).
- `bot/triage.py` — inline keyboard builders + callback handlers.
- `migrations/0002_source_posts_corrections.sql` — schema migration.

**Modified files:**
- `bot/db.py` — add source_post CRUD, corrections insert, summary queries.
- `bot/enrichment.py` — route x.com / twitter.com URLs through scraper.
- `bot/processor.py` — multi-image fan-out + two-pass vision flow.
- `bot/main.py` — register scheduler in FastAPI lifespan; route `callback_query` updates to triage.
- `bot/config.py` — add `apify_api_token` field.
- `.env.example` — add `APIFY_API_TOKEN`.
- `requirements.txt` — add `apscheduler==3.10.4`.

**New tests:** `tests/test_scraper.py`, `tests/test_midjourney.py`, `tests/test_vision.py`, `tests/test_scheduler.py`, `tests/test_summary.py`, `tests/test_triage.py`. Extended tests in `tests/test_db.py`, `tests/test_processor.py`, `tests/test_main.py`.

---

## Prerequisites (operator setup before Task 1)

- [ ] **P1: Create an Apify account.** Sign up at https://apify.com (free tier includes $5/mo of platform credits — plenty for projected volume).
- [ ] **P2: Get an Apify API token.** Console → Settings → Integrations → API tokens → Create new token, name `roscoe-robot`. Save as `APIFY_API_TOKEN` (starts with `apify_api_`).
- [ ] **P3: Subscribe to the `apidojo/tweet-scraper` actor.** Visit https://apify.com/apidojo/tweet-scraper → Try for free / Rent. No payment required; pay-per-result billing is consumption-based.
- [ ] **P4: Verify the actor responds.** From any shell with curl:
  ```
  curl -X POST "https://api.apify.com/v2/acts/apidojo~tweet-scraper/run-sync-get-dataset-items?token=$APIFY_API_TOKEN" \
    -H "Content-Type: application/json" \
    -d '{"startUrls":[{"url":"https://x.com/dennismark73450/status/2053184837524041822"}]}'
  ```
  Expected: JSON array with one object containing `text`, `author`, `media`, etc. If it returns an empty array, the URL may be deleted/private — try a different public tweet.
- [ ] **P5: Confirm Telegram chat timezone.** Verify the droplet is set to UTC (`timedatectl`); the scheduler will convert to `America/Los_Angeles` internally for the 06:30 / 21:00 wall-clock jobs.

---

# Phase A — Apify Scraper + Source Posts + Midjourney Extractor

## Task A1: Schema migration for `source_posts`, `items.source_post_id`, `corrections`

**Files:**
- Create: `migrations/0002_source_posts_corrections.sql`

- [ ] **Step 1: Write the migration**

```sql
-- 0002_source_posts_corrections.sql

CREATE TABLE source_posts (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at timestamptz NOT NULL DEFAULT now(),
    source text NOT NULL,                  -- 'x' for now; future: 'youtube', 'instagram'
    source_url text NOT NULL,
    post_text text,                        -- scraped body, NEVER from user input
    author_handle text,                    -- e.g. '@dennismark73450'
    author_name text,                      -- display name
    posted_at timestamptz,                 -- when the tweet was posted
    image_urls text[] DEFAULT '{}',        -- ordered list as returned by scraper
    midjourney_params jsonb,               -- {sref, ar, style, v, niji, chaos, stylize, weird}
    raw_scraper_response jsonb,            -- full Apify response, for debugging + future extraction
    UNIQUE (source, source_url)
);

CREATE INDEX idx_source_posts_source_url ON source_posts (source_url);

ALTER TABLE items
    ADD COLUMN source_post_id uuid REFERENCES source_posts(id) ON DELETE SET NULL;

CREATE INDEX idx_items_source_post_id ON items (source_post_id);

CREATE TABLE corrections (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at timestamptz NOT NULL DEFAULT now(),
    item_id uuid NOT NULL REFERENCES items(id) ON DELETE CASCADE,
    correction_type text NOT NULL,         -- 'project' | 'type' | 'tags' | 'destination' | 'discard'
    original_value jsonb,                  -- whatever the classifier produced
    corrected_value jsonb,                 -- whatever the operator chose
    note text                              -- optional free-text
);

CREATE INDEX idx_corrections_item_id ON corrections (item_id);
CREATE INDEX idx_corrections_created_at ON corrections (created_at DESC);
```

- [ ] **Step 2: Apply via Supabase MCP**

Use the Supabase MCP `apply_migration` tool (project_id `sqzbdkxbeotmywjdksmd`, name `0002_source_posts_corrections`) with the SQL above. Confirm both tables exist by running `SELECT table_name FROM information_schema.tables WHERE table_schema='public' AND table_name IN ('source_posts','corrections');` — expect 2 rows.

- [ ] **Step 3: Commit**

```
git add migrations/0002_source_posts_corrections.sql
git commit -m "feat(session-4): add source_posts + corrections tables and items.source_post_id"
```

---

## Task A2: Add `APIFY_API_TOKEN` to config + env example + requirements

**Files:**
- Modify: `.env.example`
- Modify: `bot/config.py`
- Modify: `requirements.txt`

- [ ] **Step 1: Add to `.env.example`** (append after the OpenAI key block):

```
# Apify (X/Twitter scraper) — Session 4
APIFY_API_TOKEN=apify_api_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
APIFY_TWEET_SCRAPER_ACTOR=apidojo~tweet-scraper
```

- [ ] **Step 2: Add field to `Config` dataclass in `bot/config.py`.** Find the `@dataclass` block and add two fields next to `openai_api_key`:

```python
    apify_api_token: str
    apify_tweet_scraper_actor: str
```

In `from_env`, add reads:

```python
        apify_api_token=os.environ["APIFY_API_TOKEN"],
        apify_tweet_scraper_actor=os.environ.get("APIFY_TWEET_SCRAPER_ACTOR", "apidojo~tweet-scraper"),
```

Add `APIFY_API_TOKEN` to the `required` list near the top of `from_env`.

- [ ] **Step 3: Pin APScheduler in `requirements.txt`**

```
APScheduler==3.10.4
```

- [ ] **Step 4: Install locally**

Run: `pip install -r requirements.txt`
Expected: Clean install, no resolution errors.

- [ ] **Step 5: Run tests to confirm nothing breaks**

Run: `pytest -q`
Expected: All 85 existing tests still pass.

- [ ] **Step 6: Commit**

```
git add .env.example bot/config.py requirements.txt
git commit -m "feat(session-4): add Apify config + APScheduler dep"
```

---

## Task A3: Midjourney param regex extractor

**Files:**
- Create: `bot/midjourney.py`
- Create: `tests/test_midjourney.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_midjourney.py
import pytest
from bot.midjourney import extract_params

def test_extract_params_returns_empty_when_no_flags():
    assert extract_params("just a regular sentence") == {}

def test_extract_params_extracts_sref():
    assert extract_params("a moody landscape --sref 1234567890") == {"sref": "1234567890"}

def test_extract_params_extracts_aspect_ratio():
    assert extract_params("portrait shot --ar 3:4") == {"ar": "3:4"}

def test_extract_params_extracts_style_keyword():
    assert extract_params("editorial --style raw") == {"style": "raw"}

def test_extract_params_extracts_version():
    assert extract_params("--v 6.1 something") == {"v": "6.1"}

def test_extract_params_extracts_niji():
    assert extract_params("--niji 6") == {"niji": "6"}

def test_extract_params_extracts_numeric_params():
    text = "scene --chaos 30 --stylize 250 --weird 100"
    assert extract_params(text) == {"chaos": "30", "stylize": "250", "weird": "100"}

def test_extract_params_handles_full_realistic_prompt():
    prompt = (
        "minimalist editorial homepage, warm beige and rust palette, "
        "newsprint typography --sref 4072830571 --ar 16:9 --style raw --v 6.1 --stylize 400"
    )
    assert extract_params(prompt) == {
        "sref": "4072830571",
        "ar": "16:9",
        "style": "raw",
        "v": "6.1",
        "stylize": "400",
    }

def test_extract_params_ignores_unknown_flags():
    assert extract_params("--foo bar --sref 123") == {"sref": "123"}

def test_extract_params_tolerates_double_dash_in_url():
    # ensure we don't false-positive on `--` inside URLs
    assert extract_params("https://example.com/--ar/path") == {}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_midjourney.py -v`
Expected: All tests FAIL with `ModuleNotFoundError: bot.midjourney`.

- [ ] **Step 3: Write the implementation**

```python
# bot/midjourney.py
"""Extract Midjourney-style flags from arbitrary text."""
import re

_PARAM_PATTERNS = {
    "sref": re.compile(r"(?:^|\s)--sref\s+(\d+)"),
    "ar": re.compile(r"(?:^|\s)--ar\s+(\d+:\d+)"),
    "style": re.compile(r"(?:^|\s)--style\s+(\S+)"),
    "v": re.compile(r"(?:^|\s)--v\s+(\d+(?:\.\d+)?)"),
    "niji": re.compile(r"(?:^|\s)--niji\s+(\d+)"),
    "chaos": re.compile(r"(?:^|\s)--chaos\s+(\d+)"),
    "stylize": re.compile(r"(?:^|\s)--stylize\s+(\d+)"),
    "weird": re.compile(r"(?:^|\s)--weird\s+(\d+)"),
}


def extract_params(text: str) -> dict[str, str]:
    """Return dict of recognized Midjourney params present in `text`.

    Only matches `--flag value` patterns preceded by whitespace or string start,
    so flags embedded in URLs (e.g. "--ar" inside a path segment) don't trigger.
    """
    if not text:
        return {}
    out: dict[str, str] = {}
    for name, pattern in _PARAM_PATTERNS.items():
        m = pattern.search(text)
        if m:
            out[name] = m.group(1)
    return out
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_midjourney.py -v`
Expected: All 9 tests PASS.

- [ ] **Step 5: Commit**

```
git add bot/midjourney.py tests/test_midjourney.py
git commit -m "feat(session-4): add Midjourney param regex extractor"
```

---

## Task A4: Apify scraper module

**Files:**
- Create: `bot/scraper.py`
- Create: `tests/test_scraper.py`

- [ ] **Step 1: Write the failing test using respx to mock Apify**

```python
# tests/test_scraper.py
import httpx
import pytest
import respx
from bot.scraper import fetch_tweet, ScrapeResult, ScraperError


APIFY_URL = "https://api.apify.com/v2/acts/apidojo~tweet-scraper/run-sync-get-dataset-items"


@respx.mock
def test_fetch_tweet_parses_single_image_response():
    respx.post(APIFY_URL).mock(return_value=httpx.Response(200, json=[{
        "url": "https://x.com/user/status/123",
        "text": "test post --sref 999 --ar 1:1",
        "author": {"userName": "user", "name": "Display Name"},
        "createdAt": "2026-05-10T12:00:00.000Z",
        "media": [
            {"type": "photo", "media_url_https": "https://pbs.twimg.com/media/A.jpg"}
        ],
    }]))
    result = fetch_tweet("https://x.com/user/status/123", token="t", actor="apidojo~tweet-scraper")
    assert isinstance(result, ScrapeResult)
    assert result.post_text == "test post --sref 999 --ar 1:1"
    assert result.author_handle == "@user"
    assert result.author_name == "Display Name"
    assert result.image_urls == ["https://pbs.twimg.com/media/A.jpg"]
    assert result.midjourney_params == {"sref": "999", "ar": "1:1"}


@respx.mock
def test_fetch_tweet_parses_multi_image_response_preserves_order():
    respx.post(APIFY_URL).mock(return_value=httpx.Response(200, json=[{
        "url": "https://x.com/user/status/123",
        "text": "three images",
        "author": {"userName": "user", "name": "U"},
        "createdAt": "2026-05-10T12:00:00.000Z",
        "media": [
            {"type": "photo", "media_url_https": "https://pbs.twimg.com/media/A.jpg"},
            {"type": "photo", "media_url_https": "https://pbs.twimg.com/media/B.jpg"},
            {"type": "photo", "media_url_https": "https://pbs.twimg.com/media/C.jpg"},
        ],
    }]))
    result = fetch_tweet("https://x.com/user/status/123", token="t", actor="apidojo~tweet-scraper")
    assert result.image_urls == [
        "https://pbs.twimg.com/media/A.jpg",
        "https://pbs.twimg.com/media/B.jpg",
        "https://pbs.twimg.com/media/C.jpg",
    ]


@respx.mock
def test_fetch_tweet_handles_empty_result_array():
    respx.post(APIFY_URL).mock(return_value=httpx.Response(200, json=[]))
    with pytest.raises(ScraperError, match="empty"):
        fetch_tweet("https://x.com/user/status/deleted", token="t", actor="apidojo~tweet-scraper")


@respx.mock
def test_fetch_tweet_raises_on_http_error():
    respx.post(APIFY_URL).mock(return_value=httpx.Response(429, text="rate limited"))
    with pytest.raises(ScraperError, match="429"):
        fetch_tweet("https://x.com/user/status/123", token="t", actor="apidojo~tweet-scraper")


@respx.mock
def test_fetch_tweet_skips_video_only_media():
    respx.post(APIFY_URL).mock(return_value=httpx.Response(200, json=[{
        "url": "https://x.com/user/status/123",
        "text": "video",
        "author": {"userName": "user", "name": "U"},
        "createdAt": "2026-05-10T12:00:00.000Z",
        "media": [{"type": "video", "media_url_https": "https://video.twimg.com/x.mp4"}],
    }]))
    result = fetch_tweet("https://x.com/user/status/123", token="t", actor="apidojo~tweet-scraper")
    assert result.image_urls == []  # only photos extracted
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_scraper.py -v`
Expected: All FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Write the implementation**

```python
# bot/scraper.py
"""Apify-backed X/Twitter scraper.

Returns normalized ScrapeResult with post text, author, image URLs, and
extracted Midjourney params. Caller is responsible for inserting/looking up
source_posts rows; this module is pure I/O + parsing.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import httpx

from bot.midjourney import extract_params


class ScraperError(RuntimeError):
    """Raised when Apify returns no usable data."""


@dataclass
class ScrapeResult:
    source_url: str
    post_text: str
    author_handle: str | None
    author_name: str | None
    posted_at: datetime | None
    image_urls: list[str] = field(default_factory=list)
    midjourney_params: dict[str, str] = field(default_factory=dict)
    raw_response: dict[str, Any] | None = None


def fetch_tweet(url: str, *, token: str, actor: str, timeout: float = 60.0) -> ScrapeResult:
    """Synchronously scrape a single X URL via Apify and return ScrapeResult.

    Raises ScraperError on HTTP error or empty result.
    """
    endpoint = f"https://api.apify.com/v2/acts/{actor}/run-sync-get-dataset-items"
    payload = {"startUrls": [{"url": url}], "maxItems": 1}
    params = {"token": token}
    with httpx.Client(timeout=timeout) as client:
        resp = client.post(endpoint, params=params, json=payload)
    if resp.status_code != 200:
        raise ScraperError(f"Apify HTTP {resp.status_code}: {resp.text[:200]}")
    data = resp.json()
    if not isinstance(data, list) or not data:
        raise ScraperError(f"Apify returned empty dataset for {url}")
    tweet = data[0]
    return _normalize(url, tweet)


def _normalize(source_url: str, tweet: dict[str, Any]) -> ScrapeResult:
    text = (tweet.get("text") or "").strip()
    author = tweet.get("author") or {}
    handle_raw = author.get("userName")
    author_handle = f"@{handle_raw}" if handle_raw else None
    posted_at = _parse_iso(tweet.get("createdAt"))
    media = tweet.get("media") or []
    image_urls = [
        m.get("media_url_https") for m in media
        if m.get("type") == "photo" and m.get("media_url_https")
    ]
    return ScrapeResult(
        source_url=source_url,
        post_text=text,
        author_handle=author_handle,
        author_name=author.get("name"),
        posted_at=posted_at,
        image_urls=image_urls,
        midjourney_params=extract_params(text),
        raw_response=tweet,
    )


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        # tolerate trailing Z
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_scraper.py -v`
Expected: All 5 tests PASS.

- [ ] **Step 5: Commit**

```
git add bot/scraper.py tests/test_scraper.py
git commit -m "feat(session-4): add Apify tweet scraper module"
```

---

## Task A5: Source-post DB helpers (insert + lookup by URL)

**Files:**
- Modify: `bot/db.py`
- Modify: `tests/test_db.py`

- [ ] **Step 1: Add failing tests for the new helpers**

Append to `tests/test_db.py`:

```python
def test_get_source_post_by_url_returns_none_when_missing(mock_supabase_client):
    from bot.db import get_source_post_by_url
    mock_supabase_client.table.return_value.select.return_value.eq.return_value.maybe_single.return_value.execute.return_value.data = None
    assert get_source_post_by_url(mock_supabase_client, "https://x.com/u/status/1") is None


def test_get_source_post_by_url_returns_row_when_present(mock_supabase_client):
    from bot.db import get_source_post_by_url
    row = {"id": "uuid-1", "source_url": "https://x.com/u/status/1"}
    mock_supabase_client.table.return_value.select.return_value.eq.return_value.maybe_single.return_value.execute.return_value.data = row
    assert get_source_post_by_url(mock_supabase_client, "https://x.com/u/status/1") == row


def test_insert_source_post_returns_inserted_id(mock_supabase_client):
    from bot.db import insert_source_post
    mock_supabase_client.table.return_value.insert.return_value.execute.return_value.data = [{"id": "new-uuid"}]
    out = insert_source_post(mock_supabase_client, source="x", source_url="https://x.com/u/s/1", post_text="hi", author_handle="@u", author_name="U", posted_at=None, image_urls=["a.jpg"], midjourney_params={"sref":"1"}, raw_response={})
    assert out == "new-uuid"


def test_insert_correction_writes_row(mock_supabase_client):
    from bot.db import insert_correction
    mock_supabase_client.table.return_value.insert.return_value.execute.return_value.data = [{"id": "c-uuid"}]
    out = insert_correction(mock_supabase_client, item_id="i-1", correction_type="project", original_value={"project":"personal"}, corrected_value={"project":"design"}, note=None)
    assert out == "c-uuid"
    mock_supabase_client.table.assert_called_with("corrections")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_db.py -v -k "source_post or correction"`
Expected: 4 FAIL with `ImportError`.

- [ ] **Step 3: Add the helpers to `bot/db.py`** (after the existing `fetch_recent_corrections` function or near other helpers):

```python
def get_source_post_by_url(supabase_client, source_url: str) -> dict | None:
    """Return existing source_post row matched by source_url, or None."""
    result = (
        supabase_client.table("source_posts")
        .select("*")
        .eq("source_url", source_url)
        .maybe_single()
        .execute()
    )
    return result.data


def insert_source_post(
    supabase_client,
    *,
    source: str,
    source_url: str,
    post_text: str,
    author_handle: str | None,
    author_name: str | None,
    posted_at,
    image_urls: list[str],
    midjourney_params: dict,
    raw_response: dict,
) -> str:
    """Insert a source_posts row and return its id."""
    payload = {
        "source": source,
        "source_url": source_url,
        "post_text": post_text,
        "author_handle": author_handle,
        "author_name": author_name,
        "posted_at": posted_at.isoformat() if posted_at else None,
        "image_urls": image_urls,
        "midjourney_params": midjourney_params,
        "raw_scraper_response": raw_response,
    }
    result = supabase_client.table("source_posts").insert(payload).execute()
    return result.data[0]["id"]


def insert_correction(
    supabase_client,
    *,
    item_id: str,
    correction_type: str,
    original_value: dict,
    corrected_value: dict,
    note: str | None,
) -> str:
    """Insert a corrections row and return its id."""
    payload = {
        "item_id": item_id,
        "correction_type": correction_type,
        "original_value": original_value,
        "corrected_value": corrected_value,
        "note": note,
    }
    result = supabase_client.table("corrections").insert(payload).execute()
    return result.data[0]["id"]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_db.py -v -k "source_post or correction"`
Expected: 4 PASS.

- [ ] **Step 5: Commit**

```
git add bot/db.py tests/test_db.py
git commit -m "feat(session-4): db helpers for source_posts + corrections"
```

---

## Task A6: Wire scraper into processor's multi-image fan-out

**Files:**
- Modify: `bot/processor.py`
- Modify: `tests/test_processor.py`

This is the most complex change in Phase A. The processor must:
1. Detect when an item's `raw_text` contains an X URL.
2. Look up existing source_post by URL (cache hit) or call scraper (cache miss, insert new row).
3. If multiple images, fan out into N items rows sharing the source_post_id.
4. Pass scraped post text into the classifier payload (without touching items.raw_text).

- [ ] **Step 1: Add failing tests**

Append to `tests/test_processor.py`:

```python
import re
from unittest.mock import MagicMock

X_URL_RE = re.compile(r"https?://(?:x|twitter)\.com/[^\s]+", re.I)


def test_handle_x_url_creates_source_post_when_cache_miss(mocker, mock_supabase_client):
    from bot.processor import handle_x_url
    from bot.scraper import ScrapeResult
    mocker.patch("bot.db.get_source_post_by_url", return_value=None)
    mock_fetch = mocker.patch(
        "bot.scraper.fetch_tweet",
        return_value=ScrapeResult(
            source_url="https://x.com/u/status/1",
            post_text="design --sref 999",
            author_handle="@u",
            author_name="U",
            posted_at=None,
            image_urls=["https://pbs.twimg.com/A.jpg"],
            midjourney_params={"sref": "999"},
            raw_response={},
        ),
    )
    mock_insert = mocker.patch("bot.db.insert_source_post", return_value="sp-uuid")
    out = handle_x_url(mock_supabase_client, "https://x.com/u/status/1", token="t", actor="a")
    assert out["source_post_id"] == "sp-uuid"
    assert out["image_urls"] == ["https://pbs.twimg.com/A.jpg"]
    assert out["post_text"] == "design --sref 999"
    mock_fetch.assert_called_once()
    mock_insert.assert_called_once()


def test_handle_x_url_reuses_cached_source_post_when_present(mocker, mock_supabase_client):
    from bot.processor import handle_x_url
    cached = {"id": "sp-cached", "image_urls": ["https://pbs.twimg.com/Z.jpg"], "post_text": "cached"}
    mocker.patch("bot.db.get_source_post_by_url", return_value=cached)
    mock_fetch = mocker.patch("bot.scraper.fetch_tweet")
    mock_insert = mocker.patch("bot.db.insert_source_post")
    out = handle_x_url(mock_supabase_client, "https://x.com/u/status/1", token="t", actor="a")
    assert out["source_post_id"] == "sp-cached"
    assert out["image_urls"] == ["https://pbs.twimg.com/Z.jpg"]
    mock_fetch.assert_not_called()
    mock_insert.assert_not_called()
```

(Plus parallel tests for the fan-out and classify path — added in Step 3.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_processor.py -v -k "x_url"`
Expected: 2 FAIL with `ImportError: cannot import name 'handle_x_url'`.

- [ ] **Step 3: Implement `handle_x_url` in `bot/processor.py`**

Add near the top of `bot/processor.py`:

```python
import re

from bot import db, scraper

X_URL_RE = re.compile(r"https?://(?:x|twitter)\.com/[^\s]+", re.I)


def extract_x_url(text: str) -> str | None:
    """Return the first X/Twitter URL in `text`, or None."""
    if not text:
        return None
    m = X_URL_RE.search(text)
    if not m:
        return None
    # strip trailing punctuation that's not part of the URL
    url = m.group(0).rstrip(".,)]")
    return url


def handle_x_url(supabase_client, url: str, *, token: str, actor: str) -> dict:
    """Return source_post info for the URL, scraping + inserting if not cached.

    Returns a dict with keys: source_post_id, image_urls, post_text, midjourney_params.
    """
    existing = db.get_source_post_by_url(supabase_client, url)
    if existing:
        return {
            "source_post_id": existing["id"],
            "image_urls": existing.get("image_urls") or [],
            "post_text": existing.get("post_text") or "",
            "midjourney_params": existing.get("midjourney_params") or {},
        }
    result = scraper.fetch_tweet(url, token=token, actor=actor)
    new_id = db.insert_source_post(
        supabase_client,
        source="x",
        source_url=result.source_url,
        post_text=result.post_text,
        author_handle=result.author_handle,
        author_name=result.author_name,
        posted_at=result.posted_at,
        image_urls=result.image_urls,
        midjourney_params=result.midjourney_params,
        raw_response=result.raw_response or {},
    )
    return {
        "source_post_id": new_id,
        "image_urls": result.image_urls,
        "post_text": result.post_text,
        "midjourney_params": result.midjourney_params,
    }
```

- [ ] **Step 4: Wire fan-out into `process_item`**

Modify `process_item` to, before enrichment, check for an X URL in `item.raw_text`:

```python
    # X-scrape pre-processing
    x_url = extract_x_url(item.get("raw_text", ""))
    scrape_info = None
    if x_url:
        try:
            scrape_info = handle_x_url(
                supabase_client, x_url,
                token=config.apify_api_token,
                actor=config.apify_tweet_scraper_actor,
            )
            # update this item with source_post_id + first image (if any)
            item["source_post_id"] = scrape_info["source_post_id"]
            if scrape_info["image_urls"]:
                # download first image to vault _inbox, attach. The processor's
                # existing _post_classify_dropbox_path step will move it into
                # the right project's _attachments folder after classification.
                first_img = scrape_info["image_urls"][0]
                item["media_dropbox_path"] = _download_image_to_dropbox(
                    dropbox_client, first_img, item["id"],
                    index=0, vault_root=vault_root, project="_inbox",
                )
                item["media_type"] = "image"
            # patch DB to persist
            supabase_client.table("items").update({
                "source_post_id": item["source_post_id"],
                "media_dropbox_path": item.get("media_dropbox_path"),
                "media_type": item.get("media_type"),
            }).eq("id", item["id"]).execute()
        except scraper.ScraperError as e:
            logger.warning("scrape failed for %s: %s; falling back to bare-URL", x_url, e)
            scrape_info = None
```

And the fan-out: after the first item is fully processed (classified + filed), if `scrape_info` had >1 image, create additional items rows synchronously inside this same batch run, append them to the list of items the batch is draining, and let the loop pick them up.

In `run_batch`, after fetching the initial pending list, define a per-iteration hook:

```python
def _fan_out_additional_items_from_scrape(supabase_client, original_item, scrape_info, dropbox_client) -> list[dict]:
    """Create N-1 additional items rows for images 2..N. Returns the new rows."""
    if not scrape_info or len(scrape_info["image_urls"]) <= 1:
        return []
    new_items = []
    for idx, img_url in enumerate(scrape_info["image_urls"][1:], start=1):
        media_path = _download_image_to_dropbox(
            dropbox_client, img_url, original_item["id"],
            index=idx, vault_root=vault_root, project="_inbox",
        )
        row = {
            "source": original_item["source"],
            "source_message_id": original_item["source_message_id"],
            "raw_text": original_item["raw_text"],
            "media_type": "image",
            "media_dropbox_path": media_path,
            "source_post_id": scrape_info["source_post_id"],
            "status": "pending",
        }
        result = supabase_client.table("items").insert(row).execute()
        new_items.append(result.data[0])
    return new_items
```

And call `_fan_out_additional_items_from_scrape` after the original item is processed; append the returned list to the batch's working queue.

- [ ] **Step 5: Implement `_download_image_to_dropbox` helper** in `bot/processor.py`:

```python
def _download_image_to_dropbox(
    dropbox_client,
    image_url: str,
    item_id: str,
    *,
    index: int,
    vault_root: str,
    project: str,
) -> str:
    """Download an image URL and upload to Dropbox inside the vault, returning the Dropbox path.

    Files land at <vault_root>/<project>/_attachments/<item_id>-<index>.jpg so Obsidian
    can resolve the wiki-link embed. Project defaults to '_inbox' until classification
    runs (caller may pass '_inbox' for pre-classification staging).
    """
    import httpx
    from dropbox.files import WriteMode
    with httpx.Client(timeout=30.0, follow_redirects=True) as client:
        resp = client.get(image_url)
        resp.raise_for_status()
        content = resp.content
    ext = ".jpg"  # X images are always jpg via media_url_https
    dropbox_path = f"{vault_root}/{project}/_attachments/{item_id}-{index}{ext}"
    dropbox_client.files_upload(f=content, path=dropbox_path, mode=WriteMode("overwrite"))
    return dropbox_path
```

Callers must pass `vault_root` (from `config.obsidian_vault_dropbox_path`) and a tentative `project` (use `"_inbox"` for pre-classification staging if project isn't known yet — files can be moved by the existing `_post_classify_dropbox_path` flow after classification).

- [ ] **Step 6: Run all processor tests**

Run: `pytest tests/test_processor.py -v`
Expected: All tests (existing + new) PASS.

- [ ] **Step 7: Commit**

```
git add bot/processor.py tests/test_processor.py
git commit -m "feat(session-4): wire Apify scraper into processor with multi-image fan-out"
```

---

## Task A7: Phase A smoke test on droplet

- [ ] **Step 1: Push commits and redeploy**

Run locally: `git push origin main`

Then SSH redeploy command (operator runs):
```
ssh root@64.23.170.115 'cd /opt/personal-os-v2 && git pull && systemctl restart personal-os-v2 && sleep 3 && systemctl status personal-os-v2 --no-pager | head -5'
```

- [ ] **Step 2: Sync `.env` with `APIFY_API_TOKEN` added**

Use the base64 PowerShell pattern from memory (`feedback_env_deploy_via_base64.md`). Verify:
```
ssh root@64.23.170.115 'grep ^APIFY_API_TOKEN /opt/personal-os-v2/.env'
```

- [ ] **Step 3: Capture a multi-image X post via Telegram**

Forward an X post with 2-3 images to the bot. Confirm 👍 ack.

- [ ] **Step 4: Run `/process` in Telegram**

Expected: bot replies `processed N items in Xs · $0.YZ · M needs review`. The N should equal the number of images in the scraped post (one item per image).

- [ ] **Step 5: Verify in Supabase MCP**

Query: `SELECT id, source_url, jsonb_array_length(to_jsonb(image_urls)) AS imgs, midjourney_params FROM source_posts ORDER BY created_at DESC LIMIT 5;`
Expected: One new row with `imgs` matching the tweet's image count.

Query: `SELECT id, source_post_id, media_type, status FROM items WHERE source_post_id IS NOT NULL ORDER BY created_at DESC LIMIT 10;`
Expected: N items sharing one source_post_id.

- [ ] **Step 6: Verify in Obsidian**

Open vault. Expect N new notes in the relevant project folder (probably `design/`), each with `source_post_id` in frontmatter.

# Phase B — Vision Two-Pass for Design Images

## Task B1: Vision classifier module

**Files:**
- Create: `bot/vision.py`
- Create: `tests/test_vision.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_vision.py
from unittest.mock import MagicMock
from bot.vision import refine_with_vision, VisionRefinement


def test_refine_with_vision_returns_refinement_from_response():
    client = MagicMock()
    client.messages.create.return_value = MagicMock(
        content=[MagicMock(text='{"visual_subtype":"hero","tags":["editorial","warm-palette"],"summary":"Editorial hero with warm beige palette"}')],
        usage=MagicMock(input_tokens=300, output_tokens=80, cache_creation_input_tokens=0, cache_read_input_tokens=0),
    )
    out = refine_with_vision(
        client,
        image_bytes=b"fake-jpg-bytes",
        text_context="design inspo — homepage hero",
        scraped_post_text="warm beige editorial --sref 123",
        prior_classification={"project": "design", "type": "image"},
    )
    assert isinstance(out, VisionRefinement)
    assert out.visual_subtype == "hero"
    assert out.tags == ["editorial", "warm-palette"]
    assert "warm beige" in out.summary
    assert out.cost_cents > 0


def test_refine_with_vision_strips_fences_in_response():
    client = MagicMock()
    client.messages.create.return_value = MagicMock(
        content=[MagicMock(text='```json\n{"visual_subtype":"nav","tags":[],"summary":"ok"}\n```')],
        usage=MagicMock(input_tokens=100, output_tokens=20, cache_creation_input_tokens=0, cache_read_input_tokens=0),
    )
    out = refine_with_vision(client, image_bytes=b"x", text_context="", scraped_post_text="", prior_classification={"project":"design","type":"image"})
    assert out.visual_subtype == "nav"


def test_refine_with_vision_returns_none_when_response_unparseable():
    client = MagicMock()
    client.messages.create.return_value = MagicMock(
        content=[MagicMock(text="I cannot see this image clearly.")],
        usage=MagicMock(input_tokens=100, output_tokens=20, cache_creation_input_tokens=0, cache_read_input_tokens=0),
    )
    out = refine_with_vision(client, image_bytes=b"x", text_context="", scraped_post_text="", prior_classification={"project":"design","type":"image"})
    assert out is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_vision.py -v`
Expected: All FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Write the implementation**

```python
# bot/vision.py
"""Second-pass vision classifier for design+image items.

Called only when the text-only classification returns project=design AND type=image.
Returns refined tags, visual_subtype, and summary, plus the API cost.
"""
from __future__ import annotations

import base64
import json
import math
from dataclasses import dataclass

from bot.llm import _extract_json  # reuse fence-stripping helper


@dataclass
class VisionRefinement:
    visual_subtype: str | None
    tags: list[str]
    summary: str
    cost_cents: int


VISION_SYSTEM_PROMPT = """You are refining a design-inspiration capture by looking at the image.

The prior text-only pass already determined this is project=design, type=image. Your job is to look at the image and produce:
- visual_subtype: one of [hero, nav, pricing, dashboard, typography, color-palette, branding, mobile, illustration, photography, layout, other]
- tags: list of 3-8 design-relevant tags (color palette, layout pattern, era, mood, etc.)
- summary: 1-2 sentence description of what makes this image notable as design inspiration

Return ONLY a single JSON object with those three keys. No prose, no markdown fences."""


def refine_with_vision(
    client,
    *,
    image_bytes: bytes,
    text_context: str,
    scraped_post_text: str,
    prior_classification: dict,
    model: str = "claude-haiku-4-5-20251001",
    max_tokens: int = 400,
) -> VisionRefinement | None:
    """Run a vision pass over the image and return refinement, or None if unparseable."""
    user_text_parts = []
    if text_context:
        user_text_parts.append(f"User caption: {text_context}")
    if scraped_post_text:
        user_text_parts.append(f"Source post text: {scraped_post_text}")
    user_text_parts.append(f"Prior classification: {json.dumps(prior_classification)}")
    user_text = "\n\n".join(user_text_parts)

    image_b64 = base64.b64encode(image_bytes).decode("ascii")

    response = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=VISION_SYSTEM_PROMPT,
        messages=[{
            "role": "user",
            "content": [
                {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": image_b64}},
                {"type": "text", "text": user_text},
            ],
        }],
    )
    raw = response.content[0].text
    usage = {
        "input_tokens": response.usage.input_tokens,
        "output_tokens": response.usage.output_tokens,
        "cache_creation_input_tokens": getattr(response.usage, "cache_creation_input_tokens", 0) or 0,
        "cache_read_input_tokens": getattr(response.usage, "cache_read_input_tokens", 0) or 0,
    }
    cost = _vision_cost_cents(usage)
    try:
        parsed = json.loads(_extract_json(raw))
    except (json.JSONDecodeError, ValueError):
        return None
    return VisionRefinement(
        visual_subtype=parsed.get("visual_subtype"),
        tags=parsed.get("tags") or [],
        summary=parsed.get("summary") or "",
        cost_cents=cost,
    )


def _vision_cost_cents(usage: dict) -> int:
    """Haiku 4.5 vision shares text pricing. Images count toward input tokens (already in usage)."""
    usd = (
        usage["input_tokens"] * 1.00
        + usage["output_tokens"] * 5.00
        + usage["cache_creation_input_tokens"] * 1.25
        + usage["cache_read_input_tokens"] * 0.10
    ) / 1_000_000
    return math.ceil(usd * 100)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_vision.py -v`
Expected: 3 PASS.

- [ ] **Step 5: Commit**

```
git add bot/vision.py tests/test_vision.py
git commit -m "feat(session-4): add vision refinement module"
```

---

## Task B2: Wire two-pass flow into processor

**Files:**
- Modify: `bot/processor.py`
- Modify: `tests/test_processor.py`

- [ ] **Step 1: Add failing test**

```python
def test_process_item_runs_vision_when_design_image(mocker, mock_supabase_client):
    from bot.processor import process_item
    from bot.vision import VisionRefinement

    mock_classify = mocker.patch("bot.llm.classify_item", return_value=({
        "project": "design", "type": "image", "tags": ["initial"], "summary": "initial",
        "confidence": 0.85,
    }, 3))
    mock_refine = mocker.patch("bot.vision.refine_with_vision", return_value=VisionRefinement(
        visual_subtype="hero", tags=["editorial","warm"], summary="refined", cost_cents=4,
    ))
    # ... arrange dropbox + anthropic clients + item dict
    # ... assert final item.tags == ["editorial","warm"], visual_subtype == "hero", summary == "refined"
    # ... assert mock_refine called once


def test_process_item_skips_vision_when_not_design(mocker, mock_supabase_client):
    from bot.processor import process_item
    mock_classify = mocker.patch("bot.llm.classify_item", return_value=({
        "project": "personal", "type": "todo", "tags": [], "summary": "do thing",
        "confidence": 0.9,
    }, 3))
    mock_refine = mocker.patch("bot.vision.refine_with_vision")
    # ... call process_item
    mock_refine.assert_not_called()
```

(Fill in the arrange section to match existing process_item test patterns.)

- [ ] **Step 2: Run tests to verify they fail**

Expected: Two new tests FAIL because the two-pass logic doesn't exist yet.

- [ ] **Step 3: Add two-pass logic to `process_item`**

After the text-only classification block (where `classification = llm.classify_item(...)`), add:

```python
    # Phase B — vision pass for design+image
    if (
        classification.get("project") == "design"
        and classification.get("type") == "image"
        and item.get("media_dropbox_path")
    ):
        try:
            image_bytes = _download_dropbox_file(dropbox_client, item["media_dropbox_path"])
            refinement = vision.refine_with_vision(
                anthropic_client,
                image_bytes=image_bytes,
                text_context=item.get("raw_text", ""),
                scraped_post_text=(scrape_info or {}).get("post_text", ""),
                prior_classification={"project": "design", "type": "image"},
            )
            if refinement is not None:
                classification["visual_subtype"] = refinement.visual_subtype
                classification["tags"] = refinement.tags or classification.get("tags") or []
                classification["summary"] = refinement.summary or classification["summary"]
                cost_cents += refinement.cost_cents
        except Exception as e:
            logger.warning("vision refinement failed for item %s: %s; using text-only classification", item.get("id"), e)
```

And add a helper:

```python
def _download_dropbox_file(dropbox_client, path: str) -> bytes:
    _, response = dropbox_client.files_download(path)
    return response.content
```

Plus `from bot import vision` at the top.

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_processor.py -v`
Expected: All pass.

- [ ] **Step 5: Commit**

```
git add bot/processor.py tests/test_processor.py
git commit -m "feat(session-4): two-pass vision flow for design+image items"
```

---

## Task B3: Phase B smoke test

- [ ] Push, redeploy, send a design-image capture (screenshot of a design with caption "design inspo — landing hero"), run `/process`.
- [ ] Verify in Supabase: item has `visual_subtype` set, tags include design-relevant terms, summary references visual elements.
- [ ] Verify cost_cents increased by ~3-5c per design image vs Session 3 baseline.

# Phase C — Daily Summary + Scheduler

## Task C1: Scheduler module

**Files:**
- Create: `bot/scheduler.py`
- Create: `tests/test_scheduler.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_scheduler.py
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from bot.scheduler import build_scheduler


def test_build_scheduler_returns_scheduler_with_two_jobs():
    sched = build_scheduler(
        morning_job=lambda: None,
        evening_job=lambda: None,
        timezone="America/Los_Angeles",
    )
    assert isinstance(sched, AsyncIOScheduler)
    jobs = sched.get_jobs()
    assert len(jobs) == 2
    job_ids = {j.id for j in jobs}
    assert job_ids == {"daily_summary_morning", "daily_summary_evening"}


def test_morning_job_is_scheduled_for_0630_la():
    sched = build_scheduler(lambda: None, lambda: None, "America/Los_Angeles")
    morning = sched.get_job("daily_summary_morning")
    trigger = morning.trigger
    assert trigger.fields[5].expressions[0].first == 6   # hour
    assert trigger.fields[6].expressions[0].first == 30  # minute


def test_evening_job_is_scheduled_for_2100_la():
    sched = build_scheduler(lambda: None, lambda: None, "America/Los_Angeles")
    evening = sched.get_job("daily_summary_evening")
    trigger = evening.trigger
    assert trigger.fields[5].expressions[0].first == 21
    assert trigger.fields[6].expressions[0].first == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_scheduler.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement**

```python
# bot/scheduler.py
"""APScheduler setup for daily-summary jobs.

Started/stopped by FastAPI lifespan in bot/main.py.
"""
from __future__ import annotations

from typing import Callable

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger


def build_scheduler(
    morning_job: Callable,
    evening_job: Callable,
    timezone: str = "America/Los_Angeles",
) -> AsyncIOScheduler:
    sched = AsyncIOScheduler(timezone=timezone)
    sched.add_job(
        morning_job,
        trigger=CronTrigger(hour=6, minute=30, timezone=timezone),
        id="daily_summary_morning",
        replace_existing=True,
    )
    sched.add_job(
        evening_job,
        trigger=CronTrigger(hour=21, minute=0, timezone=timezone),
        id="daily_summary_evening",
        replace_existing=True,
    )
    return sched
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_scheduler.py -v`
Expected: 3 PASS.

- [ ] **Step 5: Commit**

```
git add bot/scheduler.py tests/test_scheduler.py
git commit -m "feat(session-4): APScheduler with morning + evening jobs"
```

---

## Task C2: Summary content builder

**Files:**
- Create: `bot/summary.py`
- Create: `tests/test_summary.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_summary.py
from datetime import datetime, timezone
from bot.summary import build_morning_summary, build_evening_summary


def test_morning_summary_lists_yesterday_processed_grouped_by_project():
    items = [
        {"project": "acute", "type": "todo", "status": "processed", "summary": "follow up with vendor"},
        {"project": "acute", "type": "article", "status": "processed", "summary": "read piece on freight pricing"},
        {"project": "design", "type": "image", "status": "processed", "summary": "warm beige hero"},
        {"project": "design", "type": "image", "status": "needs_review", "summary": "unclear"},
    ]
    out = build_morning_summary(items, total_cost_cents=12)
    assert "acute (2)" in out
    assert "design (2)" in out  # both items, processed + needs_review
    assert "1 item needs review" in out or "1 needs review" in out
    assert "$0.12" in out


def test_evening_summary_lists_today_captures():
    items = [{"project": "personal", "type": "todo", "status": "processed", "summary": "call dentist"}]
    out = build_evening_summary(items, total_cost_cents=2)
    assert "personal" in out
    assert "$0.02" in out


def test_summary_handles_empty_list_gracefully():
    assert "nothing processed" in build_morning_summary([], total_cost_cents=0).lower()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_summary.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement**

```python
# bot/summary.py
"""Build daily-summary message text for Telegram delivery."""
from __future__ import annotations

from collections import defaultdict


def _format_cost(cents: int) -> str:
    return f"${cents / 100:.2f}"


def _group_by_project(items: list[dict]) -> dict[str, list[dict]]:
    by_project: dict[str, list[dict]] = defaultdict(list)
    for it in items:
        by_project[it.get("project") or "unknown"].append(it)
    return dict(by_project)


def build_morning_summary(items: list[dict], total_cost_cents: int) -> str:
    if not items:
        return "🌅 Morning brief — nothing processed overnight."
    lines = ["🌅 Morning brief"]
    by_project = _group_by_project(items)
    for project, project_items in sorted(by_project.items()):
        todos = sum(1 for i in project_items if i.get("type") == "todo")
        suffix = f" — {todos} todos" if todos else ""
        lines.append(f"  • {project} ({len(project_items)}){suffix}")
    needs_review = sum(1 for i in items if i.get("status") == "needs_review")
    lines.append("")
    lines.append(f"{len(items)} items processed · {_format_cost(total_cost_cents)}")
    if needs_review:
        lines.append(f"⚠️  {needs_review} item{'s' if needs_review != 1 else ''} needs review")
    return "\n".join(lines)


def build_evening_summary(items: list[dict], total_cost_cents: int) -> str:
    if not items:
        return "🌙 Evening brief — nothing captured today."
    lines = ["🌙 Evening brief"]
    by_project = _group_by_project(items)
    for project, project_items in sorted(by_project.items()):
        lines.append(f"  • {project} ({len(project_items)})")
    needs_review = sum(1 for i in items if i.get("status") == "needs_review")
    lines.append("")
    lines.append(f"{len(items)} items today · {_format_cost(total_cost_cents)}")
    if needs_review:
        lines.append(f"⚠️  {needs_review} need{'s' if needs_review == 1 else ''} review")
    return "\n".join(lines)
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_summary.py -v`
Expected: 3 PASS.

- [ ] **Step 5: Commit**

```
git add bot/summary.py tests/test_summary.py
git commit -m "feat(session-4): daily summary content builder"
```

---

## Task C3: Wire scheduler + summary jobs into FastAPI lifespan

**Files:**
- Modify: `bot/main.py`
- Modify: `bot/db.py` (add `fetch_items_for_summary` helper)

- [ ] **Step 1: Add `fetch_items_for_summary` to `bot/db.py`**

```python
def fetch_items_for_summary(supabase_client, *, since, until) -> list[dict]:
    """Return items with processed_at between since and until (UTC ISO strings)."""
    result = (
        supabase_client.table("items")
        .select("id,project,type,status,summary,processed_at")
        .gte("processed_at", since)
        .lt("processed_at", until)
        .order("processed_at")
        .execute()
    )
    return result.data or []
```

(Add a test in `tests/test_db.py` matching the pattern of existing fetch tests.)

- [ ] **Step 2: Wire scheduler in `bot/main.py` lifespan**

Find the existing `lifespan` async context manager (or `@app.on_event("startup")` block) and add:

```python
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
import asyncio
from zoneinfo import ZoneInfo

from bot.scheduler import build_scheduler
from bot.summary import build_morning_summary, build_evening_summary
from bot.db import fetch_items_for_summary

LA = ZoneInfo("America/Los_Angeles")


async def _send_morning_summary():
    # yesterday in LA local time → UTC bounds
    now_la = datetime.now(LA)
    start_la = (now_la - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    end_la = now_la.replace(hour=0, minute=0, second=0, microsecond=0)
    since_utc = start_la.astimezone(timezone.utc).isoformat()
    until_utc = end_la.astimezone(timezone.utc).isoformat()
    items = fetch_items_for_summary(supabase, since=since_utc, until=until_utc)
    total_cost = sum(it.get("api_cost_cents") or 0 for it in items)
    text = build_morning_summary(items, total_cost)
    needs_review_count = sum(1 for it in items if it.get("status") == "needs_review")
    keyboard = build_review_keyboard(needs_review_count) if needs_review_count else None
    await send_message(config.telegram_user_id, None, text, reply_markup=keyboard)


async def _send_evening_summary():
    now_la = datetime.now(LA)
    start_la = now_la.replace(hour=0, minute=0, second=0, microsecond=0)
    end_la = now_la
    since_utc = start_la.astimezone(timezone.utc).isoformat()
    until_utc = end_la.astimezone(timezone.utc).isoformat()
    items = fetch_items_for_summary(supabase, since=since_utc, until=until_utc)
    total_cost = sum(it.get("api_cost_cents") or 0 for it in items)
    text = build_evening_summary(items, total_cost)
    needs_review_count = sum(1 for it in items if it.get("status") == "needs_review")
    keyboard = build_review_keyboard(needs_review_count) if needs_review_count else None
    await send_message(config.telegram_user_id, None, text, reply_markup=keyboard)


@asynccontextmanager
async def lifespan(app):
    sched = build_scheduler(
        morning_job=lambda: asyncio.create_task(_send_morning_summary()),
        evening_job=lambda: asyncio.create_task(_send_evening_summary()),
    )
    sched.start()
    try:
        yield
    finally:
        sched.shutdown(wait=False)


app = FastAPI(lifespan=lifespan)
```

(`build_review_keyboard` is implemented in Phase D Task D1; for now, leave it referenced — the import will work after Phase D.)

- [ ] **Step 3: Run all tests**

Run: `pytest -q`
Expected: All pass.

- [ ] **Step 4: Commit**

```
git add bot/main.py bot/db.py tests/test_db.py
git commit -m "feat(session-4): wire APScheduler + daily summary jobs into FastAPI lifespan"
```

---

## Task C4: Phase C smoke test

- [ ] Push, redeploy, verify scheduler starts (check logs for APScheduler start messages).
- [ ] Force-trigger the morning job by SSHing in and running a one-liner with the same logic, OR wait until 06:30 LA local.
- [ ] Confirm Telegram receives the summary message.

# Phase D — Triage Flow + Corrections

## Task D1: Inline keyboard builders + send_message support

**Files:**
- Create: `bot/triage.py`
- Modify: `bot/main.py` (extend `send_message` to accept `reply_markup`)
- Create: `tests/test_triage.py`

- [ ] **Step 1: Write failing tests for keyboard builders**

```python
# tests/test_triage.py
from bot.triage import build_review_keyboard, build_item_action_keyboard, parse_callback_data


def test_review_keyboard_has_review_button_when_items_pending():
    kb = build_review_keyboard(needs_review_count=3)
    buttons = kb["inline_keyboard"][0]
    assert buttons[0]["text"] == "Review 3 items"
    assert buttons[0]["callback_data"] == "review:start"


def test_review_keyboard_returns_none_when_zero():
    assert build_review_keyboard(0) is None


def test_item_action_keyboard_has_correct_buttons():
    kb = build_item_action_keyboard("item-uuid-123")
    rows = kb["inline_keyboard"]
    flat = [b for row in rows for b in row]
    callbacks = [b["callback_data"] for b in flat]
    assert "keep:item-uuid-123" in callbacks
    assert "refile:item-uuid-123" in callbacks
    assert "todo:item-uuid-123" in callbacks
    assert "discard:item-uuid-123" in callbacks


def test_parse_callback_data_round_trip():
    assert parse_callback_data("refile:abc-123") == ("refile", "abc-123")
    assert parse_callback_data("review:start") == ("review", "start")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_triage.py -v`
Expected: All FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement keyboard builders**

```python
# bot/triage.py
"""Telegram inline-keyboard builders and callback handlers for the triage UI."""
from __future__ import annotations

PROJECTS = ["acute", "abp", "lake-arrowhead", "church", "claude-build", "design", "personal"]


def build_review_keyboard(needs_review_count: int) -> dict | None:
    if needs_review_count <= 0:
        return None
    label = f"Review {needs_review_count} item{'s' if needs_review_count != 1 else ''}"
    return {"inline_keyboard": [[{"text": label, "callback_data": "review:start"}]]}


def build_item_action_keyboard(item_id: str) -> dict:
    return {"inline_keyboard": [
        [
            {"text": "✅ Keep", "callback_data": f"keep:{item_id}"},
            {"text": "📂 Refile", "callback_data": f"refile:{item_id}"},
        ],
        [
            {"text": "📝 → Todo", "callback_data": f"todo:{item_id}"},
            {"text": "🗑 Discard", "callback_data": f"discard:{item_id}"},
        ],
    ]}


def build_project_picker_keyboard(item_id: str) -> dict:
    rows = []
    for i in range(0, len(PROJECTS), 2):
        row = [
            {"text": p, "callback_data": f"setproj:{item_id}:{p}"}
            for p in PROJECTS[i:i+2]
        ]
        rows.append(row)
    return {"inline_keyboard": rows}


def parse_callback_data(data: str) -> tuple[str, str]:
    """Split callback_data into (action, payload). Payload may contain colons."""
    action, _, payload = data.partition(":")
    return action, payload
```

- [ ] **Step 4: Update `send_message` in `bot/main.py` to accept `reply_markup`**

Find the existing `send_message` async helper and add `reply_markup=None` parameter; pass it into the Telegram API payload when not None:

```python
async def send_message(chat_id: int, reply_to: int | None, text: str, *, reply_markup: dict | None = None) -> None:
    payload = {"chat_id": chat_id, "text": text}
    if reply_to is not None:
        payload["reply_to_message_id"] = reply_to
    if reply_markup is not None:
        payload["reply_markup"] = reply_markup
    # ... existing httpx post
```

- [ ] **Step 5: Run tests**

Run: `pytest tests/test_triage.py -v`
Expected: 4 PASS.

- [ ] **Step 6: Commit**

```
git add bot/triage.py bot/main.py tests/test_triage.py
git commit -m "feat(session-4): inline keyboard builders for triage flow"
```

---

## Task D2: Callback query handler routing

**Files:**
- Modify: `bot/main.py`
- Modify: `tests/test_main.py`

- [ ] **Step 1: Write failing test for callback dispatch**

```python
def test_webhook_routes_callback_query_to_handler(test_client, mock_sb):
    payload = {
        "update_id": 1,
        "callback_query": {
            "id": "cb-1",
            "from": {"id": 12345, "username": "ryan"},
            "message": {"chat": {"id": 12345}, "message_id": 99},
            "data": "keep:item-uuid",
        },
    }
    resp = test_client.post("/telegram/webhook?secret=test", json=payload)
    assert resp.status_code == 200
```

- [ ] **Step 2: Run test to verify it fails or proves dispatch isn't wired**

- [ ] **Step 3: Extend webhook handler in `bot/main.py`**

After the existing `if "message" in update:` block, add:

```python
    if "callback_query" in update:
        cb = update["callback_query"]
        user_id = cb["from"]["id"]
        if user_id != config.telegram_user_id:
            return {"ok": True}  # silently ignore strangers
        from bot.triage import parse_callback_data
        action, payload = parse_callback_data(cb["data"])
        chat_id = cb["message"]["chat"]["id"]
        message_id = cb["message"]["message_id"]
        background_tasks.add_task(
            _handle_triage_callback, action=action, payload=payload,
            chat_id=chat_id, message_id=message_id, callback_id=cb["id"],
        )
        return {"ok": True}
```

- [ ] **Step 4: Implement `_handle_triage_callback`** in `bot/main.py`:

```python
async def _handle_triage_callback(*, action: str, payload: str, chat_id: int, message_id: int, callback_id: str) -> None:
    # Always answer the callback first so Telegram clears the spinner
    await _answer_callback_query(callback_id)
    if action == "review":
        await _start_review(chat_id, message_id)
    elif action == "keep":
        await _handle_keep(payload, chat_id, message_id)
    elif action == "refile":
        await _handle_refile_prompt(payload, chat_id, message_id)
    elif action == "setproj":
        item_id, _, new_project = payload.partition(":")
        await _handle_set_project(item_id, new_project, chat_id, message_id)
    elif action == "todo":
        await _handle_mark_todo(payload, chat_id, message_id)
    elif action == "discard":
        await _handle_discard(payload, chat_id, message_id)


async def _answer_callback_query(callback_id: str) -> None:
    import httpx
    url = f"https://api.telegram.org/bot{config.telegram_bot_token}/answerCallbackQuery"
    async with httpx.AsyncClient(timeout=5.0) as client:
        await client.post(url, json={"callback_query_id": callback_id})
```

- [ ] **Step 5: Run tests**

Run: `pytest tests/test_main.py -v`
Expected: New test PASSES.

- [ ] **Step 6: Commit**

```
git add bot/main.py tests/test_main.py
git commit -m "feat(session-4): route callback_query updates to triage handlers"
```

---

## Task D3: Review-start handler enumerates needs_review items

**Files:**
- Modify: `bot/main.py`
- Modify: `bot/db.py` (add `fetch_needs_review_items`)

- [ ] **Step 1: Add `fetch_needs_review_items` to `bot/db.py`** (with test in `tests/test_db.py`):

```python
def fetch_needs_review_items(supabase_client, limit: int = 20) -> list[dict]:
    result = (
        supabase_client.table("items")
        .select("id,raw_text,media_type,media_dropbox_path,project,type,tags,summary,source_post_id")
        .eq("status", "needs_review")
        .order("processed_at", desc=False)
        .limit(limit)
        .execute()
    )
    return result.data or []
```

- [ ] **Step 2: Implement `_start_review` in `bot/main.py`**

```python
async def _start_review(chat_id: int, message_id: int) -> None:
    from bot.db import fetch_needs_review_items
    from bot.triage import build_item_action_keyboard
    items = fetch_needs_review_items(supabase, limit=20)
    if not items:
        await send_message(chat_id, message_id, "Nothing needs review. ✨")
        return
    first = items[0]
    text = (
        f"Review 1 of {len(items)}\n\n"
        f"Project: {first.get('project') or '—'}\n"
        f"Type: {first.get('type') or '—'}\n"
        f"Summary: {first.get('summary') or '—'}\n\n"
        f"Original: {(first.get('raw_text') or '')[:300]}"
    )
    await send_message(chat_id, None, text, reply_markup=build_item_action_keyboard(first["id"]))
```

- [ ] **Step 3: Commit**

```
git add bot/main.py bot/db.py tests/test_db.py
git commit -m "feat(session-4): review-start handler sends first needs_review item"
```

---

## Task D4: Keep / Discard / MarkTodo handlers + corrections write

**Files:**
- Modify: `bot/main.py`

- [ ] **Step 1: Implement the three terminal handlers**

```python
async def _handle_keep(item_id: str, chat_id: int, message_id: int) -> None:
    """User confirmed the classification is fine. Move status to 'processed'."""
    item = _fetch_item(item_id)
    if not item:
        return
    supabase.table("items").update({"status": "processed"}).eq("id", item_id).execute()
    insert_correction(
        supabase, item_id=item_id, correction_type="keep",
        original_value={k: item.get(k) for k in ("project", "type", "tags")},
        corrected_value={k: item.get(k) for k in ("project", "type", "tags")},
        note=None,
    )
    await send_message(chat_id, message_id, "✅ Kept.")


async def _handle_discard(item_id: str, chat_id: int, message_id: int) -> None:
    item = _fetch_item(item_id)
    if not item:
        return
    supabase.table("items").update({"status": "discarded"}).eq("id", item_id).execute()
    insert_correction(
        supabase, item_id=item_id, correction_type="discard",
        original_value={k: item.get(k) for k in ("project", "type", "tags")},
        corrected_value={"status": "discarded"},
        note=None,
    )
    await send_message(chat_id, message_id, "🗑 Discarded.")


async def _handle_mark_todo(item_id: str, chat_id: int, message_id: int) -> None:
    item = _fetch_item(item_id)
    if not item:
        return
    supabase.table("items").update({"type": "todo", "status": "processed"}).eq("id", item_id).execute()
    insert_correction(
        supabase, item_id=item_id, correction_type="type",
        original_value={"type": item.get("type")},
        corrected_value={"type": "todo"},
        note=None,
    )
    await send_message(chat_id, message_id, "📝 Marked as todo. (Note: not yet filed to Todoist; manual add or wait for re-process.)")


def _fetch_item(item_id: str) -> dict | None:
    result = supabase.table("items").select("*").eq("id", item_id).maybe_single().execute()
    return result.data
```

- [ ] **Step 2: Add tests** (mock supabase, assert calls and message text).

- [ ] **Step 3: Run tests**

- [ ] **Step 4: Commit**

```
git add bot/main.py tests/test_main.py
git commit -m "feat(session-4): keep/discard/todo handlers write to corrections"
```

---

## Task D5: Refile handler with project picker

**Files:**
- Modify: `bot/main.py`

- [ ] **Step 1: Implement `_handle_refile_prompt` and `_handle_set_project`**

```python
async def _handle_refile_prompt(item_id: str, chat_id: int, message_id: int) -> None:
    from bot.triage import build_project_picker_keyboard
    await send_message(chat_id, message_id, "Pick the right project:", reply_markup=build_project_picker_keyboard(item_id))


async def _handle_set_project(item_id: str, new_project: str, chat_id: int, message_id: int) -> None:
    item = _fetch_item(item_id)
    if not item:
        return
    original_project = item.get("project")
    supabase.table("items").update({
        "project": new_project,
        "status": "processed",
    }).eq("id", item_id).execute()
    insert_correction(
        supabase, item_id=item_id, correction_type="project",
        original_value={"project": original_project},
        corrected_value={"project": new_project},
        note=None,
    )
    await send_message(chat_id, message_id, f"📂 Refiled to {new_project}.")
```

- [ ] **Step 2: Add tests, run, commit**

```
git add bot/main.py tests/test_main.py
git commit -m "feat(session-4): refile flow with project picker writes corrections"
```

---

# Phase E — Wrap-Up

## Task E1: Full smoke test on droplet

- [ ] **Step 1: Final push + redeploy**
- [ ] **Step 2: Send a variety of captures over a 30-min window:**
  - One bare X URL with multi-image design tweet
  - One screenshot with caption "design inspo — landing hero"
  - One text-only todo
  - One YouTube link
- [ ] **Step 3: Run `/process`** — confirm items + source_posts populated correctly, vision fired on design image.
- [ ] **Step 4: Wait for next 21:00 LA local (or temporarily reschedule for "in 2 minutes" via SSH-edited cron expression)** — confirm Telegram receives the evening summary with [Review] button.
- [ ] **Step 5: Tap [Review]** — confirm first needs_review item appears with action keyboard.
- [ ] **Step 6: Try each of the four buttons** on different items — confirm corrections rows are created.

## Task E2: Update CLAUDE.md + memory

- [ ] **Step 1: Move items from "Things NOT done yet" → "What's shipped"** in CLAUDE.md for the four Session 4 deliverables. Update "Next sessions" to drop Session 4 entirely and elevate Session 5.

- [ ] **Step 2: Save 2-3 new memory entries** for non-obvious things learned:
  - Apify actor name + endpoint pattern (reference)
  - APScheduler timezone gotchas if any surfaced (feedback)
  - Vision skip-override pattern (feedback)

- [ ] **Step 3: Commit**

```
git add CLAUDE.md
git commit -m "docs: Session 4 shipped — update CLAUDE.md, next is Session 5 (autonomy)"
git push origin main
```

## Task E3: Finishing-a-development-branch

- [ ] **REQUIRED SUB-SKILL:** Use `superpowers:finishing-a-development-branch` to verify all tests pass, present finalization options, and execute the chosen path.
