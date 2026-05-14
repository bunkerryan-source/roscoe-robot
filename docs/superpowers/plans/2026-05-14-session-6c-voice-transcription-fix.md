# Session 6c — Voice Memo Transcription Fix

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Voice memos sent to the Telegram bot get transcribed by OpenAI Whisper and flow through the classifier like text memos. Today they don't — `transcribe_voice` is failing silently in production. Voice items land in Supabase with `raw_text=null` and the classifier hallucinates a summary from an empty payload (e.g., the 2026-05-13 voice memo was filed under `project=acute` with summary "Customer research task related to Acute Logistics prospecting activities" — derived from recent corrections, not the audio).

**Architecture:** Two layered changes. (1) **Stop hiding failures.** `enrich_item`'s voice branch currently catches every exception and returns the null `raw_text`, so the classifier sees an empty payload, falls back to its no-caption stub, and invents a summary. We replace silent fallback with a hardcoded "transcription failed" classification (no Haiku call) that lands the item in `needs_review` with the error saved to `items.error`. The audio file stays in Dropbox so Ryan can listen and re-process. (2) **Diagnose and fix the actual root cause** on the droplet. The unit test for `transcribe_voice` already passes (it mocks OpenAI), so the production failure is environmental: bad `OPENAI_API_KEY`, file extension Whisper rejects, audio file size limit, or Dropbox download path. Task 5 is a journal grep that picks the branch.

**Tech Stack:** Python, OpenAI Whisper API, Dropbox Python SDK, Supabase REST, pytest.

**Scope boundary:**
- Voice memos only. Long-video transcription (Whisper-as-summary for tutorial tweets) is a separate v2 enhancement of [Session 6b](2026-05-14-session-6b-long-video-tutorials.md) and is NOT in this plan.
- No retroactive re-transcription of the 2 historical voice items (one `processed` with a hallucinated summary on 2026-05-13, one `discarded` on 2026-05-06). Ryan can re-send them if wanted.
- No new env vars (OpenAI key already required at startup via [bot/config.py](../../../bot/config.py)).
- No schema changes (`items.error` column already exists and is already updated on the failed path).

**Prerequisite:**
- Session 6b shipped (kill-switch released, autonomy restored). This plan and 6b touch different code paths, but landing 6b first keeps the droplet stable while we work on the voice fix.

---

## File Structure

**Modify:**
- `bot/processor.py` — `enrich_item` voice branch + new `_transcription_failure_classification` helper
- `bot/db.py` — extend `update_classified` to accept an `error` field (or add a new `update_classified_with_error` helper)
- `tests/test_processor.py` — failure-path tests
- `tests/test_db.py` — error-update test

**No changes:**
- `bot/enrichment.py` — the `transcribe_voice` helper itself is correct (its unit test passes). The bug is upstream (how we handle its exceptions) and on the droplet (why it raises).
- `bot/main.py` / `bot/intake.py` — Telegram → Dropbox upload path works; the 2026-05-13 voice memo has a valid `media_dropbox_path` on Supabase.

**Out-of-repo:**
- One SSH command to pull journal logs (Task 5).
- Possibly one `.env` edit on the droplet (Task 6 Branch A) — base64-sync pattern from `feedback_env_deploy_via_base64.md` memory.

---

## Task 1: Surface transcription failure in `enrich_item` (failing test first)

**Files:**
- Modify: `tests/test_processor.py`

The existing `test_enrich_voice_item_transcribes` covers the happy path. We add two failure-path tests: one for `transcribe_voice` raising, one for the Dropbox download raising. Both should return a marker payload that names the failure — not the original null `raw_text`.

- [ ] **Step 1: Write the failing tests**

Append to [tests/test_processor.py](../../../tests/test_processor.py):

```python
def test_enrich_voice_returns_failure_marker_when_whisper_raises(mocker):
    item = {
        "media_type": "voice",
        "raw_text": None,
        "media_dropbox_path": "/personal-os/_inbox/2026-05-14/v.ogg",
    }
    mocker.patch("bot.processor._download_dropbox_bytes", return_value=b"fake-ogg")
    mocker.patch(
        "bot.processor.transcribe_voice",
        side_effect=RuntimeError("401 Unauthorized"),
    )

    payload, needs_vision = enrich_item(item, openai_api_key="x", dropbox_client=MagicMock())

    # Marker payload identifies the failure mode so the classifier (or our
    # hardcoded-failure route in process_item) has something deterministic to
    # latch onto. Must NOT be empty/None — that's what causes hallucination.
    assert "voice transcription failed" in payload.lower()
    assert "401" in payload  # underlying exception text is surfaced
    assert needs_vision is False


def test_enrich_voice_returns_failure_marker_when_dropbox_download_raises(mocker):
    item = {
        "media_type": "voice",
        "raw_text": None,
        "media_dropbox_path": "/personal-os/_inbox/2026-05-14/v.ogg",
    }
    mocker.patch(
        "bot.processor._download_dropbox_bytes",
        side_effect=RuntimeError("path not found"),
    )
    # transcribe_voice should never be reached.
    transcribe_mock = mocker.patch("bot.processor.transcribe_voice")

    payload, needs_vision = enrich_item(item, openai_api_key="x", dropbox_client=MagicMock())

    assert "voice transcription failed" in payload.lower()
    assert "path not found" in payload
    transcribe_mock.assert_not_called()


def test_enrich_voice_with_no_media_path_returns_empty_marker():
    # Edge case: voice item created but media_dropbox_path never populated
    # (intake bug). Don't claim "transcription failed" because we never tried.
    item = {"media_type": "voice", "raw_text": None, "media_dropbox_path": None}
    payload, needs_vision = enrich_item(item, openai_api_key="x", dropbox_client=MagicMock())
    # Falls back to raw_text (None → empty string when joined into payload elsewhere).
    # The existing behavior here is correct — leave it alone.
    assert payload is None or payload == ""
```

- [ ] **Step 2: Run and confirm the two failure tests FAIL**

Run: `pytest tests/test_processor.py -k enrich_voice -v`

Expected:
- `test_enrich_voice_item_transcribes` — PASS (existing, unchanged)
- `test_enrich_voice_returns_failure_marker_when_whisper_raises` — FAIL (current code returns `raw_text` which is None)
- `test_enrich_voice_returns_failure_marker_when_dropbox_download_raises` — FAIL (same reason)
- `test_enrich_voice_with_no_media_path_returns_empty_marker` — PASS (existing behavior unchanged)

- [ ] **Step 3: Modify `enrich_item`'s voice branch**

In [bot/processor.py](../../../bot/processor.py), find the voice branch (lines 203-212):

```python
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
```

Replace lines 203-212 with:

```python
    if media_type == "voice":
        if dropbox_client is None or not item.get("media_dropbox_path"):
            return raw_text, False
        try:
            audio_bytes = _download_dropbox_bytes(dropbox_client, item["media_dropbox_path"])
            transcript = transcribe_voice(openai_api_key, audio_bytes, file_extension=".ogg")
            return f"[voice transcript]\n{transcript}", False
        except Exception as e:
            # Surface the failure in the payload so downstream code can
            # detect it instead of silently classifying an empty payload
            # (which causes the classifier to hallucinate). process_item
            # reads this marker and applies a hardcoded "needs_review"
            # classification in Task 3.
            logger.warning("voice transcription failed for item %s: %s", item.get("id"), e)
            return f"[voice transcription failed: {e}]", False
```

- [ ] **Step 4: Run and confirm all four tests PASS**

Run: `pytest tests/test_processor.py -k enrich_voice -v`

Expected: all 4 PASS.

- [ ] **Step 5: Commit**

```bash
git add bot/processor.py tests/test_processor.py
git commit -m "feat(session-6c): enrich_item surfaces voice transcription failure in payload"
```

---

## Task 2: Add `_transcription_failure_classification` helper

**Files:**
- Modify: `bot/processor.py`
- Modify: `tests/test_processor.py`

A failed voice transcription should NOT be classified by Haiku — there's nothing to classify, and the classifier hallucinates. Use a hardcoded classification with `confidence=0.0` so the existing `confidence < NEEDS_REVIEW_THRESHOLD` check (line 444) routes the item into `needs_review`. Ryan sees it in triage, can listen to the audio via Obsidian, and decides what to do.

- [ ] **Step 1: Write the failing tests**

Append to [tests/test_processor.py](../../../tests/test_processor.py):

```python
def test_transcription_failure_classification_marks_needs_review():
    from bot.processor import _transcription_failure_classification

    result = _transcription_failure_classification(error_text="401 Unauthorized")

    # Confidence below NEEDS_REVIEW_THRESHOLD (0.6) so process_item routes to needs_review.
    assert result["confidence"] < 0.6
    assert result["project"] == "personal"  # default bucket — Ryan refiles via triage
    assert result["type"] == "voice"
    assert result["_cost_cents"] == 0  # no Haiku call
    assert "transcription failed" in result["summary"].lower()
    assert "401" in result["summary"]
    assert "transcription-failed" in result["tags"]


def test_transcription_failure_classification_truncates_long_error():
    from bot.processor import _transcription_failure_classification
    long_error = "x" * 500
    result = _transcription_failure_classification(error_text=long_error)
    # Summary must be reasonable length for the Obsidian frontmatter.
    assert len(result["summary"]) <= 250
```

- [ ] **Step 2: Run and confirm both FAIL**

Run: `pytest tests/test_processor.py -k _transcription_failure_classification -v`

Expected: FAIL — `ImportError: cannot import name '_transcription_failure_classification'`.

- [ ] **Step 3: Implement the helper**

In [bot/processor.py](../../../bot/processor.py), directly below `_tutorial_classification` (added by Session 6b), add:

```python
_TRANSCRIPTION_ERROR_SUMMARY_MAX = 250


def _transcription_failure_classification(*, error_text: str) -> dict:
    """Build a hardcoded classification for an item whose voice transcription failed.

    Confidence is intentionally 0.0 so the existing
    `confidence < NEEDS_REVIEW_THRESHOLD` check in process_item routes the
    item into `needs_review`. The error text is surfaced in the summary so
    Ryan can diagnose from the triage card without opening the journal.
    """
    summary = f"Voice transcription failed: {error_text}"[:_TRANSCRIPTION_ERROR_SUMMARY_MAX]
    return {
        "project": "personal",
        "subdomain": None,
        "type": "voice",
        "tags": ["voice", "transcription-failed"],
        "visual_subtype": None,
        "summary": summary,
        "confidence": 0.0,
        "_cost_cents": 0,
    }
```

- [ ] **Step 4: Run and confirm both PASS**

Run: `pytest tests/test_processor.py -k _transcription_failure_classification -v`

Expected: both PASS.

- [ ] **Step 5: Commit**

```bash
git add bot/processor.py tests/test_processor.py
git commit -m "feat(session-6c): _transcription_failure_classification helper"
```

---

## Task 3: `process_item` routes voice failures to needs_review with error saved

**Files:**
- Modify: `bot/processor.py:347-370` (enrich/classify block) and `bot/db.py` (update_classified to accept error)
- Modify: `tests/test_processor.py`

When `enrich_item` returns a payload starting with `[voice transcription failed:`, we route via the same pre-set-classification mechanism Session 6b added. The hardcoded `_transcription_failure_classification` lands the item in `needs_review` (confidence 0.0). We also extend the Supabase update to write the error text into `items.error` so Ryan can diagnose from a SQL query without re-pulling the journal.

- [ ] **Step 1: Write the failing integration test**

Append to [tests/test_processor.py](../../../tests/test_processor.py):

```python
def test_process_item_voice_transcription_failure_routes_to_needs_review(mocker):
    item = {
        "id": "voice-fail-1",
        "source": "telegram",
        "source_message_id": "300",
        "media_type": "voice",
        "raw_text": None,
        "media_dropbox_path": "/personal-os/_inbox/2026-05-14/voice-fail-1.ogg",
    }
    mocker.patch("bot.processor._download_dropbox_bytes", return_value=b"fake-ogg")
    mocker.patch(
        "bot.processor.transcribe_voice",
        side_effect=RuntimeError("401 Unauthorized: Bearer token invalid"),
    )
    # classify_item MUST NOT be called — we use the hardcoded failure classification.
    classify = mocker.patch("bot.processor.classify_item")
    mocker.patch("bot.processor.write_obsidian_note", return_value="personal/v.md")
    mocker.patch("bot.processor.move_dropbox_media", side_effect=lambda dropbox_client, from_path, to_path: None)

    result = process_item(
        item,
        anthropic_client=MagicMock(),
        dropbox_client=MagicMock(),
        openai_api_key="x",
        todoist_token="t",
        todoist_projects={"personal": "111"},
        vault_root="/personal-os",
        system_blocks=[{"type": "text", "text": "s"}],
    )

    assert result["status"] == "needs_review"
    assert result["error"] is not None
    assert "401" in result["error"]
    assert result["classification"]["type"] == "voice"
    assert result["classification"]["confidence"] == 0.0
    assert result["api_cost_cents"] == 0
    classify.assert_not_called()


def test_run_batch_persists_error_field_for_voice_failure(mocker):
    # Regression guard: when a voice item lands in needs_review with an error,
    # run_batch must propagate that error to the Supabase update so it shows
    # up in items.error.
    from bot.processor import run_batch

    pending_items = [{
        "id": "vfail-1",
        "source": "telegram",
        "source_message_id": "301",
        "media_type": "voice",
        "raw_text": None,
        "media_dropbox_path": "/personal-os/_inbox/x.ogg",
    }]
    mocker.patch("bot.processor.fetch_pending_items", return_value=pending_items)
    mocker.patch("bot.processor.fetch_recent_corrections", return_value=[])
    mocker.patch("bot.processor.build_classifier_system_prompt", return_value=[{"type": "text", "text": "s"}])
    mocker.patch("bot.processor._download_dropbox_bytes", return_value=b"x")
    mocker.patch("bot.processor.transcribe_voice", side_effect=RuntimeError("whisper blew up"))
    mocker.patch("bot.processor.write_obsidian_note", return_value="personal/v.md")
    mocker.patch("bot.processor.move_dropbox_media", side_effect=lambda dropbox_client, from_path, to_path: None)
    mocker.patch("bot.processor.insert_run")
    update = mocker.patch("bot.processor.update_classified")

    run_batch(
        supabase_client=MagicMock(),
        anthropic_client=MagicMock(),
        dropbox_client_factory=lambda: MagicMock(),
        openai_api_key="x",
        todoist_token="t",
        todoist_projects={"personal": "111"},
        vault_root="/personal-os",
        rules_md="",
        tag_vocab_md="",
    )

    update.assert_called_once()
    call_kwargs = update.call_args.kwargs
    assert call_kwargs["status"] == "needs_review"
    # The error text must be passed through to update_classified.
    assert "whisper blew up" in (call_kwargs.get("error") or "")
```

- [ ] **Step 2: Run and confirm both FAIL**

Run: `pytest tests/test_processor.py -k "voice_transcription_failure_routes_to_needs_review or persists_error_field" -v`

Expected: both FAIL — current code calls `classify_item` even on voice failures, and `update_classified` doesn't accept an `error` kwarg yet.

- [ ] **Step 3: Branch on the failure marker in `process_item`**

In [bot/processor.py](../../../bot/processor.py), find the post-`enrich_item` block (around lines 347-370). Locate this section:

```python
        payload, _needs_vision = enrich_item(
            item,
            openai_api_key=openai_api_key,
            dropbox_client=dropbox_client,
        )
```

Immediately after that call (still inside the outer try), insert this block BEFORE the existing `# Inject scraped post body into the classifier payload` comment and the subsequent payload-injection / classify_item logic:

```python
        # Voice transcription failure short-circuit — set a hardcoded
        # classification so the classifier doesn't hallucinate from an empty
        # payload. The audio stays in Dropbox; Ryan sees the failure in the
        # triage queue and decides what to do.
        if payload.startswith("[voice transcription failed:"):
            # Extract the error text inside the brackets for the saved error field.
            error_text = payload[len("[voice transcription failed:"):].rstrip("]").strip()
            out["classification"] = _transcription_failure_classification(error_text=error_text)
            out["api_cost_cents"] = 0
            out["error"] = error_text
```

- [ ] **Step 4: Pass `error` through `update_classified`**

In [bot/db.py](../../../bot/db.py), find the `update_classified` function. Add an optional `error: str | None = None` parameter and include it in the update payload only when non-None. (Adjust the exact field assignment to match the existing pattern in the function.)

Concretely, locate the existing signature like:

```python
def update_classified(
    supabase_client,
    *,
    item_id: str,
    classification: dict,
    obsidian_path: str | None,
    todoist_task_id: str | None,
    api_cost_cents: int,
    status: str,
    source_post_id: str | None = None,
    media_dropbox_path: str | None = None,
) -> None:
```

Add the parameter:

```python
def update_classified(
    supabase_client,
    *,
    item_id: str,
    classification: dict,
    obsidian_path: str | None,
    todoist_task_id: str | None,
    api_cost_cents: int,
    status: str,
    source_post_id: str | None = None,
    media_dropbox_path: str | None = None,
    error: str | None = None,
) -> None:
```

And in the dict that gets sent to the `supabase_client.table("items").update(...)` call, conditionally include the error key:

```python
    payload = {
        # ... existing fields ...
    }
    if error is not None:
        payload["error"] = error
    supabase_client.table("items").update(payload).eq("id", item_id).execute()
```

(Match the existing code's exact structure — this is a pattern, not a literal patch.)

- [ ] **Step 5: Wire the error through `run_batch`**

In [bot/processor.py](../../../bot/processor.py)'s `run_batch`, find the `update_classified(...)` call inside the per-item loop. Add `error=result.get("error")` to the kwargs:

```python
            update_classified(
                supabase_client,
                item_id=item["id"],
                classification=result["classification"],
                obsidian_path=result["obsidian_path"],
                todoist_task_id=result["todoist_task_id"],
                api_cost_cents=result["api_cost_cents"],
                status=result["status"],
                source_post_id=result.get("source_post_id"),
                media_dropbox_path=result.get("media_dropbox_path"),
                error=result.get("error"),
            )
```

- [ ] **Step 6: Skip `classify_item` when classification is pre-set (already done by Session 6b — verify)**

Session 6b Task 5 Step 4 added the `if out["classification"] is not None:` branch that skips `classify_item`. This Task 3 reuses that branch. Verify by running:

Run: `pytest tests/test_processor.py::test_process_item_voice_transcription_failure_routes_to_needs_review -v`

Expected: PASS. If FAIL, confirm Session 6b has been merged before continuing this plan.

- [ ] **Step 7: Run both integration tests and confirm PASS**

Run: `pytest tests/test_processor.py -k "voice_transcription_failure or persists_error_field" -v`

Expected: both PASS.

- [ ] **Step 8: Full processor test file sanity check**

Run: `pytest tests/test_processor.py -v`

Expected: all PASS.

- [ ] **Step 9: Commit**

```bash
git add bot/processor.py bot/db.py tests/test_processor.py
git commit -m "feat(session-6c): voice transcription failures land in needs_review with error"
```

---

## Task 4: Confirm `bot/db.update_classified` test covers the error field

**Files:**
- Modify: `tests/test_db.py`

The Task 3 change introduced an optional `error` parameter to `update_classified`. Add an explicit unit test so the contract is locked in.

- [ ] **Step 1: Write the failing test**

Append to [tests/test_db.py](../../../tests/test_db.py) (mirror an existing `update_classified` test's mock pattern; it likely uses a chained `.table().update().eq().execute()` MagicMock):

```python
def test_update_classified_includes_error_when_provided():
    from bot.db import update_classified

    supabase = MagicMock()
    update_chain = supabase.table.return_value.update
    classification = {
        "project": "personal",
        "subdomain": None,
        "type": "voice",
        "tags": ["voice", "transcription-failed"],
        "visual_subtype": None,
        "summary": "Voice transcription failed: 401",
        "confidence": 0.0,
    }

    update_classified(
        supabase,
        item_id="vf-1",
        classification=classification,
        obsidian_path="personal/v.md",
        todoist_task_id=None,
        api_cost_cents=0,
        status="needs_review",
        error="401 Unauthorized",
    )

    update_chain.assert_called_once()
    payload = update_chain.call_args.args[0]
    assert payload["error"] == "401 Unauthorized"
    assert payload["status"] == "needs_review"


def test_update_classified_omits_error_when_not_provided():
    from bot.db import update_classified

    supabase = MagicMock()
    update_chain = supabase.table.return_value.update
    classification = {
        "project": "design",
        "subdomain": None,
        "type": "image",
        "tags": ["hero"],
        "visual_subtype": None,
        "summary": "hero banner",
        "confidence": 0.9,
    }

    update_classified(
        supabase,
        item_id="img-1",
        classification=classification,
        obsidian_path="design/x.md",
        todoist_task_id=None,
        api_cost_cents=2,
        status="processed",
        # no error kwarg
    )

    payload = update_chain.call_args.args[0]
    # Regression guard: don't accidentally clear a previously-set error
    # by writing error=None on the happy path.
    assert "error" not in payload
```

- [ ] **Step 2: Run and confirm both PASS**

Run: `pytest tests/test_db.py -k update_classified_includes_error -v` and `pytest tests/test_db.py -k update_classified_omits_error -v`

Expected: both PASS (the implementation from Task 3 should already satisfy these).

If the second test FAILS because the Task 3 implementation always includes `error` in the payload (even when None), tighten the Task 3 implementation to only include `error` when `error is not None`. The intent is: an explicit error string overwrites; an absent error leaves the column alone.

- [ ] **Step 3: Commit**

```bash
git add tests/test_db.py
git commit -m "test(session-6c): lock in update_classified error-field contract"
```

---

## Task 5: Diagnostic — pull journal logs for the 2026-05-13 voice memo

**Files:** none (live investigation)

The unit tests for `transcribe_voice` pass — so the production failure is environmental. Pull the actual exception text from the droplet journal. This determines which branch of Task 6 we execute.

- [ ] **Step 1: SSH and grep the journal**

```powershell
ssh root@64.23.170.115 'journalctl -u personal-os-v2 --since "2026-05-12" --no-pager | grep -i -E "voice|whisper|transcription|openai|1010f4a4"'
```

- [ ] **Step 2: Interpret the output and pick a Task 6 branch**

| Symptom in the log | Likely root cause | Task 6 branch |
|---|---|---|
| `401`, `Unauthorized`, `Bearer token invalid`, `Incorrect API key` | OPENAI_API_KEY is wrong or revoked on the droplet | **A** |
| `400`, `Invalid file format`, `Could not decode`, `Unsupported file type` | Whisper rejects the `.ogg` filename — maybe needs explicit MIME type or `.oga` | **B** |
| `413`, `file too large`, exceeds size limit | Audio file >25 MB (Whisper limit) — unlikely for voice memos, but possible | **C** |
| `404`, `path not found`, Dropbox path resolution error | `media_dropbox_path` written at intake doesn't match the post-move path | **D** |
| `429`, `rate limit`, `quota` | Out of OpenAI credits | **A** (variant — top up account, key is fine) |
| Nothing matching — clean log | Either no voice memo has been sent post-fix, OR the warning was emitted before/after the grep window. Send a fresh voice memo (Task 7 Step 1) and re-grep. | re-grep |

- [ ] **Step 3: Document what you found**

Add a note to the bottom of THIS plan file (commit it) under a new `## Task 5 findings` heading: paste the exact log line(s) and identify the branch. This becomes the audit trail for next time.

```bash
git add docs/superpowers/plans/2026-05-14-session-6c-voice-transcription-fix.md
git commit -m "docs(session-6c): record root-cause diagnostic from droplet journal"
```

---

## Task 6: Fix the root cause (choose ONE branch based on Task 5)

### Branch A — OPENAI_API_KEY is bad or revoked

**Files:** none (operational)

- [ ] **A1: Generate a fresh OpenAI API key**

In the OpenAI dashboard: API Keys → Create new secret key. Copy it (one-time visibility).

- [ ] **A2: Sync the new key to the droplet via base64** (per `feedback_env_deploy_via_base64.md`)

From Windows:

```powershell
$key = "sk-..."  # paste the new key here
$encoded = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($key))
ssh root@64.23.170.115 "echo $encoded | base64 -d > /tmp/openai_key && KEY=`$(cat /tmp/openai_key) && sed -i ""s|^OPENAI_API_KEY=.*|OPENAI_API_KEY=`$KEY|"" /opt/personal-os-v2/.env && rm /tmp/openai_key && grep OPENAI_API_KEY /opt/personal-os-v2/.env | head -c 20"
```

Expected: prints the first 20 chars of `OPENAI_API_KEY=sk-...` confirming it was set.

- [ ] **A3: Restart and verify**

```powershell
ssh root@64.23.170.115 'systemctl restart personal-os-v2 && sleep 3 && systemctl status personal-os-v2 --no-pager | head -10'
```

Skip to Task 7.

### Branch B — Whisper rejects the `.ogg` filename

**Files:**
- Modify: `bot/processor.py:208` (the `transcribe_voice` call site)
- Test: `tests/test_processor.py` (extend the happy-path test)

Telegram delivers voice memos as OGG Opus. Whisper's API officially supports `ogg` but some clients see issues unless the filename uses `.oga` or the MIME is set explicitly. Try `.oga` first; if that fails, pass MIME via the `OpenAI` client's audio kwargs.

- [ ] **B1: Update the file_extension passed to transcribe_voice**

In [bot/processor.py](../../../bot/processor.py), find line 208:

```python
            transcript = transcribe_voice(openai_api_key, audio_bytes, file_extension=".ogg")
```

Change to:

```python
            # Whisper accepts .oga for OGG Opus; .ogg occasionally trips client-side validation.
            transcript = transcribe_voice(openai_api_key, audio_bytes, file_extension=".oga")
```

- [ ] **B2: Update the existing happy-path test**

In [tests/test_processor.py](../../../tests/test_processor.py), `test_enrich_voice_item_transcribes` — there's nothing that asserts the file extension. No test change needed unless you want to lock it in:

```python
def test_enrich_voice_uses_oga_extension(mocker):
    item = {"media_type": "voice", "raw_text": "", "media_dropbox_path": "/x.ogg"}
    mocker.patch("bot.processor._download_dropbox_bytes", return_value=b"x")
    transcribe = mocker.patch("bot.processor.transcribe_voice", return_value="hello")
    enrich_item(item, openai_api_key="k", dropbox_client=MagicMock())
    assert transcribe.call_args.kwargs["file_extension"] == ".oga"
```

- [ ] **B3: Run and confirm tests PASS**

Run: `pytest tests/test_processor.py -k voice -v`

- [ ] **B4: Commit**

```bash
git add bot/processor.py tests/test_processor.py
git commit -m "fix(session-6c): use .oga extension for OGG Opus Whisper uploads"
```

Skip to Task 7. If Whisper STILL rejects in production after deploy, escalate to Branch B2: explicit MIME (`audio/ogg`) via a `transcribe_voice` parameter — out of scope for this plan, file a follow-up.

### Branch C — Audio file too large

**Files:**
- Modify: `bot/processor.py`
- Test: `tests/test_processor.py`

Whisper limit is 25 MB. Telegram voice memos at 16 kbps Opus are tiny (~120 KB/min), so 25 MB = ~3.5 hours. Very unlikely. If this is the actual root cause, skip transcription with an explicit too-large failure marker rather than calling Whisper at all.

- [ ] **C1: Add a size guard in `enrich_item`**

(Sketch — write the exact code at execution time after seeing the actual error.) Check `len(audio_bytes) > 24 * 1024 * 1024` before the Whisper call; if exceeded, raise a `RuntimeError("audio file too large for Whisper (>24 MB)")` so the Task 1 marker path catches it. Tests: a >24MB bytestring triggers the marker; a <24MB does not.

- [ ] **C2: Commit and skip to Task 7.**

### Branch D — Dropbox download path issue

**Files:**
- Investigation only — likely an `items.media_dropbox_path` mismatch between intake-time and process-time. Read the path stored on the 5/13 voice memo via Supabase MCP and confirm whether the file is actually at that path on Dropbox. If not, the post-classify move step is the culprit and the fix is in `bot/main.py` or `bot/processor.py` post-move logic. Diagnose interactively; this branch is unlikely enough not to pre-plan in detail.

---

## Task 7: Live smoke test on the droplet

**Files:** none (verification)

- [ ] **Step 1: Send a voice memo to the bot from Telegram**

Open Telegram, find the Roscoe bot chat, hold the mic button, record ~10 seconds of speech (say something specific like "test transcription, May 14 at 2 pm"). Release to send.

- [ ] **Step 2: Confirm intake worked**

Via Supabase MCP:

```sql
SELECT id, status, media_type, media_dropbox_path, raw_text, created_at
FROM items
WHERE created_at > now() - interval '5 minutes'
  AND media_type = 'voice'
ORDER BY created_at DESC
LIMIT 1;
```

Expected: 1 row, `status='pending'`, `media_dropbox_path` like `/personal-os/_inbox/2026-05-14/<uuid>.ogg`.

- [ ] **Step 3: Run `/process`**

In Telegram: `/process`. Wait for `processed N · $X.XX` reply.

- [ ] **Step 4: Verify transcription landed**

```sql
SELECT id, status, raw_text, summary, error, project, type
FROM items
WHERE id = '<paste id from Step 2>';
```

Expected: `raw_text` is NULL (we never write the transcript there — it lives in the classifier payload), but `summary` should reflect the actual audio content (something like "test transcription May 14 at 2 pm" or a paraphrase). `status='processed'`. `error` IS NULL.

- [ ] **Step 5: Verify the Obsidian note**

Open the vault. Find the note (filed under whatever project the classifier picked, default `personal/`). The note body should contain the transcript text under "## Raw capture" or in the summary.

- [ ] **Step 6: Verify the journal shows the Whisper call succeeded**

```powershell
ssh root@64.23.170.115 'journalctl -u personal-os-v2 -n 100 --no-pager | grep -i "api.openai.com\|transcrip"'
```

Expected: a `POST https://api.openai.com/v1/audio/transcriptions "HTTP/1.1 200 OK"` line. NO `voice transcription failed` warning.

- [ ] **Step 7: Test the failure path explicitly (optional but recommended)**

Temporarily break the key (replace one character in the OPENAI_API_KEY env var via sed), restart, send another voice memo, `/process`, and verify the item lands with `status='needs_review'` and `items.error` populated. Then restore the key, restart.

---

## Task 8: PR and deploy

**Files:** none (git only)

- [ ] **Step 1: Push the branch and open a PR**

```bash
git push -u origin <branch-name>
gh pr create --title "feat(session-6c): voice transcription fix" --body "$(cat <<'EOF'
## Summary

- Voice transcription failures no longer cause the classifier to hallucinate from an empty payload. Failed items land in `needs_review` with the underlying error saved to `items.error`.
- Fixes the root cause of `transcribe_voice` failing on the droplet (see Task 5 findings in the plan for the specific cause).
- `update_classified` now accepts an optional `error` field for surfacing transcription-style failures without going through the outer try/except `failed` path.

## Test plan

- [ ] All tests pass (`pytest -q`). New test count ~239 (Session 6c adds ~7).
- [ ] Live smoke: send a voice memo, `/process`, verify summary reflects the audio.
- [ ] Live failure smoke: break the key briefly, send a voice memo, `/process`, verify needs_review + error.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 2: Wait for Ryan to merge**

---

## Task 9: Deploy and monitor

**Files:** none (operational)

- [ ] **Step 1: Standard redeploy**

```powershell
ssh root@64.23.170.115 'cd /opt/personal-os-v2 && git pull && systemctl restart personal-os-v2 && sleep 3 && systemctl status personal-os-v2 --no-pager | head -10'
```

- [ ] **Step 2: Run the live smoke test again post-deploy (Task 7 abbreviated)**

Send one voice memo, run `/process`, verify the transcript lands. This catches any prod-only deploy issues.

- [ ] **Step 3: Update CLAUDE.md**

Append to the "Things that are NOT done yet" section: strike through the voice transcription bullet (or remove it entirely):

```
- **Voice transcription on droplet.** Code path is built (`transcribe_voice` in `bot/enrichment.py`) but unverified end-to-end on droplet — no real voice memo has been processed yet.
```

Replace with a current-state note in the "Current state" block confirming Session 6c shipped. Commit.

- [ ] **Step 4: Save a memory recording the production root cause**

Whatever Task 5's diagnostic uncovered (Branch A/B/C/D) is worth saving for future debugging. Write a feedback memory at `feedback_voice_whisper_root_cause.md` summarizing: symptom, root cause, fix. Link from `MEMORY.md`.

---

## Out-of-scope (do not implement in this session)

- Retroactive re-transcription of the 2 historical voice items (1 processed with hallucinated summary on 2026-05-13, 1 discarded on 2026-05-06). Ryan can re-send if wanted.
- Whisper-based summarization for long-video tutorials. That's a v2 enhancement of [Session 6b](2026-05-14-session-6b-long-video-tutorials.md) — separate plan when needed.
- Streaming uploads to Whisper for >25 MB files (Branch C territory only if it bites).
- Switching to an open-source Whisper deployment (cost optimization, only worth it at much higher volume than 1-2 voice memos/week).
- Multi-language detection. Default is English; if Ryan starts sending Spanish/French memos, add `language=` kwarg to `transcribe_voice`.
