# Session 6 — Video Storage for Design Dashboard

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `roscoe-robot` populate `items.media_dropbox_path` for `type='video'` items captured from X URLs, so the sibling `design-dashboard` project can render them.

**Architecture:** The Apify scraper already returns the full media array but drops anything that isn't `type='photo'`. We extend `_normalize()` to also extract video URLs (preferring the highest-bitrate mp4 variant from `video_info.variants`, falling back to a direct `media_url_https` if it ends in a video extension). Videos are appended to the existing `ScrapeResult.image_urls` list — preserving zero-migration scope per the spec. The processor's Phase-A media-download step inspects the first URL's extension and dispatches to either `_download_image_to_dropbox` (existing) or a new `_download_video_to_dropbox` helper with a longer timeout and `.mp4` extension. The multi-image fan-out helper skips non-image URLs to keep its `_download_image_to_dropbox` callsite valid. Telegram-direct video sends already work end-to-end through the intake path and are verified, not modified.

**Tech Stack:** Python, httpx, Dropbox Python SDK, Supabase REST, pytest + respx.

**Scope boundary:** No schema changes. No `poster_dropbox_path` column. No ffmpeg / first-frame extraction. No new `runs.trigger` values. No multi-video fan-out (rare in practice; single-video tweets are the actual use case).

**Cross-project handoff:** When this lands, the design-dashboard side just flips one `.eq('type', 'image')` → `.in('type', ['image', 'video'])` and branches a `<video>` element. The spec lives at [docs/superpowers/specs/2026-05-12-ask-roscoe-video-support.md](../specs/2026-05-12-ask-roscoe-video-support.md).

---

## File Structure

**Modify:**
- `bot/scraper.py` — extend `_normalize()` to extract video URLs; add `_extract_video_url()` helper
- `bot/processor.py` — add `_is_video_url()` + `_download_video_to_dropbox()` helpers; branch Phase-A download dispatch; make `_fan_out_additional_items_from_scrape` skip non-image URLs
- `tests/test_scraper.py` — flip the existing skip-video test; add variant-selection + mp4-only-via-media_url_https tests
- `tests/test_processor.py` — add video-download tests (mocking the Apify response + Dropbox client)
- `CLAUDE.md` — bump session-6 pointer to session-7 once the digest plan is renamed

**Rename:**
- `docs/superpowers/plans/2026-05-12-session-6-weekly-digest-rules.md` → `docs/superpowers/plans/2026-05-12-session-7-weekly-digest-rules.md`

**Out-of-repo:**
- One Supabase update statement to discard the orphaned `2e38f4d8-…` row (run via Supabase MCP)
- Droplet redeploy via standard runbook

---

## Task 1: Flip the scraper's "skip video" test to assert extraction

**Files:**
- Modify: `tests/test_scraper.py:67-77`

- [ ] **Step 1: Replace the existing skip-video test with an inclusion test**

Open [tests/test_scraper.py](../../../tests/test_scraper.py) and replace `test_fetch_tweet_skips_video_only_media` (lines 66-76) with:

```python
@respx.mock
def test_fetch_tweet_extracts_video_url_from_video_info_variants():
    respx.post(APIFY_URL).mock(return_value=httpx.Response(200, json=[{
        "url": "https://x.com/user/status/123",
        "text": "video post",
        "author": {"username": "user", "name": "U"},
        "createdAt": "2026-05-10T12:00:00.000Z",
        "media": [{
            "type": "video",
            "media_url_https": "https://pbs.twimg.com/amplify_video_thumb/x/img/thumb.jpg",
            "video_info": {
                "duration_millis": 12345,
                "variants": [
                    {"content_type": "video/mp4", "bitrate": 256000,
                     "url": "https://video.twimg.com/amplify_video/x/vid/240x240/lo.mp4"},
                    {"content_type": "video/mp4", "bitrate": 832000,
                     "url": "https://video.twimg.com/amplify_video/x/vid/720x720/hi.mp4"},
                    {"content_type": "application/x-mpegURL",
                     "url": "https://video.twimg.com/amplify_video/x/pl/master.m3u8"},
                ]
            }
        }],
    }]))
    result = fetch_tweet("https://x.com/user/status/123", token="t", actor="xquik~x-tweet-scraper")
    assert result.image_urls == ["https://video.twimg.com/amplify_video/x/vid/720x720/hi.mp4"]
```

- [ ] **Step 2: Run the test and confirm it FAILS**

Run: `pytest tests/test_scraper.py::test_fetch_tweet_extracts_video_url_from_video_info_variants -v`

Expected: FAIL — `result.image_urls == []` because the current `_normalize()` only collects photos.

- [ ] **Step 3: Commit the failing test**

```bash
git add tests/test_scraper.py
git commit -m "test(session-6): flip scraper test to assert video URL extraction"
```

---

## Task 2: Add a test for direct-mp4 fallback shape

**Files:**
- Modify: `tests/test_scraper.py`

- [ ] **Step 1: Add a fallback-shape test**

Append to [tests/test_scraper.py](../../../tests/test_scraper.py):

```python
@respx.mock
def test_fetch_tweet_extracts_video_url_from_direct_media_url():
    # Some Apify actors flatten video entries to just media_url_https pointing
    # at the .mp4. Accept that shape too.
    respx.post(APIFY_URL).mock(return_value=httpx.Response(200, json=[{
        "url": "https://x.com/user/status/123",
        "text": "video",
        "author": {"username": "user", "name": "U"},
        "createdAt": "2026-05-10T12:00:00.000Z",
        "media": [{"type": "video", "media_url_https": "https://video.twimg.com/x.mp4"}],
    }]))
    result = fetch_tweet("https://x.com/user/status/123", token="t", actor="xquik~x-tweet-scraper")
    assert result.image_urls == ["https://video.twimg.com/x.mp4"]


@respx.mock
def test_fetch_tweet_orders_images_before_videos_in_mixed_post():
    respx.post(APIFY_URL).mock(return_value=httpx.Response(200, json=[{
        "url": "https://x.com/user/status/123",
        "text": "mixed",
        "author": {"username": "user", "name": "U"},
        "createdAt": "2026-05-10T12:00:00.000Z",
        "media": [
            {"type": "video", "media_url_https": "https://video.twimg.com/clip.mp4"},
            {"type": "photo", "media_url_https": "https://pbs.twimg.com/media/A.jpg"},
            {"type": "photo", "media_url_https": "https://pbs.twimg.com/media/B.jpg"},
        ],
    }]))
    result = fetch_tweet("https://x.com/user/status/123", token="t", actor="xquik~x-tweet-scraper")
    # Photos first (existing fan-out invariant), videos appended at the end.
    assert result.image_urls == [
        "https://pbs.twimg.com/media/A.jpg",
        "https://pbs.twimg.com/media/B.jpg",
        "https://video.twimg.com/clip.mp4",
    ]
```

- [ ] **Step 2: Run both tests and confirm they FAIL**

Run: `pytest tests/test_scraper.py::test_fetch_tweet_extracts_video_url_from_direct_media_url tests/test_scraper.py::test_fetch_tweet_orders_images_before_videos_in_mixed_post -v`

Expected: both FAIL.

- [ ] **Step 3: Commit the failing tests**

```bash
git add tests/test_scraper.py
git commit -m "test(session-6): add fallback-shape + ordering tests for video URLs"
```

---

## Task 3: Implement video URL extraction in `bot/scraper.py`

**Files:**
- Modify: `bot/scraper.py:69-89`

- [ ] **Step 1: Add the `_extract_video_url` helper**

Open [bot/scraper.py](../../../bot/scraper.py). Above the `_normalize` function (after `_is_diagnostic`, around line 67), add:

```python
_VIDEO_EXTENSIONS = (".mp4", ".mov", ".webm")


def _extract_video_url(media_entry: dict[str, Any]) -> str | None:
    """Return the best video URL for a media entry, or None.

    Preference order:
      1. Highest-bitrate `video/mp4` variant in `video_info.variants`.
      2. Direct `media_url_https` if it ends in a known video extension.
    """
    variants = (media_entry.get("video_info") or {}).get("variants") or []
    mp4_variants = [
        v for v in variants
        if v.get("content_type") == "video/mp4" and v.get("url")
    ]
    if mp4_variants:
        best = max(mp4_variants, key=lambda v: v.get("bitrate") or 0)
        return best["url"]
    direct = media_entry.get("media_url_https")
    if direct and any(direct.lower().endswith(ext) for ext in _VIDEO_EXTENSIONS):
        return direct
    return None
```

- [ ] **Step 2: Rewrite the media-extraction block in `_normalize`**

Replace lines 75-79 of [bot/scraper.py](../../../bot/scraper.py) (the current `image_urls = [...]` comprehension) with:

```python
    media = tweet.get("media") or []
    image_urls: list[str] = [
        m.get("media_url_https") for m in media
        if m.get("type") == "photo" and m.get("media_url_https")
    ]
    # Videos and animated_gif entries: append video URL after photos so the
    # existing multi-image fan-out invariant ("image_urls[1:] are all images")
    # holds for pure-image tweets and mixed tweets order photos-first.
    for m in media:
        if m.get("type") in ("video", "animated_gif"):
            video_url = _extract_video_url(m)
            if video_url:
                image_urls.append(video_url)
```

- [ ] **Step 3: Run all three scraper video tests and confirm they PASS**

Run: `pytest tests/test_scraper.py -k video -v`

Expected: all three new tests PASS. The pre-existing image tests must still pass — run the full file:

Run: `pytest tests/test_scraper.py -v`

Expected: all 11 tests in `tests/test_scraper.py` PASS.

- [ ] **Step 4: Commit**

```bash
git add bot/scraper.py
git commit -m "feat(session-6): scraper extracts video URLs from Apify response"
```

---

## Task 4: Add a `_is_video_url` helper to the processor

**Files:**
- Modify: `bot/processor.py`
- Test: `tests/test_processor.py`

- [ ] **Step 1: Write the failing test**

Open [tests/test_processor.py](../../../tests/test_processor.py). Find an existing import block from `bot.processor` and append `_is_video_url` to it (or add a new import line). Then add this test at the bottom of the file:

```python
def test_is_video_url_detects_known_extensions():
    from bot.processor import _is_video_url
    assert _is_video_url("https://video.twimg.com/x.mp4") is True
    assert _is_video_url("https://video.twimg.com/x.MOV") is True
    assert _is_video_url("https://video.twimg.com/x.webm") is True
    assert _is_video_url("https://pbs.twimg.com/media/A.jpg") is False
    assert _is_video_url("https://video.twimg.com/master.m3u8") is False
    assert _is_video_url("") is False
    assert _is_video_url(None) is False
```

- [ ] **Step 2: Run and confirm it FAILS**

Run: `pytest tests/test_processor.py::test_is_video_url_detects_known_extensions -v`

Expected: FAIL — `ImportError: cannot import name '_is_video_url'`.

- [ ] **Step 3: Implement the helper**

Open [bot/processor.py](../../../bot/processor.py). After the `X_URL_RE` constant (around line 38) and before `extract_x_url`, add:

```python
_VIDEO_EXTENSIONS = (".mp4", ".mov", ".webm")


def _is_video_url(url: str | None) -> bool:
    """Return True iff the URL path ends in a known video extension."""
    if not url:
        return False
    return any(url.lower().endswith(ext) for ext in _VIDEO_EXTENSIONS)
```

- [ ] **Step 4: Run and confirm it PASSES**

Run: `pytest tests/test_processor.py::test_is_video_url_detects_known_extensions -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add bot/processor.py tests/test_processor.py
git commit -m "feat(session-6): add _is_video_url helper to processor"
```

---

## Task 5: Add `_download_video_to_dropbox` helper

**Files:**
- Modify: `bot/processor.py`
- Test: `tests/test_processor.py`

- [ ] **Step 1: Write the failing test**

Add to [tests/test_processor.py](../../../tests/test_processor.py):

```python
def test_download_video_to_dropbox_stages_with_mp4_extension(monkeypatch):
    from bot import processor

    captured = {}

    class FakeDropbox:
        def files_upload(self, *, f, path, mode):
            captured["path"] = path
            captured["bytes_len"] = len(f)

    class FakeResponse:
        content = b"FAKE_MP4_BYTES" * 100

        def raise_for_status(self):
            pass

    class FakeClient:
        def __init__(self, *a, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

        def get(self, url):
            captured["fetched_url"] = url
            return FakeResponse()

    monkeypatch.setattr(processor.httpx, "Client", FakeClient)

    path = processor._download_video_to_dropbox(
        FakeDropbox(),
        "https://video.twimg.com/clip.mp4",
        item_id="item-abc",
        vault_root="/personal-os",
        project="_inbox",
    )

    assert path == "/personal-os/_inbox/_attachments/item-abc.mp4"
    assert captured["fetched_url"] == "https://video.twimg.com/clip.mp4"
    assert captured["bytes_len"] == len(b"FAKE_MP4_BYTES" * 100)
```

- [ ] **Step 2: Run and confirm it FAILS**

Run: `pytest tests/test_processor.py::test_download_video_to_dropbox_stages_with_mp4_extension -v`

Expected: FAIL — `AttributeError: module 'bot.processor' has no attribute '_download_video_to_dropbox'`.

- [ ] **Step 3: Implement the helper**

In [bot/processor.py](../../../bot/processor.py), directly after `_download_image_to_dropbox` (ends ~line 115), add:

```python
def _download_video_to_dropbox(
    dropbox_client,
    video_url: str,
    item_id: str,
    *,
    vault_root: str,
    project: str = "_inbox",
) -> str:
    """Download a video URL and upload to Dropbox inside the vault.

    Files land at `<vault_root>/<project>/_attachments/<item_id>.mp4`.
    Videos can be 10+ MB so timeout is generous compared to the image path.
    The post-classify move step in `process_item` relocates the file once
    the classifier picks a project.
    """
    from dropbox.files import WriteMode

    with httpx.Client(timeout=120.0, follow_redirects=True) as client:
        resp = client.get(video_url)
        resp.raise_for_status()
        content = resp.content
    dropbox_path = f"{vault_root}/{project}/_attachments/{item_id}.mp4"
    dropbox_client.files_upload(f=content, path=dropbox_path, mode=WriteMode("overwrite"))
    return dropbox_path
```

- [ ] **Step 4: Run and confirm it PASSES**

Run: `pytest tests/test_processor.py::test_download_video_to_dropbox_stages_with_mp4_extension -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add bot/processor.py tests/test_processor.py
git commit -m "feat(session-6): add _download_video_to_dropbox helper"
```

---

## Task 6: Wire video download into `process_item` Phase A

**Files:**
- Modify: `bot/processor.py:285-298`
- Test: `tests/test_processor.py`

- [ ] **Step 1: Write the failing integration test**

Add to [tests/test_processor.py](../../../tests/test_processor.py) (the file already imports `MagicMock` and defines the `_fake_classify_response` helper — reuse them):

```python
def test_process_item_with_x_video_url_downloads_video_and_marks_type(mocker):
    item = {
        "id": "item-vid-1",
        "source": "telegram",
        "source_message_id": "10",
        "media_type": "link",
        "raw_text": "https://x.com/designer/status/9001",
        "media_dropbox_path": None,
    }
    mocker.patch(
        "bot.processor.handle_x_url",
        return_value={
            "source_post_id": "sp-vid",
            "image_urls": ["https://video.twimg.com/clip.mp4"],
            "post_text": "cool landing page video",
            "midjourney_params": {},
        },
    )
    video_download = mocker.patch(
        "bot.processor._download_video_to_dropbox",
        return_value="/personal-os/_inbox/_attachments/item-vid-1.mp4",
    )
    image_download = mocker.patch("bot.processor._download_image_to_dropbox")
    mocker.patch(
        "bot.processor.classify_item",
        return_value=_fake_classify_response("design", "video", ["hero"], "landing page video"),
    )
    mocker.patch("bot.processor.write_obsidian_note", return_value="design/x.md")
    # Post-classify move is stubbed to identity so we can read the staged path back.
    mocker.patch(
        "bot.processor.move_dropbox_media",
        side_effect=lambda dropbox_client, from_path, to_path: None,
    )

    result = process_item(
        item,
        anthropic_client=MagicMock(),
        dropbox_client=MagicMock(),
        openai_api_key="x",
        todoist_token="t",
        todoist_projects={"design": "111"},
        vault_root="/personal-os",
        system_blocks=[{"type": "text", "text": "s"}],
        supabase_client=MagicMock(),
        apify_api_token="apify_token",
        apify_tweet_scraper_actor="xquik~x-tweet-scraper",
    )

    assert result["error"] is None
    assert result["source_post_id"] == "sp-vid"
    assert result["media_dropbox_path"].endswith(".mp4")
    # Video helper was called once with the .mp4 URL, image helper untouched.
    video_download.assert_called_once()
    assert video_download.call_args.args[1] == "https://video.twimg.com/clip.mp4"
    image_download.assert_not_called()
    # In-memory item should reflect the type flip so downstream enrichment sees 'video'.
    assert item["media_type"] == "video"
```

- [ ] **Step 2: Run and confirm it FAILS**

Run: `pytest tests/test_processor.py::test_process_item_with_x_video_url_downloads_video_and_marks_type -v`

Expected: FAIL — the current Phase-A code unconditionally calls `_download_image_to_dropbox` on `image_urls[0]`, which is not the function the test patched.

- [ ] **Step 3: Modify `process_item`'s Phase A block**

Open [bot/processor.py](../../../bot/processor.py) and locate the block starting at line 285 (`if scrape_info["image_urls"] and not item.get("media_dropbox_path"):`). Replace lines 285-294 with:

```python
                    if scrape_info["image_urls"] and not item.get("media_dropbox_path"):
                        first_url = scrape_info["image_urls"][0]
                        if _is_video_url(first_url):
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

- [ ] **Step 4: Run and confirm it PASSES**

Run: `pytest tests/test_processor.py::test_process_item_with_x_video_url_downloads_video_and_marks_type -v`

Expected: PASS.

- [ ] **Step 5: Run the entire processor test file to confirm no regressions**

Run: `pytest tests/test_processor.py -v`

Expected: all tests PASS (the prior image-URL Phase-A tests should still pass — only the dispatch logic was added, not changed for images).

- [ ] **Step 6: Commit**

```bash
git add bot/processor.py tests/test_processor.py
git commit -m "feat(session-6): download scraped X video to Dropbox in Phase A"
```

---

## Task 7: Make fan-out skip non-image URLs

**Files:**
- Modify: `bot/processor.py:408-449`
- Test: `tests/test_processor.py`

- [ ] **Step 1: Write the failing test**

Add to [tests/test_processor.py](../../../tests/test_processor.py). Mirror the supabase mock pattern from `test_run_batch_fans_out_multi_image_scrape_into_additional_items` (the `.table().insert().execute()` chain).

```python
def test_fan_out_skips_video_urls(mocker):
    from bot.processor import _fan_out_additional_items_from_scrape

    inserted_rows = [{"id": "child-B"}]  # only B should fan out (A is parent, C is video)
    insert_iter = iter(inserted_rows)

    mock_supabase = MagicMock()
    mock_supabase.table.return_value.insert.return_value.execute.side_effect = (
        lambda: MagicMock(data=[next(insert_iter)])
    )

    image_download = mocker.patch(
        "bot.processor._download_image_to_dropbox",
        side_effect=lambda dropbox_client, image_url, item_id, *, index, vault_root, project: (
            f"{vault_root}/_inbox/_attachments/{item_id}-{index}.jpg"
        ),
    )

    original_item = {
        "id": "item-parent",
        "source": "telegram",
        "source_message_id": "42",
        "raw_text": "https://x.com/u/status/1",
    }
    scrape_info = {
        "source_post_id": "sp-1",
        "image_urls": [
            "https://pbs.twimg.com/media/A.jpg",  # index 0 = parent, never fanned out
            "https://pbs.twimg.com/media/B.jpg",  # index 1 = fan out as image
            "https://video.twimg.com/clip.mp4",   # index 2 = MUST be skipped
        ],
    }

    new_items = _fan_out_additional_items_from_scrape(
        mock_supabase, MagicMock(), original_item, scrape_info, "/personal-os",
    )

    assert len(new_items) == 1
    assert new_items[0]["id"] == "child-B"
    assert new_items[0]["media_dropbox_path"].endswith(".jpg")
    # Image helper called exactly once (for B), not for the video URL.
    assert image_download.call_count == 1
    assert image_download.call_args.args[1] == "https://pbs.twimg.com/media/B.jpg"
```

- [ ] **Step 2: Run and confirm it FAILS**

Run: `pytest tests/test_processor.py::test_fan_out_skips_video_urls -v`

Expected: FAIL — the helper currently iterates `image_urls[1:]` unconditionally and would try to download the .mp4 via the image helper, producing 2 items instead of 1.

- [ ] **Step 3: Modify `_fan_out_additional_items_from_scrape`**

In [bot/processor.py](../../../bot/processor.py), find the loop at line 426 (`for idx, img_url in enumerate(image_urls[1:], start=1):`). Replace the loop body opening lines so it filters out video URLs:

```python
    new_items: list[dict] = []
    for idx, img_url in enumerate(image_urls[1:], start=1):
        if _is_video_url(img_url):
            # Multi-video fan-out is out of scope for v1. Single-video posts
            # are the actual use case; mixed video+image posts are rare. Skip
            # videos here so the existing image helper invariant holds.
            continue
        try:
            row = {
```

(Leave the rest of the loop body identical.)

- [ ] **Step 4: Run and confirm it PASSES**

Run: `pytest tests/test_processor.py::test_fan_out_skips_video_urls -v`

Expected: PASS.

- [ ] **Step 5: Run the full processor file again**

Run: `pytest tests/test_processor.py -v`

Expected: all PASS, including existing fan-out tests.

- [ ] **Step 6: Commit**

```bash
git add bot/processor.py tests/test_processor.py
git commit -m "feat(session-6): fan-out skips video URLs (v1 limitation)"
```

---

## Task 8: Full-suite smoke run

**Files:** none (verification only)

- [ ] **Step 1: Run every test**

Run: `pytest -q`

Expected: all tests PASS. Prior to this plan the count was 211; this plan adds ~6 tests so expect ~217 passing. If anything regresses, stop and diagnose before continuing.

- [ ] **Step 2: Commit any incidental fixes (only if needed)**

If you found a regression caused by a test fixture I didn't anticipate (e.g. a shared mock that returns an empty `image_urls`), fix it surgically and commit:

```bash
git add <fixed-files>
git commit -m "fix(session-6): <one-line description>"
```

---

## Task 9: Rename the existing session-6 plan to session-7

**Files:**
- Rename: `docs/superpowers/plans/2026-05-12-session-6-weekly-digest-rules.md` → `docs/superpowers/plans/2026-05-12-session-7-weekly-digest-rules.md`
- Modify: `CLAUDE.md`
- Modify: any in-plan self-references inside the renamed file

- [ ] **Step 1: Rename the file via `git mv`**

```bash
git mv docs/superpowers/plans/2026-05-12-session-6-weekly-digest-rules.md docs/superpowers/plans/2026-05-12-session-7-weekly-digest-rules.md
```

- [ ] **Step 2: Update the in-file session number**

Open the renamed file and replace any `Session 6` references with `Session 7` (header and intro prose at minimum). Leave the spec link references unchanged (the spec is canonical and unaware of session numbering).

- [ ] **Step 3: Update CLAUDE.md pointers**

Open [CLAUDE.md](../../../CLAUDE.md). Four touches — leave line 10 (`Sessions 2–5 shipped; Session 6 drafted`) alone because the post-ship update of that line happens at Session 6 wrapup, not during this rename.

**3a.** Line 26 — replace:
```
Do not start Session 6 without explicit instruction.
```
with:
```
Do not start Session 7 without explicit instruction.
```

**3b.** Lines 29–30 — replace this block:
```
- **Session 6** — Weekly digest (Sonnet 4.6), monthly rule consolidation, research-thread clustering. **Plan drafted at [docs/superpowers/plans/2026-05-12-session-6-weekly-digest-rules.md](docs/superpowers/plans/2026-05-12-session-6-weekly-digest-rules.md); two open decisions deferred to implementation start (rule-proposal UX, vault rules.md repo sync).**
- **Session 7** — `/find` polish, OA-wiki feeders, multi-source ingest.
```
with:
```
- **Session 7** — Weekly digest (Sonnet 4.6), monthly rule consolidation, research-thread clustering. **Plan drafted at [docs/superpowers/plans/2026-05-12-session-7-weekly-digest-rules.md](docs/superpowers/plans/2026-05-12-session-7-weekly-digest-rules.md); two open decisions deferred to implementation start (rule-proposal UX, vault rules.md repo sync).**
- **Session 8** — `/find` polish, OA-wiki feeders, multi-source ingest.
```

**3c.** Line 83 — replace:
```
- **`/find` retrieval.** No retrieval surface yet beyond Obsidian native search and Supabase queries. Session 7.
```
with:
```
- **`/find` retrieval.** No retrieval surface yet beyond Obsidian native search and Supabase queries. Session 8.
```

**3d.** Line 85 — replace:
```
- **Weekly digest + monthly rule consolidation.** Session 6 — Sonnet 4.6 weekly summary on Saturdays, automatic rule pass once a month, research-thread clustering.
```
with:
```
- **Weekly digest + monthly rule consolidation.** Session 7 — Sonnet 4.6 weekly summary on Saturdays, automatic rule pass once a month, research-thread clustering.
```

- [ ] **Step 4: Commit**

```bash
git add -A docs/superpowers/plans/ CLAUDE.md
git commit -m "docs(plan): renumber weekly-digest plan to session-7"
```

---

## Task 10: Open a PR for the code changes

**Files:** none (git only)

- [ ] **Step 1: Push the branch and open a PR**

Use the standard `gh pr create` flow. PR title: `feat(session-6): video storage for design dashboard`. Body should reference the spec at `docs/superpowers/specs/2026-05-12-ask-roscoe-video-support.md` and the plan file. Note in the body that the renumbering of the digest plan is included.

- [ ] **Step 2: Wait for Ryan to merge**

Do not merge directly; Ryan reviews and merges all PRs to `main`.

---

## Task 11: Deploy to droplet after merge

**Files:** none (operational)

- [ ] **Step 1: SSH and redeploy**

Ryan runs (or paste for him):

```bash
ssh root@64.23.170.115 'cd /opt/personal-os-v2 && git pull && systemctl restart personal-os-v2 && sleep 3 && systemctl status personal-os-v2 --no-pager | head -5'
```

Expected: `Active: active (running)`.

- [ ] **Step 2: Tail the journal for clean startup**

```bash
ssh root@64.23.170.115 'journalctl -u personal-os-v2 -n 50 --no-pager'
```

Expected: no tracebacks; APScheduler shows the three cron jobs registered.

---

## Task 12: Live verification on production

**Files:** none (manual smoke test)

- [ ] **Step 1: Discard the orphaned video row**

Via Supabase MCP (project `sqzbdkxbeotmywjdksmd`), run:

```sql
UPDATE items
SET status = 'discarded',
    error = 'session-6 cleanup: row predates video-storage support, no recoverable source URL'
WHERE id = '2e38f4d8-e426-48b7-9a60-2b68d521d2f0';
```

Expected: 1 row updated.

- [ ] **Step 2: Capture a fresh X-video post**

From Telegram, send the bot a URL of a short X post that contains a video (a landing-page-design clip is ideal). Wait for the 👍 ack.

- [ ] **Step 3: Run `/process`**

In Telegram: `/process`. Wait for `processed N · $X.XX` reply.

- [ ] **Step 4: Verify in Supabase**

Via Supabase MCP:

```sql
SELECT id, project, type, media_type, media_dropbox_path, source_post_id, summary
FROM items
WHERE created_at > now() - interval '15 minutes'
  AND type = 'video'
ORDER BY created_at DESC;
```

Expected: at least one row with `type='video'`, `media_dropbox_path` ending in `.mp4`, `source_post_id` populated, `media_dropbox_path` starting with `/personal-os/design/_attachments/` (post-classify move worked).

- [ ] **Step 5: Verify the file plays from Dropbox**

Open the Dropbox path in the desktop Dropbox app (or the dashboard's signed-URL endpoint). The file should be a playable mp4.

- [ ] **Step 6: Send a Telegram video forward as the second leg of the smoke test**

Forward a short MP4 to the bot directly (not as a link). Run `/process`. Re-run the SQL above. Expected: another `type='video'` row with `media_dropbox_path` populated. This confirms the pre-existing Telegram intake path still works.

---

## Task 13: Hand off to design-dashboard

**Files:** none (cross-project communication)

- [ ] **Step 1: Notify the design-dashboard window**

In the design-dashboard Claude session, paste:

> roscoe-robot now stores videos. Two confirmations from production:
> - Row 1: `<paste id from Task 12 Step 4>` — X-video capture
> - Row 2: `<paste id from Task 12 Step 6>` — Telegram-video capture
>
> Both have `type='video'`, `media_dropbox_path` ending in `.mp4`, and resolve via `/2/files/get_temporary_link`. Go ahead with the one-line `.in('type', ['image', 'video'])` swap and the `<video>` branch in `image-card.tsx` / `detail-panel.tsx`. Spec: `docs/superpowers/specs/2026-05-12-ask-roscoe-video-support.md`.

---

## Out-of-scope (do not implement in this session)

- `poster_dropbox_path` column + ffmpeg first-frame extraction (defer until grid feels slow)
- Multi-video fan-out for tweets with >1 video
- Backfill of pre-Session-6 orphaned rows beyond the one explicit discard
- Animated GIF handling beyond the URL extraction wired up in Task 3 (Twitter serves these as mp4s, so they should "just work" if the post has video_info; verify in production if a test case appears)
