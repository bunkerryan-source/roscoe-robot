# Session 6b — Long-Video Tutorial Routing

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop the OOM crash loop. When a scraped X tweet contains a video longer than 60 seconds, route the item as a "tutorial" (project=`claude-build`, type=`tutorial`) without downloading the video. Save the tweet text + URL to Obsidian and create a Todoist task in the Claude Build project as a "watch this later" reminder.

**Architecture:** Add a `video_durations` map to `ScrapeResult` keyed by URL → duration_millis (Apify already provides `video_info.duration_millis`). In `process_item`'s Phase A, before the existing `_download_video_to_dropbox` dispatch, check duration: if > 60s, set a hardcoded classification (no Haiku call, no Dropbox download, no media path) and let the normal Obsidian + Todoist filers handle the rest. Extend the Todoist trigger from `type == 'todo'` to `type in ('todo', 'tutorial')`. The design-dashboard already filters on `type IN ('image', 'video')`, so tutorial items are naturally excluded.

**Tech Stack:** Python, httpx, Dropbox Python SDK, Supabase REST, pytest + respx.

**Scope boundary:**
- No Whisper transcription (deferred to a separate plan that will also fix the voice-memo bug).
- No HEAD/Content-Length fallback for videos missing `duration_millis` — that path is statistically rare (variants always carry duration in standard Twitter responses). Add `# TODO` comment and revisit only if it bites.
- No schema migration (verified: `items.type` has no CHECK constraint).
- No new env var. `TODOIST_PROJECT_CLAUDE_BUILD` is already required in [bot/config.py](../../../bot/config.py#L40).
- No design-dashboard code changes (its `type IN ('image','video')` filter excludes `tutorial` naturally).
- Threshold hardcoded at 60s (60_000 ms). Easy to make configurable later if it feels wrong.

**Prerequisite (already done by Ryan before this plan):**
- Poison item `05beffee-b1aa-4f80-92cd-8fc68011797e` is `status='failed'` in Supabase.
- Droplet `.env` has `DAILY_COST_CAP_CENTS=0` and the service has been restarted (autonomy paused).

---

## File Structure

**Modify:**
- `bot/scraper.py` — `ScrapeResult.video_durations` field, populated by `_normalize`
- `bot/processor.py` — `_is_long_video` helper, `_tutorial_classification` helper, Phase-A branching, Todoist trigger extension
- `tests/test_scraper.py` — duration-extraction tests
- `tests/test_processor.py` — `_is_long_video` test, long-video tutorial-path integration test, Todoist-on-tutorial test

**No changes:**
- `bot/filers.py` — `write_obsidian_note` already handles `media_dropbox_path=None`; tutorial items just skip the image embed.
- `bot/config.py` — no new env vars.
- `migrations/` — no schema changes.

**Out-of-repo:**
- (Optional) Flip the quarantined poison item back to `pending` after deploy so the new logic re-processes it as a tutorial.
- Re-enable autonomy on droplet (`DAILY_COST_CAP_CENTS=200`, restart).

---

## Task 1: Scraper carries video duration

**Files:**
- Modify: `bot/scraper.py:22-31` (dataclass), `bot/scraper.py:93-121` (`_normalize`)
- Modify: `tests/test_scraper.py`

- [ ] **Step 1: Write the failing test**

Append to [tests/test_scraper.py](../../../tests/test_scraper.py):

```python
@respx.mock
def test_fetch_tweet_records_video_duration_from_variants():
    respx.post(APIFY_URL).mock(return_value=httpx.Response(200, json=[{
        "url": "https://x.com/user/status/123",
        "text": "tutorial",
        "author": {"username": "user", "name": "U"},
        "createdAt": "2026-05-10T12:00:00.000Z",
        "media": [{
            "type": "video",
            "media_url_https": "https://pbs.twimg.com/amplify_video_thumb/x/img/thumb.jpg",
            "video_info": {
                "duration_millis": 600000,
                "variants": [
                    {"content_type": "video/mp4", "bitrate": 832000,
                     "url": "https://video.twimg.com/amplify_video/x/vid/720x720/hi.mp4"},
                ],
            },
        }],
    }]))
    result = fetch_tweet("https://x.com/user/status/123", token="t", actor="xquik~x-tweet-scraper")
    assert result.image_urls == ["https://video.twimg.com/amplify_video/x/vid/720x720/hi.mp4"]
    assert result.video_durations == {
        "https://video.twimg.com/amplify_video/x/vid/720x720/hi.mp4": 600000,
    }


@respx.mock
def test_fetch_tweet_video_durations_empty_when_no_videos():
    respx.post(APIFY_URL).mock(return_value=httpx.Response(200, json=[{
        "url": "https://x.com/user/status/123",
        "text": "two photos",
        "author": {"username": "user", "name": "U"},
        "createdAt": "2026-05-10T12:00:00.000Z",
        "media": [
            {"type": "photo", "media_url_https": "https://pbs.twimg.com/media/A.jpg"},
            {"type": "photo", "media_url_https": "https://pbs.twimg.com/media/B.jpg"},
        ],
    }]))
    result = fetch_tweet("https://x.com/user/status/123", token="t", actor="xquik~x-tweet-scraper")
    assert result.video_durations == {}
```

- [ ] **Step 2: Run and confirm both FAIL**

Run: `pytest tests/test_scraper.py::test_fetch_tweet_records_video_duration_from_variants tests/test_scraper.py::test_fetch_tweet_video_durations_empty_when_no_videos -v`

Expected: both FAIL with `AttributeError: 'ScrapeResult' object has no attribute 'video_durations'`.

- [ ] **Step 3: Add the `video_durations` field to `ScrapeResult`**

In [bot/scraper.py](../../../bot/scraper.py), replace the `ScrapeResult` dataclass (lines 22-31) with:

```python
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
    video_durations: dict[str, int] = field(default_factory=dict)
```

- [ ] **Step 4: Populate `video_durations` in `_normalize`**

In [bot/scraper.py](../../../bot/scraper.py), replace the `_normalize` function (currently lines 93-121) with:

```python
def _normalize(source_url: str, tweet: dict[str, Any]) -> ScrapeResult:
    text = (tweet.get("text") or "").strip()
    author = tweet.get("author") or {}
    handle_raw = author.get("username")
    author_handle = f"@{handle_raw}" if handle_raw else None
    posted_at = _parse_twitter_date(tweet.get("createdAt"))
    media = tweet.get("media") or []
    image_urls: list[str] = [
        m.get("media_url_https") for m in media
        if m.get("type") == "photo" and m.get("media_url_https")
    ]
    # Videos and animated_gif entries: append video URL after photos so the
    # existing multi-image fan-out invariant ("image_urls[1:] are all images")
    # holds for pure-image tweets and mixed tweets order photos-first.
    video_durations: dict[str, int] = {}
    for m in media:
        if m.get("type") in ("video", "animated_gif"):
            video_url = _extract_video_url(m)
            if video_url:
                image_urls.append(video_url)
                duration = (m.get("video_info") or {}).get("duration_millis")
                if isinstance(duration, (int, float)) and duration > 0:
                    video_durations[video_url] = int(duration)
    return ScrapeResult(
        source_url=source_url,
        post_text=text,
        author_handle=author_handle,
        author_name=author.get("name"),
        posted_at=posted_at,
        image_urls=image_urls,
        midjourney_params=extract_params(text),
        raw_response=tweet,
        video_durations=video_durations,
    )
```

- [ ] **Step 5: Run the scraper tests and confirm all PASS**

Run: `pytest tests/test_scraper.py -v`

Expected: all tests PASS, including the existing video-extraction tests from Session 6 and the two new duration tests.

- [ ] **Step 6: Commit**

```bash
git add bot/scraper.py tests/test_scraper.py
git commit -m "feat(session-6b): scraper records video duration_millis per URL"
```

---

## Task 2: `handle_x_url` propagates `video_durations`

**Files:**
- Modify: `bot/processor.py:62-98` (`handle_x_url`)
- Modify: `tests/test_processor.py`

`handle_x_url` is the bridge between the scraper and `process_item`. It must surface `video_durations` so the Phase-A branching can read it. Cached source_posts (the `existing` branch) don't have durations persisted to the DB; that's fine because the Phase-A download path only runs on FRESH scrapes (see [bot/processor.py:323](../../../bot/processor.py#L323) — cached items skip the download block entirely).

- [ ] **Step 1: Write the failing test**

Append to [tests/test_processor.py](../../../tests/test_processor.py):

```python
def test_handle_x_url_fresh_scrape_returns_video_durations(mocker):
    from bot.processor import handle_x_url
    from bot.scraper import ScrapeResult

    mock_supabase = MagicMock()
    mock_supabase.table.return_value.select.return_value.eq.return_value.limit.return_value.execute.return_value = MagicMock(data=[])
    mocker.patch("bot.processor.get_source_post_by_url", return_value=None)
    mocker.patch(
        "bot.processor.scraper.fetch_tweet",
        return_value=ScrapeResult(
            source_url="https://x.com/u/status/1",
            post_text="long tutorial",
            author_handle="@u",
            author_name="U",
            posted_at=None,
            image_urls=["https://video.twimg.com/clip.mp4"],
            video_durations={"https://video.twimg.com/clip.mp4": 600000},
        ),
    )
    mocker.patch("bot.processor.insert_source_post", return_value="sp-new")

    result = handle_x_url(
        mock_supabase, "https://x.com/u/status/1",
        token="t", actor="xquik~x-tweet-scraper",
    )

    assert result["source_post_id"] == "sp-new"
    assert result["image_urls"] == ["https://video.twimg.com/clip.mp4"]
    assert result["video_durations"] == {"https://video.twimg.com/clip.mp4": 600000}


def test_handle_x_url_cached_returns_empty_video_durations(mocker):
    from bot.processor import handle_x_url

    mocker.patch(
        "bot.processor.get_source_post_by_url",
        return_value={
            "id": "sp-cached",
            "image_urls": ["https://pbs.twimg.com/A.jpg"],
            "post_text": "cached",
            "midjourney_params": {},
        },
    )

    result = handle_x_url(
        MagicMock(), "https://x.com/u/status/1",
        token="t", actor="xquik~x-tweet-scraper",
    )

    assert result["source_post_id"] == "sp-cached"
    # Cached path: no durations available (we don't persist them). Defaults to {}.
    assert result["video_durations"] == {}
```

- [ ] **Step 2: Run and confirm both FAIL**

Run: `pytest tests/test_processor.py::test_handle_x_url_fresh_scrape_returns_video_durations tests/test_processor.py::test_handle_x_url_cached_returns_empty_video_durations -v`

Expected: both FAIL — `KeyError: 'video_durations'`.

- [ ] **Step 3: Modify `handle_x_url` to surface durations**

In [bot/processor.py](../../../bot/processor.py), replace the body of `handle_x_url` (lines 62-98) with:

```python
def handle_x_url(supabase_client, url: str, *, token: str, actor: str) -> dict:
    """Return source_post info for the URL, scraping + inserting if not cached.

    Returns a dict with keys:
      - source_post_id
      - image_urls (list)
      - post_text (str)
      - midjourney_params (dict)
      - video_durations (dict[str, int]) — URL → duration_millis. Empty for
        cached returns since durations are not persisted to source_posts.
    Raises scraper.ScraperError on scrape failure (caller decides fallback).
    """
    existing = get_source_post_by_url(supabase_client, url)
    if existing:
        return {
            "source_post_id": existing["id"],
            "image_urls": existing.get("image_urls") or [],
            "post_text": existing.get("post_text") or "",
            "midjourney_params": existing.get("midjourney_params") or {},
            "video_durations": {},
        }
    result = scraper.fetch_tweet(url, token=token, actor=actor)
    new_id = insert_source_post(
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
        "video_durations": result.video_durations,
    }
```

- [ ] **Step 4: Run and confirm both PASS**

Run: `pytest tests/test_processor.py::test_handle_x_url_fresh_scrape_returns_video_durations tests/test_processor.py::test_handle_x_url_cached_returns_empty_video_durations -v`

Expected: both PASS.

- [ ] **Step 5: Commit**

```bash
git add bot/processor.py tests/test_processor.py
git commit -m "feat(session-6b): handle_x_url surfaces video_durations dict"
```

---

## Task 3: `_is_long_video` helper

**Files:**
- Modify: `bot/processor.py`
- Modify: `tests/test_processor.py`

- [ ] **Step 1: Write the failing test**

Append to [tests/test_processor.py](../../../tests/test_processor.py):

```python
def test_is_long_video_returns_true_when_duration_exceeds_threshold():
    from bot.processor import _is_long_video
    durations = {"https://video.twimg.com/long.mp4": 90_000}  # 90 seconds
    assert _is_long_video("https://video.twimg.com/long.mp4", durations) is True


def test_is_long_video_returns_false_for_short_video():
    from bot.processor import _is_long_video
    durations = {"https://video.twimg.com/short.mp4": 30_000}  # 30 seconds
    assert _is_long_video("https://video.twimg.com/short.mp4", durations) is False


def test_is_long_video_returns_false_when_duration_unknown():
    from bot.processor import _is_long_video
    # No entry in durations dict — conservative default is False so we still
    # download. Standard Twitter `video_info.variants` responses always carry
    # duration_millis; the unknown-duration path is the rare media_url_https
    # direct-mp4 fallback. TODO: add HEAD fallback if this bites in production.
    assert _is_long_video("https://video.twimg.com/unknown.mp4", {}) is False


def test_is_long_video_returns_false_for_non_video_url():
    from bot.processor import _is_long_video
    assert _is_long_video("https://pbs.twimg.com/media/A.jpg", {}) is False


def test_is_long_video_threshold_is_exclusive_at_60_seconds():
    from bot.processor import _is_long_video
    # Exactly 60s = short (not long). 60s + 1ms = long.
    short_durations = {"https://video.twimg.com/exact.mp4": 60_000}
    long_durations = {"https://video.twimg.com/over.mp4": 60_001}
    assert _is_long_video("https://video.twimg.com/exact.mp4", short_durations) is False
    assert _is_long_video("https://video.twimg.com/over.mp4", long_durations) is True
```

- [ ] **Step 2: Run and confirm all FAIL**

Run: `pytest tests/test_processor.py -k _is_long_video -v`

Expected: all 5 FAIL — `ImportError: cannot import name '_is_long_video'`.

- [ ] **Step 3: Implement the helper**

In [bot/processor.py](../../../bot/processor.py), directly below the existing `_is_video_url` function (after line 49), add:

```python
LONG_VIDEO_THRESHOLD_MS = 60_000  # >1 minute → tutorial path, not design path


def _is_long_video(url: str | None, video_durations: dict[str, int]) -> bool:
    """Return True iff `url` is a known video and its duration exceeds the threshold.

    Used to route long X tweet videos away from the Dropbox download path
    (which OOM-kills the uvicorn process on 4K videos) and toward the
    Obsidian + Todoist tutorial path. If `url` is not in `video_durations`
    the duration is unknown — we conservatively return False so the existing
    download path runs. Standard Twitter responses always include
    `duration_millis` in `video_info.variants`, so the unknown-duration path
    is the rare media_url_https direct-mp4 fallback.
    TODO: add HEAD Content-Length fallback for the unknown-duration case if
    OOMs recur in production.
    """
    if not _is_video_url(url):
        return False
    duration_ms = video_durations.get(url)
    if duration_ms is None:
        return False
    return duration_ms > LONG_VIDEO_THRESHOLD_MS
```

- [ ] **Step 4: Run and confirm all PASS**

Run: `pytest tests/test_processor.py -k _is_long_video -v`

Expected: all 5 PASS.

- [ ] **Step 5: Commit**

```bash
git add bot/processor.py tests/test_processor.py
git commit -m "feat(session-6b): _is_long_video helper with 60s threshold"
```

---

## Task 4: `_tutorial_classification` helper

**Files:**
- Modify: `bot/processor.py`
- Modify: `tests/test_processor.py`

This builds the hardcoded classification dict that the long-video path uses in lieu of calling Haiku. Keeping it as a pure helper makes it easy to unit-test the shape without standing up the full pipeline.

- [ ] **Step 1: Write the failing test**

Append to [tests/test_processor.py](../../../tests/test_processor.py):

```python
def test_tutorial_classification_uses_tweet_text_as_summary():
    from bot.processor import _tutorial_classification

    result = _tutorial_classification(
        post_text="How to build a Next.js dashboard in 10 minutes",
        x_url="https://x.com/u/status/1",
        video_url="https://video.twimg.com/clip.mp4",
    )

    assert result["project"] == "claude-build"
    assert result["type"] == "tutorial"
    assert result["subdomain"] is None
    assert result["visual_subtype"] is None
    # confidence stays well above NEEDS_REVIEW_THRESHOLD (0.6) so the item
    # bypasses triage — there's nothing to review on a hardcoded route.
    assert result["confidence"] >= 0.95
    # Free — no Haiku call.
    assert result["_cost_cents"] == 0
    # Summary is a watch-cue derived from the tweet text.
    assert "Watch" in result["summary"]
    assert "Next.js dashboard" in result["summary"]


def test_tutorial_classification_truncates_long_tweet_text():
    from bot.processor import _tutorial_classification

    long_text = "x" * 500
    result = _tutorial_classification(
        post_text=long_text,
        x_url="https://x.com/u/status/1",
        video_url="https://video.twimg.com/clip.mp4",
    )

    # Summary must fit comfortably in a Todoist task title; cap at ~200 chars.
    assert len(result["summary"]) <= 200


def test_tutorial_classification_falls_back_when_tweet_text_empty():
    from bot.processor import _tutorial_classification

    result = _tutorial_classification(
        post_text="",
        x_url="https://x.com/u/status/1",
        video_url="https://video.twimg.com/clip.mp4",
    )

    # Fallback should reference the source URL so the task is still actionable.
    assert "https://x.com/u/status/1" in result["summary"]
    assert "Watch" in result["summary"]


def test_tutorial_classification_tags_include_tutorial():
    from bot.processor import _tutorial_classification

    result = _tutorial_classification(
        post_text="something",
        x_url="https://x.com/u/status/1",
        video_url="https://video.twimg.com/clip.mp4",
    )

    assert "tutorial" in result["tags"]
```

- [ ] **Step 2: Run and confirm all FAIL**

Run: `pytest tests/test_processor.py -k _tutorial_classification -v`

Expected: 4 FAIL — `ImportError: cannot import name '_tutorial_classification'`.

- [ ] **Step 3: Implement the helper**

In [bot/processor.py](../../../bot/processor.py), directly below `LONG_VIDEO_THRESHOLD_MS` and `_is_long_video`, add:

```python
_TUTORIAL_SUMMARY_MAX = 200


def _tutorial_classification(*, post_text: str, x_url: str, video_url: str) -> dict:
    """Build the hardcoded classification dict for a long-video tutorial item.

    Long-video X posts skip Haiku classification entirely. The classifier's
    job (pick project, pick type, write summary) is deterministic here:
    project is always claude-build, type is always tutorial, summary is a
    watch-cue derived from the tweet text.
    """
    text = (post_text or "").strip()
    if text:
        snippet = text[:_TUTORIAL_SUMMARY_MAX - len("Watch: ")]
        summary = f"Watch: {snippet}"
    else:
        summary = f"Watch tutorial at {x_url}"
    return {
        "project": "claude-build",
        "subdomain": None,
        "type": "tutorial",
        "tags": ["tutorial", "x-video"],
        "visual_subtype": None,
        "summary": summary[:_TUTORIAL_SUMMARY_MAX],
        "confidence": 0.95,
        "_cost_cents": 0,
        "_tutorial_video_url": video_url,  # for the Obsidian note body
    }
```

- [ ] **Step 4: Run and confirm all PASS**

Run: `pytest tests/test_processor.py -k _tutorial_classification -v`

Expected: all 4 PASS.

- [ ] **Step 5: Commit**

```bash
git add bot/processor.py tests/test_processor.py
git commit -m "feat(session-6b): _tutorial_classification builds hardcoded route"
```

---

## Task 5: Phase-A routes long videos to the tutorial path

**Files:**
- Modify: `bot/processor.py:283-345` (Phase A block + `out` initialization)
- Modify: `tests/test_processor.py`

The integration test asserts the end-to-end shape: a fresh X scrape returning a long video should NOT trigger `_download_video_to_dropbox`, should NOT call `classify_item`, and should write an Obsidian note in `claude-build/` plus a Todoist task in the claude-build project.

- [ ] **Step 1: Write the failing test**

Append to [tests/test_processor.py](../../../tests/test_processor.py):

```python
def test_process_item_routes_long_video_to_tutorial_path(mocker):
    item = {
        "id": "item-tut-1",
        "source": "telegram",
        "source_message_id": "100",
        "media_type": "link",
        "raw_text": "https://x.com/u/status/1\nYT tutorial on website building",
        "media_dropbox_path": None,
    }
    mocker.patch(
        "bot.processor.handle_x_url",
        return_value={
            "source_post_id": "sp-tut",
            "image_urls": ["https://video.twimg.com/long.mp4"],
            "post_text": "How to build a Next.js dashboard in 10 minutes",
            "midjourney_params": {},
            "video_durations": {"https://video.twimg.com/long.mp4": 600_000},  # 10 min
        },
    )
    video_download = mocker.patch("bot.processor._download_video_to_dropbox")
    image_download = mocker.patch("bot.processor._download_image_to_dropbox")
    classify = mocker.patch("bot.processor.classify_item")  # MUST NOT be called
    todoist = mocker.patch("bot.processor.create_todoist_task", return_value="todoist-id-99")
    obsidian = mocker.patch("bot.processor.write_obsidian_note", return_value="claude-build/2026-05-14-watch-next-js.md")
    move = mocker.patch("bot.processor.move_dropbox_media")  # MUST NOT be called (no media)

    result = process_item(
        item,
        anthropic_client=MagicMock(),
        dropbox_client=MagicMock(),
        openai_api_key="x",
        todoist_token="t",
        todoist_projects={"claude-build": "1000005"},
        vault_root="/personal-os",
        system_blocks=[{"type": "text", "text": "s"}],
        supabase_client=MagicMock(),
        apify_api_token="apify_token",
        apify_tweet_scraper_actor="xquik~x-tweet-scraper",
    )

    assert result["error"] is None
    assert result["status"] == "processed"
    # No Dropbox writes.
    video_download.assert_not_called()
    image_download.assert_not_called()
    move.assert_not_called()
    # No Haiku call.
    classify.assert_not_called()
    # Hardcoded classification was used.
    assert result["classification"]["project"] == "claude-build"
    assert result["classification"]["type"] == "tutorial"
    assert "Watch" in result["classification"]["summary"]
    # Zero API cost on the route.
    assert result["api_cost_cents"] == 0
    # Obsidian note written under claude-build.
    obsidian.assert_called_once()
    assert obsidian.call_args.kwargs["classification"]["project"] == "claude-build"
    assert obsidian.call_args.kwargs["media_dropbox_path"] is None
    # Todoist task created in claude-build project.
    todoist.assert_called_once()
    assert todoist.call_args.kwargs["project_id"] == "1000005"
    assert "Watch" in todoist.call_args.kwargs["content"]


def test_process_item_short_video_still_takes_download_path(mocker):
    # Regression guard: 30s videos must still hit the existing Session 6 download path.
    item = {
        "id": "item-shortvid-1",
        "source": "telegram",
        "source_message_id": "101",
        "media_type": "link",
        "raw_text": "https://x.com/u/status/2",
        "media_dropbox_path": None,
    }
    mocker.patch(
        "bot.processor.handle_x_url",
        return_value={
            "source_post_id": "sp-short",
            "image_urls": ["https://video.twimg.com/short.mp4"],
            "post_text": "quick clip",
            "midjourney_params": {},
            "video_durations": {"https://video.twimg.com/short.mp4": 30_000},  # 30 sec
        },
    )
    video_download = mocker.patch(
        "bot.processor._download_video_to_dropbox",
        return_value="/personal-os/_inbox/_attachments/item-shortvid-1.mp4",
    )
    mocker.patch(
        "bot.processor.classify_item",
        return_value=_fake_classify_response("design", "video", ["motion"], "short motion clip"),
    )
    mocker.patch("bot.processor.write_obsidian_note", return_value="design/x.md")
    mocker.patch("bot.processor.move_dropbox_media", side_effect=lambda dropbox_client, from_path, to_path: None)

    result = process_item(
        item,
        anthropic_client=MagicMock(),
        dropbox_client=MagicMock(),
        openai_api_key="x",
        todoist_token="t",
        todoist_projects={"design": "111", "claude-build": "222"},
        vault_root="/personal-os",
        system_blocks=[{"type": "text", "text": "s"}],
        supabase_client=MagicMock(),
        apify_api_token="apify_token",
        apify_tweet_scraper_actor="xquik~x-tweet-scraper",
    )

    assert result["error"] is None
    video_download.assert_called_once()
    assert item["media_type"] == "video"  # existing behavior preserved
```

- [ ] **Step 2: Run and confirm both FAIL**

Run: `pytest tests/test_processor.py::test_process_item_routes_long_video_to_tutorial_path tests/test_processor.py::test_process_item_short_video_still_takes_download_path -v`

Expected: the long-video test FAILS — `_download_video_to_dropbox` is called and `classify_item` is also called. The short-video test will fail because `handle_x_url`'s new `video_durations` key isn't yet read by Phase A — but it shouldn't break the short path, just confirming the test scaffolding works.

- [ ] **Step 3: Modify the Phase-A download block in `process_item`**

Open [bot/processor.py](../../../bot/processor.py). Locate the block at lines 323-341 (the `# Download first image only...` comment plus the `if scrape_info["image_urls"] and not item.get("media_dropbox_path"):` branch inside the fresh-scrape path). Replace lines 323-341 with:

```python
                    # Phase A media dispatch. Three branches, in priority order:
                    #   1. Long video (>60s) → tutorial path. Skip download
                    #      entirely; pre-set classification so the rest of
                    #      process_item writes Obsidian + Todoist normally.
                    #   2. Short video → existing Session 6 download path.
                    #   3. Image → existing image download path.
                    if scrape_info["image_urls"] and not item.get("media_dropbox_path"):
                        first_url = scrape_info["image_urls"][0]
                        video_durations = scrape_info.get("video_durations") or {}
                        if _is_long_video(first_url, video_durations):
                            out["classification"] = _tutorial_classification(
                                post_text=scrape_info.get("post_text") or "",
                                x_url=x_url,
                                video_url=first_url,
                            )
                            out["api_cost_cents"] = 0
                            # Keep media_type as 'link' so enrich_item's link
                            # branch runs (we still want OG fetch as a backup
                            # source of context if the tweet text was empty).
                            # No media path is set — the Obsidian writer
                            # already handles media_dropbox_path=None.
                        elif _is_video_url(first_url):
                            staged = _download_video_to_dropbox(
                                dropbox_client, first_url, item["id"],
                                vault_root=vault_root, project="_inbox",
                            )
                            item["media_dropbox_path"] = staged
                            item["media_type"] = "video"
                            out["media_dropbox_path"] = staged
                        else:
                            staged = _download_image_to_dropbox(
                                dropbox_client, first_url, item["id"],
                                index=0, vault_root=vault_root, project="_inbox",
                            )
                            item["media_dropbox_path"] = staged
                            item["media_type"] = "image"
                            out["media_dropbox_path"] = staged
```

- [ ] **Step 4: Skip `classify_item` when the tutorial path pre-set the classification**

Still in [bot/processor.py](../../../bot/processor.py), find the three `classify_item` lines (368-370). Replace ONLY those three lines (leave the preceding `if not payload.strip():` block at lines 362-366 unchanged) with:

```python
        if out["classification"] is not None:
            # Tutorial path (or any future hardcoded route) — classification
            # is already set; skip the Haiku call entirely.
            classification = out["classification"]
        else:
            classification = classify_item(anthropic_client, system_blocks, payload)
            out["classification"] = classification
            out["api_cost_cents"] = classification.get("_cost_cents", 0)
```

- [ ] **Step 5: Run both new tests and confirm they PASS**

Run: `pytest tests/test_processor.py::test_process_item_routes_long_video_to_tutorial_path tests/test_processor.py::test_process_item_short_video_still_takes_download_path -v`

Expected: both PASS.

- [ ] **Step 6: Commit**

```bash
git add bot/processor.py tests/test_processor.py
git commit -m "feat(session-6b): route long X videos to tutorial path, skip download"
```

---

## Task 6: Todoist creates a task for `type='tutorial'`

**Files:**
- Modify: `bot/processor.py:430` (Todoist trigger condition)
- Modify: `tests/test_processor.py`

The long-video integration test in Task 5 already asserts a Todoist task is created. That test passes only after this change — but it's worth a focused unit test that nails down the condition explicitly so a future refactor doesn't silently break it.

- [ ] **Step 1: Write the failing test**

Append to [tests/test_processor.py](../../../tests/test_processor.py):

```python
def test_process_item_creates_todoist_task_for_type_tutorial(mocker):
    # Minimal item with the tutorial classification pre-set so we isolate the
    # Todoist trigger condition. No X URL — no Phase-A scrape needed.
    item = {
        "id": "item-tut-todoist",
        "source": "telegram",
        "source_message_id": "200",
        "media_type": "text",
        "raw_text": "https://x.com/u/status/9 watch this later",
        "media_dropbox_path": None,
    }
    mocker.patch(
        "bot.processor.classify_item",
        return_value={
            "project": "claude-build",
            "subdomain": None,
            "type": "tutorial",
            "tags": ["tutorial"],
            "visual_subtype": None,
            "summary": "Watch: how to build X",
            "confidence": 0.95,
            "_cost_cents": 0,
        },
    )
    mocker.patch("bot.processor.write_obsidian_note", return_value="claude-build/x.md")
    todoist = mocker.patch("bot.processor.create_todoist_task", return_value="td-1")

    result = process_item(
        item,
        anthropic_client=MagicMock(),
        dropbox_client=MagicMock(),
        openai_api_key="x",
        todoist_token="t",
        todoist_projects={"claude-build": "1000005"},
        vault_root="/personal-os",
        system_blocks=[{"type": "text", "text": "s"}],
    )

    assert result["error"] is None
    assert result["todoist_task_id"] == "td-1"
    todoist.assert_called_once()
    assert todoist.call_args.kwargs["project_id"] == "1000005"
    assert todoist.call_args.kwargs["content"] == "Watch: how to build X"


def test_process_item_does_not_create_todoist_for_type_image(mocker):
    # Regression: existing types (image, idea, article, link, video) must NOT
    # trigger Todoist. Only todo and tutorial do.
    item = {
        "id": "item-img",
        "source": "telegram",
        "source_message_id": "201",
        "media_type": "image",
        "raw_text": "lake arrowhead kitchen tile",
        "media_dropbox_path": "/personal-os/_inbox/_attachments/item-img-0.jpg",
    }
    mocker.patch(
        "bot.processor.classify_item",
        return_value=_fake_classify_response("lake-arrowhead", "image", ["tile"], "kitchen tile"),
    )
    mocker.patch("bot.processor.write_obsidian_note", return_value="lake-arrowhead/x.md")
    mocker.patch("bot.processor.move_dropbox_media", side_effect=lambda dropbox_client, from_path, to_path: None)
    todoist = mocker.patch("bot.processor.create_todoist_task")

    process_item(
        item,
        anthropic_client=MagicMock(),
        dropbox_client=MagicMock(),
        openai_api_key="x",
        todoist_token="t",
        todoist_projects={"lake-arrowhead": "111"},
        vault_root="/personal-os",
        system_blocks=[{"type": "text", "text": "s"}],
    )

    todoist.assert_not_called()
```

- [ ] **Step 2: Run and confirm the tutorial test FAILS**

Run: `pytest tests/test_processor.py::test_process_item_creates_todoist_task_for_type_tutorial -v`

Expected: FAIL — Todoist isn't called because the current condition is `type == 'todo'`. The image test should PASS because the existing condition correctly excludes images.

- [ ] **Step 3: Extend the Todoist trigger**

In [bot/processor.py](../../../bot/processor.py), locate the block starting at line 430:

```python
        if classification.get("type") == "todo":
```

Replace that line with:

```python
        if classification.get("type") in ("todo", "tutorial"):
```

Leave the body unchanged.

- [ ] **Step 4: Run both tests and confirm they PASS**

Run: `pytest tests/test_processor.py::test_process_item_creates_todoist_task_for_type_tutorial tests/test_processor.py::test_process_item_does_not_create_todoist_for_type_image -v`

Expected: both PASS.

- [ ] **Step 5: Commit**

```bash
git add bot/processor.py tests/test_processor.py
git commit -m "feat(session-6b): Todoist trigger covers type=tutorial in addition to todo"
```

---

## Task 7: Obsidian note for tutorial items includes the video URL

**Files:**
- Modify: `bot/filers.py:48-84` (`write_obsidian_note`)
- Modify: `tests/test_filers.py`

`write_obsidian_note` already supports `media_dropbox_path=None` (the `![[...]]` embed only runs when `type == 'image'`). For tutorials, we want to include the video URL as a clickable line in the note body so Ryan can jump straight from Obsidian to the X video. The classification dict carries `_tutorial_video_url` from Task 4.

- [ ] **Step 1: Write the failing test**

Append to [tests/test_filers.py](../../../tests/test_filers.py) (mirror the existing pattern — `write_obsidian_note` tests use `dropbox_client.files_upload.call_args` to inspect the written content):

```python
def test_write_obsidian_note_includes_tutorial_video_link():
    from bot.filers import write_obsidian_note

    dropbox_client = MagicMock()
    classification = {
        "project": "claude-build",
        "type": "tutorial",
        "summary": "Watch: how to build X",
        "tags": ["tutorial"],
        "confidence": 0.95,
        "_tutorial_video_url": "https://video.twimg.com/clip.mp4",
    }

    path = write_obsidian_note(
        dropbox_client=dropbox_client,
        vault_root="/personal-os",
        item_id="tut-1",
        classification=classification,
        raw_text="https://x.com/u/status/1\nYT tutorial",
        media_dropbox_path=None,
    )

    assert path.startswith("claude-build/")
    written = dropbox_client.files_upload.call_args.kwargs["f"].decode("utf-8")
    # Video URL appears in the body so it's clickable in Obsidian.
    assert "https://video.twimg.com/clip.mp4" in written
    # Original raw text (with X URL) is preserved under "## Raw capture".
    assert "https://x.com/u/status/1" in written
    # No wiki-link embed since there's no media file in the vault.
    assert "![[" not in written


def test_write_obsidian_note_non_tutorial_omits_tutorial_video_link():
    from bot.filers import write_obsidian_note

    dropbox_client = MagicMock()
    classification = {
        "project": "design",
        "type": "image",
        "summary": "kitchen tile",
        "tags": ["tile"],
        "confidence": 0.9,
        # No _tutorial_video_url for image items.
    }

    write_obsidian_note(
        dropbox_client=dropbox_client,
        vault_root="/personal-os",
        item_id="img-1",
        classification=classification,
        raw_text="kitchen tile",
        media_dropbox_path="/personal-os/design/_attachments/img-1.jpg",
    )

    written = dropbox_client.files_upload.call_args.kwargs["f"].decode("utf-8")
    # Wiki-link embed for the image still works.
    assert "![[img-1.jpg]]" in written
```

- [ ] **Step 2: Run the tests and confirm the tutorial test FAILS**

Run: `pytest tests/test_filers.py -k tutorial -v`

Expected: tutorial-link test FAILS (the video URL is not written). The image test PASSES (existing behavior unchanged).

- [ ] **Step 3: Modify `write_obsidian_note` to include the tutorial link**

In [bot/filers.py](../../../bot/filers.py), replace the body-construction block in `write_obsidian_note` (lines 69-79, starting at `frontmatter = _format_frontmatter(...)` through `note = "\n".join(body_parts).encode("utf-8")`) with:

```python
    frontmatter = _format_frontmatter(item_id, classification, media_dropbox_path)
    body_parts = [frontmatter, "", summary]
    if raw_text and raw_text != summary:
        body_parts += ["", "## Raw capture", "", raw_text]
    if media_dropbox_path and classification.get("type") == "image":
        # Use Obsidian wiki-link syntax so the embed resolves to the file
        # anywhere inside the vault (e.g., the project's _attachments/ folder).
        # Filenames are UUIDs so basename collisions are not a concern.
        filename = media_dropbox_path.rsplit("/", 1)[-1]
        body_parts += ["", f"![[{filename}]]"]
    if classification.get("type") == "tutorial" and classification.get("_tutorial_video_url"):
        # Direct link to the X-hosted mp4 so the note is one click from watch.
        body_parts += ["", "## Video", "", classification["_tutorial_video_url"]]
    note = "\n".join(body_parts).encode("utf-8")
```

- [ ] **Step 4: Run and confirm both PASS**

Run: `pytest tests/test_filers.py -k tutorial -v`

Expected: both PASS.

- [ ] **Step 5: Run the full filer test file to confirm no regressions**

Run: `pytest tests/test_filers.py -v`

Expected: all tests PASS.

- [ ] **Step 6: Commit**

```bash
git add bot/filers.py tests/test_filers.py
git commit -m "feat(session-6b): Obsidian note includes tutorial video URL"
```

---

## Task 8: Full-suite smoke run

**Files:** none (verification only)

- [ ] **Step 1: Run every test**

Run: `pytest -q`

Expected: all tests PASS. Prior to this plan the count was ~217 (Session 6 added 6, baseline 211). This plan adds ~14 tests so expect ~231 passing. If anything regresses outside of what this plan touches, stop and diagnose before continuing — most likely candidate is a test that constructs `ScrapeResult` directly without the new `video_durations` field (the field has a default, so this should be safe, but verify).

---

## Task 9: PR

**Files:** none (git only)

- [ ] **Step 1: Push the branch and open a PR**

```bash
git push -u origin <your-branch-name>
```

Then:

```bash
gh pr create --title "feat(session-6b): long-video tutorial routing" --body "$(cat <<'EOF'
## Summary

- Stops the OOM crash loop on long X-tweet videos (root cause: 4K mp4 loaded into memory before Dropbox upload).
- Long videos (>60s per Apify's `video_info.duration_millis`) now route to a "tutorial" path: project=claude-build, type=tutorial, no Dropbox download, no Haiku call.
- Obsidian note + Todoist task in the Claude Build project replace the design-folder download.
- Short videos (≤60s) continue through the existing Session 6 download path.
- Unknown-duration videos default to the download path (rare media_url_https direct-mp4 case); a TODO comment marks it for a HEAD-fallback follow-up if it bites in production.

## What this does NOT do

- Whisper transcription / summarization — deferred to a separate plan that will also fix the voice-memo bug (voice items currently land in Supabase with `raw_text=null`).
- Schema changes — `items.type` has no CHECK constraint, so `type='tutorial'` Just Works.
- Design-dashboard changes — its `type IN ('image','video')` filter naturally excludes tutorials.

## Test plan

- [ ] All ~231 tests pass locally (`pytest -q`).
- [ ] Smoke test on droplet: re-queue the quarantined poison item, run `/process`, verify (a) no OOM, (b) item lands in `claude-build/` in Obsidian, (c) Todoist task created in Claude Build, (d) no Dropbox download in `_attachments/`.
- [ ] Re-enable autonomy (`DAILY_COST_CAP_CENTS=200`, restart), watch next cron, confirm clean run.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 2: Wait for Ryan to merge**

Do not merge directly; Ryan reviews and merges all PRs to `main`.

---

## Task 10: Deploy to droplet after merge

**Files:** none (operational)

- [ ] **Step 1: Standard redeploy**

Ryan runs:

```powershell
ssh root@64.23.170.115 'cd /opt/personal-os-v2 && git pull && systemctl restart personal-os-v2 && sleep 3 && systemctl status personal-os-v2 --no-pager | head -10'
```

Expected: `Active: active (running)`. APScheduler line shows the three cron jobs registered with their next-fire times.

- [ ] **Step 2: Tail the journal for clean startup**

```powershell
ssh root@64.23.170.115 'journalctl -u personal-os-v2 -n 50 --no-pager'
```

Expected: no tracebacks, no OOM events.

---

## Task 11: Re-queue the quarantined poison item and smoke test

**Files:** none (live verification)

The item `05beffee-b1aa-4f80-92cd-8fc68011797e` is the same viktoroddy 4K tutorial that caused the original OOM. Flipping it back to `pending` is a clean live smoke test for the new tutorial path. Cost cap is still 0 at this point so we use `/process` (which bypasses the cap) to drive a controlled drain.

- [ ] **Step 1: Restore the poison item to pending**

Via Supabase MCP (project `sqzbdkxbeotmywjdksmd`):

```sql
UPDATE items
SET status = 'pending',
    error = NULL
WHERE id = '05beffee-b1aa-4f80-92cd-8fc68011797e';
```

Expected: 1 row updated.

- [ ] **Step 2: Trigger `/process` from Telegram**

In Telegram, send `/process`. Wait for the final `processed N · $X.XX` reply (no `needs review` for these items — tutorial classification has confidence 0.95).

- [ ] **Step 3: Verify in Supabase**

```sql
SELECT id, project, type, media_type, media_dropbox_path, todoist_task_id, summary, obsidian_path
FROM items
WHERE id = '05beffee-b1aa-4f80-92cd-8fc68011797e';
```

Expected:
- `project = 'claude-build'`
- `type = 'tutorial'`
- `media_dropbox_path` is NULL (we did NOT download)
- `todoist_task_id` is populated
- `obsidian_path` starts with `claude-build/`
- `summary` starts with "Watch:"

- [ ] **Step 4: Verify in Obsidian**

Open the Obsidian vault (`Dropbox (Personal)/Apps/roscoe-robot/personal-os/`). Navigate to `claude-build/`. Find the new note (filename includes today's date + a slug). Confirm:
- Frontmatter says `type: tutorial` and `project: claude-build`.
- Body has a `## Video` section with the `https://video.twimg.com/...` URL.
- Body has the original tweet URL under `## Raw capture`.
- No broken `![[...]]` embed.

- [ ] **Step 5: Verify in Todoist**

Open Todoist → Claude Build project. Confirm a new task exists with content `Watch: ...` and the X URL in its description.

- [ ] **Step 6: Verify no Dropbox download happened**

```powershell
ssh root@64.23.170.115 'journalctl -u personal-os-v2 -n 200 --no-pager | grep -E "video.twimg.com|OOM|killed"'
```

Expected: zero matches for `video.twimg.com` against this item. Zero `OOM` / `killed`. (There may be a `video.twimg.com` line from the OLD scrape cached in source_posts — that's fine, what matters is no GET against the actual video file during this run.)

- [ ] **Step 7: Process the remaining 10 pending X-URL items**

If `/process` in Step 2 only handled 1 item (because of the 50-item limit per run), trigger `/process` again to drain the rest of the queue. Any with embedded videos >60s will land as tutorials. Any pure-image tweets land in `design/` as before. Watch for OOMs.

---

## Task 12: Re-enable autonomy

**Files:** none (operational)

- [ ] **Step 1: Flip the cap back to 200¢**

```powershell
ssh root@64.23.170.115 'sed -i "s/^DAILY_COST_CAP_CENTS=.*/DAILY_COST_CAP_CENTS=200/" /opt/personal-os-v2/.env && grep DAILY_COST_CAP_CENTS /opt/personal-os-v2/.env'
```

Expected: prints `DAILY_COST_CAP_CENTS=200`.

- [ ] **Step 2: Restart**

```powershell
ssh root@64.23.170.115 'systemctl restart personal-os-v2 && sleep 3 && systemctl status personal-os-v2 --no-pager | head -10'
```

Expected: `Active: active (running)`.

- [ ] **Step 3: Wait for the next scheduled cron and verify clean run**

Watch the journal at the next cron fire time (06:30 / 12:00 / 21:00 LA, whichever is closest). Expected: APScheduler "executed successfully" line. No OOM. A new row in `runs` with `trigger='scheduled-...'`.

```powershell
ssh root@64.23.170.115 'journalctl -u personal-os-v2 -n 100 --no-pager | tail -30'
```

- [ ] **Step 4: Update CLAUDE.md current-state pointer**

Open [CLAUDE.md](../../../CLAUDE.md). Under "Current state — 2026-05-12", add a new dated entry:

```
## Current state — 2026-05-14

**Session 6b (long-video tutorial routing) shipped.** The processor now branches Phase A on `video_info.duration_millis`:
- Long videos (>60s) bypass Dropbox download entirely. Item gets `type='tutorial'`, `project='claude-build'`, hardcoded classification (no Haiku call, $0 cost), Obsidian note in `claude-build/`, Todoist task in the Claude Build project.
- Short videos (≤60s) continue through the existing Session 6 download path.

Fixes the OOM crash loop caused by 4K MP4s being loaded entirely into memory before the Dropbox upload. Whisper transcription / voice-memo bug fix is a separate next plan.
```

Update the "Now: validation week #3 — let cron live." line to reflect that Session 6b just shipped and we're back to letting autonomy run.

- [ ] **Step 5: Commit the docs update**

```bash
git add CLAUDE.md
git commit -m "docs: update current-state pointer for session-6b ship"
git push
```

---

## Out-of-scope (do not implement in this session)

- Whisper transcription for long-video tutorials (would replace the tweet-text-as-summary with a real audio summary, ~10¢ per 10-min video).
- Whisper bug fix for voice memos (`raw_text=null` on `media_type='voice'` items — separate plan).
- HEAD/Content-Length fallback for missing-duration videos (defer until a real OOM recurs on a non-variant video).
- Backfill of historical X-URL items that may contain long videos (Ryan can manually re-queue any he cares about).
- Multi-video fan-out for tweets containing >1 video (Session 6 plan already declares this out of scope; tutorials don't change that).
- Configurable threshold via env var (60s hardcoded for v1; revisit if it feels wrong).
