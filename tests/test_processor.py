from unittest.mock import MagicMock

import pytest

from bot.processor import enrich_item, process_item, run_batch


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
    item = {"media_type": "voice", "raw_text": "", "media_dropbox_path": "/personal-os/_inbox/x.ogg"}
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
    yt_mock.side_effect = ValueError("not a youtube URL")
    mocker.patch(
        "bot.processor.fetch_og_metadata",
        return_value={"title": "Post", "description": "About something", "image": "", "site_name": ""},
    )

    payload, needs_vision = enrich_item(item, openai_api_key="x")
    assert "Post" in payload
    assert "About something" in payload


def test_enrich_image_with_short_caption_needs_vision():
    item = {"media_type": "image", "raw_text": "x", "media_dropbox_path": "/personal-os/_inbox/x.jpg"}
    payload, needs_vision = enrich_item(item, openai_api_key="x")
    assert needs_vision is True


def test_enrich_image_with_decisive_caption_skips_vision():
    item = {
        "media_type": "image",
        "raw_text": "lake arrowhead kitchen tile inspiration",
        "media_dropbox_path": "/personal-os/_inbox/x.jpg",
    }
    payload, needs_vision = enrich_item(item, openai_api_key="x")
    assert needs_vision is False
    assert "lake arrowhead" in payload.lower()


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
        todoist_projects={"claude-build": "1000005"},
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
        todoist_projects={"acute": "1000001"},
        vault_root="/personal-os",
        system_blocks=[{"type": "text", "text": "s"}],
    )

    assert result["todoist_task_id"] == "todoist-task-9999"


def test_process_item_moves_media_for_image(mocker):
    item = {
        "id": "item-3",
        "media_type": "image",
        "raw_text": "design hero with dark gradient",
        "media_dropbox_path": "/personal-os/_inbox/2026-05-06/item-3.jpg",
    }

    mocker.patch("bot.processor.classify_item", return_value=_fake_classify_response(
        "design", "image", ["hero", "dark-mode"], "Hero with dark gradient."
    ))
    mocker.patch("bot.processor.write_obsidian_note", return_value="design/2026-05-06-hero.md")
    move_mock = mocker.patch(
        "bot.processor.move_dropbox_media",
        return_value="/personal-os/design/_attachments/item-3.jpg",
    )

    result = process_item(
        item,
        anthropic_client=MagicMock(),
        dropbox_client=MagicMock(),
        openai_api_key="x",
        todoist_token="t",
        todoist_projects={"design": "1000006"},
        vault_root="/personal-os",
        system_blocks=[{"type": "text", "text": "s"}],
    )

    assert result["status"] == "processed"
    move_mock.assert_called_once()
    move_kwargs = move_mock.call_args.kwargs
    assert move_kwargs["from_path"] == "/personal-os/_inbox/2026-05-06/item-3.jpg"
    assert move_kwargs["to_path"] == "/personal-os/design/_attachments/item-3.jpg"


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
        todoist_projects={},
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
        todoist_projects={"personal": "1000007"},
        vault_root="/personal-os",
        system_blocks=[{"type": "text", "text": "s"}],
    )

    assert result["status"] == "needs_review"


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
        todoist_projects={"personal": "1000007"},
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
            {"status": "failed", "classification": None, "obsidian_path": None, "todoist_task_id": None, "api_cost_cents": 0, "error": "boom"},
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
        todoist_projects={},
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
        todoist_projects={},
        vault_root="/personal-os",
        rules_md="",
        tag_vocab_md="",
    )

    assert result["items_processed"] == 0
    assert result["items_failed"] == 0
    insert_run_mock.assert_called_once()


# -----------------------------------------------------------------------------
# Session 4 — X URL scrape + fan-out
# -----------------------------------------------------------------------------


def test_extract_x_url_returns_x_com_url():
    from bot.processor import extract_x_url
    assert extract_x_url("check this https://x.com/naval/status/123 cool") == "https://x.com/naval/status/123"


def test_extract_x_url_returns_twitter_com_url():
    from bot.processor import extract_x_url
    assert extract_x_url("https://twitter.com/elonmusk/status/456") == "https://twitter.com/elonmusk/status/456"


def test_extract_x_url_strips_trailing_punctuation():
    from bot.processor import extract_x_url
    assert extract_x_url("see https://x.com/u/status/1.") == "https://x.com/u/status/1"
    assert extract_x_url("(https://x.com/u/status/1)") == "https://x.com/u/status/1"


def test_extract_x_url_returns_none_when_absent():
    from bot.processor import extract_x_url
    assert extract_x_url("just a thought, no link") is None
    assert extract_x_url("") is None
    assert extract_x_url(None) is None


def test_extract_x_url_ignores_other_domains():
    from bot.processor import extract_x_url
    assert extract_x_url("https://example.com/x.com/fake") is None


def test_handle_x_url_creates_source_post_when_cache_miss(mocker):
    from bot.processor import handle_x_url
    from bot.scraper import ScrapeResult

    mocker.patch("bot.processor.get_source_post_by_url", return_value=None)
    mocker.patch(
        "bot.processor.scraper.fetch_tweet",
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
    insert_mock = mocker.patch("bot.processor.insert_source_post", return_value="sp-new")

    out = handle_x_url(MagicMock(), "https://x.com/u/status/1", token="t", actor="a")

    assert out["source_post_id"] == "sp-new"
    assert out["image_urls"] == ["https://pbs.twimg.com/A.jpg"]
    assert out["post_text"] == "design --sref 999"
    assert out["midjourney_params"] == {"sref": "999"}
    insert_mock.assert_called_once()


def test_handle_x_url_reuses_cached_source_post_when_present(mocker):
    from bot.processor import handle_x_url

    cached = {
        "id": "sp-cached",
        "image_urls": ["https://pbs.twimg.com/Z.jpg"],
        "post_text": "cached",
        "midjourney_params": {"ar": "1:1"},
    }
    mocker.patch("bot.processor.get_source_post_by_url", return_value=cached)
    fetch_mock = mocker.patch("bot.processor.scraper.fetch_tweet")
    insert_mock = mocker.patch("bot.processor.insert_source_post")

    out = handle_x_url(MagicMock(), "https://x.com/u/status/1", token="t", actor="a")

    assert out["source_post_id"] == "sp-cached"
    assert out["image_urls"] == ["https://pbs.twimg.com/Z.jpg"]
    fetch_mock.assert_not_called()
    insert_mock.assert_not_called()


def test_process_item_scrapes_when_raw_text_contains_x_url(mocker):
    item = {
        "id": "item-x1",
        "media_type": "link",
        "raw_text": "thoughts? https://x.com/naval/status/123",
        "media_dropbox_path": None,
    }
    mocker.patch(
        "bot.processor.handle_x_url",
        return_value={
            "source_post_id": "sp-1",
            "image_urls": ["https://pbs.twimg.com/A.jpg"],
            "post_text": "naval wisdom about systems",
            "midjourney_params": {},
        },
    )
    mocker.patch("bot.processor._download_image_to_dropbox", return_value="/personal-os/_inbox/_attachments/item-x1-0.jpg")
    classify_mock = mocker.patch("bot.processor.classify_item", return_value=_fake_classify_response(
        "claude-build", "article", ["systems"], "naval thread on systems thinking"
    ))
    mocker.patch("bot.processor.write_obsidian_note", return_value="claude-build/x.md")

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

    assert result["source_post_id"] == "sp-1"
    assert result["media_dropbox_path"] is not None  # post-classify move sets this
    assert result["scrape_info"]["image_urls"] == ["https://pbs.twimg.com/A.jpg"]
    # classifier payload should include the scraped post text
    payload_arg = classify_mock.call_args.args[2]
    assert "naval wisdom about systems" in payload_arg


def test_process_item_skips_scrape_when_no_x_url(mocker):
    item = {
        "id": "item-nox",
        "media_type": "text",
        "raw_text": "just a normal thought, no URL",
        "media_dropbox_path": None,
    }
    handle_mock = mocker.patch("bot.processor.handle_x_url")
    mocker.patch("bot.processor.classify_item", return_value=_fake_classify_response(
        "personal", "idea", [], "thought"
    ))
    mocker.patch("bot.processor.write_obsidian_note", return_value="personal/x.md")

    process_item(
        item,
        anthropic_client=MagicMock(),
        dropbox_client=MagicMock(),
        openai_api_key="x",
        todoist_token="t",
        todoist_projects={},
        vault_root="/personal-os",
        system_blocks=[{"type": "text", "text": "s"}],
        supabase_client=MagicMock(),
        apify_api_token="apify_token",
    )

    handle_mock.assert_not_called()


def test_process_item_skips_scrape_when_item_is_fanout_child(mocker):
    """Regression: fan-out children inherit raw_text containing the X URL.

    Without the source_post_id guard in process_item, the child re-detects the
    URL, cache-hits the same source_post, and run_batch fans out N-1 more
    children — an unbounded loop. Guard must skip handle_x_url entirely when
    source_post_id is already set on the item.
    """
    item = {
        "id": "fanout-child-1",
        "media_type": "image",
        "raw_text": "https://x.com/u/status/123",
        "media_dropbox_path": "/personal-os/_inbox/_attachments/fanout-child-1-1.jpg",
        "source_post_id": "sp-already-set",
    }
    handle_mock = mocker.patch("bot.processor.handle_x_url")
    mocker.patch("bot.processor.get_source_post_by_id", return_value=None)
    mocker.patch("bot.processor.classify_item", return_value=_fake_classify_response(
        "design", "image", [], "image from a tweet"
    ))
    mocker.patch("bot.processor.write_obsidian_note", return_value="design/x.md")
    mocker.patch("bot.processor.move_dropbox_media")

    result = process_item(
        item,
        anthropic_client=MagicMock(),
        dropbox_client=MagicMock(),
        openai_api_key="x",
        todoist_token="t",
        todoist_projects={},
        vault_root="/personal-os",
        system_blocks=[{"type": "text", "text": "s"}],
        supabase_client=MagicMock(),
        apify_api_token="apify_token",
        apify_tweet_scraper_actor="xquik~x-tweet-scraper",
    )

    handle_mock.assert_not_called()
    assert result["scrape_info"] is None
    # run_batch keys on scrape_info to decide whether to fan out; None means no fan-out.


def test_process_item_loads_source_post_context_for_fanout_child(mocker):
    """Fan-out children must load cached post_text so the classifier sees the
    same context as the parent — otherwise they mis-classify as random links."""
    item = {
        "id": "fanout-child-2",
        "media_type": "image",
        "raw_text": "https://x.com/midlibrary_io/status/123",
        "media_dropbox_path": "/personal-os/_inbox/_attachments/fanout-child-2-1.jpg",
        "source_post_id": "sp-parent",
    }
    lookup_mock = mocker.patch(
        "bot.processor.get_source_post_by_id",
        return_value={
            "id": "sp-parent",
            "post_text": "warm beige editorial hero --sref 999",
            "midjourney_params": {"sref": "999"},
            "image_urls": ["a.jpg", "b.jpg", "c.jpg", "d.jpg"],
        },
    )
    classify_mock = mocker.patch("bot.processor.classify_item", return_value=_fake_classify_response(
        "design", "image", ["hero"], "design hero"
    ))
    mocker.patch("bot.processor.write_obsidian_note", return_value="design/x.md")
    mocker.patch("bot.processor.move_dropbox_media", return_value="/personal-os/design/_attachments/fanout-child-2.jpg")

    result = process_item(
        item,
        anthropic_client=MagicMock(),
        dropbox_client=MagicMock(),
        openai_api_key="x",
        todoist_token="t",
        todoist_projects={},
        vault_root="/personal-os",
        system_blocks=[{"type": "text", "text": "s"}],
        supabase_client=MagicMock(),
        apify_api_token="apify_token",
    )

    lookup_mock.assert_called_once()
    # Classifier payload should now include the scraped post text
    payload_arg = classify_mock.call_args.args[2]
    assert "warm beige editorial" in payload_arg
    # But out["scrape_info"] must stay None — otherwise run_batch fans out again
    assert result["scrape_info"] is None
    # source_post_id still propagated so update_classified writes it through
    assert result["source_post_id"] == "sp-parent"


def test_process_item_fanout_child_handles_missing_source_post(mocker):
    """If get_source_post_by_id returns None, the item still classifies (text-only)."""
    item = {
        "id": "fanout-child-3",
        "media_type": "image",
        "raw_text": "https://x.com/u/status/xx",
        "media_dropbox_path": "/personal-os/_inbox/_attachments/fanout-child-3-1.jpg",
        "source_post_id": "sp-deleted",
    }
    mocker.patch("bot.processor.get_source_post_by_id", return_value=None)
    handle_mock = mocker.patch("bot.processor.handle_x_url")
    mocker.patch("bot.processor.classify_item", return_value=_fake_classify_response(
        "personal", "image", [], "x"
    ))
    mocker.patch("bot.processor.write_obsidian_note", return_value="personal/x.md")
    mocker.patch("bot.processor.move_dropbox_media")

    result = process_item(
        item,
        anthropic_client=MagicMock(),
        dropbox_client=MagicMock(),
        openai_api_key="x",
        todoist_token="t",
        todoist_projects={},
        vault_root="/personal-os",
        system_blocks=[{"type": "text", "text": "s"}],
        supabase_client=MagicMock(),
        apify_api_token="apify_token",
    )

    handle_mock.assert_not_called()
    assert result["scrape_info"] is None
    assert result["status"] in ("processed", "needs_review")


def test_process_item_continues_when_scraper_errors(mocker):
    from bot.scraper import ScraperError
    item = {
        "id": "item-fail",
        "media_type": "link",
        "raw_text": "https://x.com/u/status/deleted",
        "media_dropbox_path": None,
    }
    mocker.patch("bot.processor.handle_x_url", side_effect=ScraperError("zero-output"))
    mocker.patch("bot.processor.classify_item", return_value=_fake_classify_response(
        "personal", "idea", [], "x link"
    ))
    mocker.patch("bot.processor.write_obsidian_note", return_value="personal/x.md")

    result = process_item(
        item,
        anthropic_client=MagicMock(),
        dropbox_client=MagicMock(),
        openai_api_key="x",
        todoist_token="t",
        todoist_projects={},
        vault_root="/personal-os",
        system_blocks=[{"type": "text", "text": "s"}],
        supabase_client=MagicMock(),
        apify_api_token="apify_token",
    )

    # Should still complete (fallback to bare URL classification)
    assert result["status"] in ("processed", "needs_review")
    assert result["source_post_id"] is None
    assert result["scrape_info"] is None


def test_process_item_does_not_overwrite_user_uploaded_media(mocker):
    item = {
        "id": "item-mixed",
        "media_type": "image",
        "raw_text": "screenshot of https://x.com/u/status/1",
        "media_dropbox_path": "/personal-os/_inbox/user-upload.jpg",  # user attached their own
    }
    mocker.patch(
        "bot.processor.handle_x_url",
        return_value={
            "source_post_id": "sp-1",
            "image_urls": ["https://pbs.twimg.com/scraped.jpg"],
            "post_text": "tweet body",
            "midjourney_params": {},
        },
    )
    download_mock = mocker.patch("bot.processor._download_image_to_dropbox")
    mocker.patch("bot.processor.classify_item", return_value=_fake_classify_response(
        "design", "image", ["x"], "design tweet"
    ))
    mocker.patch("bot.processor.write_obsidian_note", return_value="design/x.md")

    process_item(
        item,
        anthropic_client=MagicMock(),
        dropbox_client=MagicMock(),
        openai_api_key="x",
        todoist_token="t",
        todoist_projects={},
        vault_root="/personal-os",
        system_blocks=[{"type": "text", "text": "s"}],
        supabase_client=MagicMock(),
        apify_api_token="apify_token",
    )

    # User's upload preserved; scraped image NOT downloaded
    download_mock.assert_not_called()


def test_process_item_runs_vision_when_design_image(mocker):
    """Design+image+media triggers two-pass vision; refinement merges into classification."""
    from bot.vision import VisionRefinement

    item = {
        "id": "item-vis-1",
        "media_type": "image",
        "raw_text": "design inspo",
        "media_dropbox_path": "/personal-os/_inbox/_attachments/item-vis-1.jpg",
    }
    mocker.patch("bot.processor.classify_item", return_value=_fake_classify_response(
        "design", "image", ["initial-tag"], "initial summary"
    ))
    mocker.patch("bot.processor._download_dropbox_bytes", return_value=b"\xff\xd8\xff\xe0jpeg-bytes")
    refine_mock = mocker.patch(
        "bot.processor.refine_with_vision",
        return_value=VisionRefinement(
            visual_subtype="hero",
            tags=["editorial", "warm-palette"],
            summary="Editorial hero with warm beige.",
            cost_cents=3,
        ),
    )
    mocker.patch("bot.processor.write_obsidian_note", return_value="design/x.md")
    mocker.patch("bot.processor.move_dropbox_media", return_value="/personal-os/design/_attachments/item-vis-1.jpg")

    result = process_item(
        item,
        anthropic_client=MagicMock(),
        dropbox_client=MagicMock(),
        openai_api_key="x",
        todoist_token="t",
        todoist_projects={},
        vault_root="/personal-os",
        system_blocks=[{"type": "text", "text": "s"}],
    )

    refine_mock.assert_called_once()
    assert result["classification"]["visual_subtype"] == "hero"
    assert result["classification"]["tags"] == ["editorial", "warm-palette"]
    assert result["classification"]["summary"] == "Editorial hero with warm beige."
    # cost_cents from text (1) + vision (3) = 4
    assert result["api_cost_cents"] == 4


def test_process_item_skips_vision_when_not_design(mocker):
    """Non-design items never trigger the vision pass."""
    item = {
        "id": "item-vis-2",
        "media_type": "text",
        "raw_text": "follow up with walmart",
        "media_dropbox_path": None,
    }
    mocker.patch("bot.processor.classify_item", return_value=_fake_classify_response(
        "acute", "todo", ["prospecting"], "Follow up with Walmart."
    ))
    refine_mock = mocker.patch("bot.processor.refine_with_vision")
    mocker.patch("bot.processor.write_obsidian_note", return_value="acute/x.md")
    mocker.patch("bot.processor.create_todoist_task", return_value="t-1")

    process_item(
        item,
        anthropic_client=MagicMock(),
        dropbox_client=MagicMock(),
        openai_api_key="x",
        todoist_token="t",
        todoist_projects={"acute": "1000001"},
        vault_root="/personal-os",
        system_blocks=[{"type": "text", "text": "s"}],
    )

    refine_mock.assert_not_called()


def test_process_item_skips_vision_when_design_but_no_media(mocker):
    """A design+image classification with no media_dropbox_path can't run vision."""
    item = {
        "id": "item-vis-3",
        "media_type": "text",
        "raw_text": "design inspo I forgot to attach",
        "media_dropbox_path": None,
    }
    mocker.patch("bot.processor.classify_item", return_value=_fake_classify_response(
        "design", "image", ["x"], "y"
    ))
    refine_mock = mocker.patch("bot.processor.refine_with_vision")
    mocker.patch("bot.processor.write_obsidian_note", return_value="design/x.md")

    process_item(
        item,
        anthropic_client=MagicMock(),
        dropbox_client=MagicMock(),
        openai_api_key="x",
        todoist_token="t",
        todoist_projects={},
        vault_root="/personal-os",
        system_blocks=[{"type": "text", "text": "s"}],
    )

    refine_mock.assert_not_called()


def test_process_item_keeps_text_classification_when_vision_returns_none(mocker):
    """If vision can't parse the response, the text-only classification is preserved."""
    item = {
        "id": "item-vis-4",
        "media_type": "image",
        "raw_text": "design",
        "media_dropbox_path": "/personal-os/_inbox/_attachments/item-vis-4.jpg",
    }
    mocker.patch("bot.processor.classify_item", return_value=_fake_classify_response(
        "design", "image", ["text-tag"], "text summary"
    ))
    mocker.patch("bot.processor._download_dropbox_bytes", return_value=b"fake")
    mocker.patch("bot.processor.refine_with_vision", return_value=None)
    mocker.patch("bot.processor.write_obsidian_note", return_value="design/x.md")
    mocker.patch("bot.processor.move_dropbox_media", return_value="/personal-os/design/_attachments/item-vis-4.jpg")

    result = process_item(
        item,
        anthropic_client=MagicMock(),
        dropbox_client=MagicMock(),
        openai_api_key="x",
        todoist_token="t",
        todoist_projects={},
        vault_root="/personal-os",
        system_blocks=[{"type": "text", "text": "s"}],
    )

    # Text-only fields preserved
    assert result["classification"]["tags"] == ["text-tag"]
    assert result["classification"]["summary"] == "text summary"
    # No vision cost added
    assert result["api_cost_cents"] == 1


def test_process_item_continues_when_vision_raises(mocker):
    """Vision exception (e.g., Dropbox 404 on download) must not fail the item."""
    item = {
        "id": "item-vis-5",
        "media_type": "image",
        "raw_text": "design",
        "media_dropbox_path": "/personal-os/_inbox/_attachments/item-vis-5.jpg",
    }
    mocker.patch("bot.processor.classify_item", return_value=_fake_classify_response(
        "design", "image", ["text-tag"], "text summary"
    ))
    mocker.patch("bot.processor._download_dropbox_bytes", side_effect=RuntimeError("dropbox 404"))
    refine_mock = mocker.patch("bot.processor.refine_with_vision")
    mocker.patch("bot.processor.write_obsidian_note", return_value="design/x.md")
    mocker.patch("bot.processor.move_dropbox_media", return_value="/personal-os/design/_attachments/item-vis-5.jpg")

    result = process_item(
        item,
        anthropic_client=MagicMock(),
        dropbox_client=MagicMock(),
        openai_api_key="x",
        todoist_token="t",
        todoist_projects={},
        vault_root="/personal-os",
        system_blocks=[{"type": "text", "text": "s"}],
    )

    refine_mock.assert_not_called()
    assert result["status"] in ("processed", "needs_review")
    assert result["classification"]["tags"] == ["text-tag"]


def test_run_batch_fans_out_multi_image_scrape_into_additional_items(mocker):
    """Tweet with 3 images → 1 original item + 2 fan-out items, all processed in same batch."""
    original = {
        "id": "i1",
        "source": "telegram",
        "source_message_id": "100",
        "media_type": "link",
        "raw_text": "https://x.com/u/status/multi",
        "media_dropbox_path": None,
    }

    fanout_rows = [{"id": "i2"}, {"id": "i3"}]

    mock_supabase = MagicMock()
    # insert() returns the new items rows when fan-out runs
    insert_calls = iter(fanout_rows)
    mock_supabase.table.return_value.insert.return_value.execute.side_effect = lambda: MagicMock(data=[next(insert_calls)])

    mocker.patch("bot.processor.fetch_pending_items", return_value=[original])
    mocker.patch("bot.processor.fetch_recent_corrections", return_value=[])
    mocker.patch("bot.processor.insert_run", return_value={"id": "run-1"})
    mocker.patch("bot.processor.update_classified")
    mocker.patch("bot.processor._download_image_to_dropbox", return_value="/_inbox/x.jpg")

    # process_item is called 3 times: original + 2 fan-out items.
    # Only the first has scrape_info (the others have source_post_id pre-set on the row).
    process_mock = mocker.patch(
        "bot.processor.process_item",
        side_effect=[
            {
                "status": "processed",
                "classification": {"project": "design", "type": "image", "tags": [], "summary": "img1", "confidence": 0.9},
                "obsidian_path": "design/1.md", "todoist_task_id": None, "api_cost_cents": 1,
                "source_post_id": "sp-1",
                "media_dropbox_path": "/_inbox/x.jpg",
                "scrape_info": {
                    "source_post_id": "sp-1",
                    "image_urls": ["a.jpg", "b.jpg", "c.jpg"],
                    "post_text": "design thread",
                    "midjourney_params": {},
                },
                "error": None,
            },
            {
                "status": "processed",
                "classification": {"project": "design", "type": "image", "tags": [], "summary": "img2", "confidence": 0.9},
                "obsidian_path": "design/2.md", "todoist_task_id": None, "api_cost_cents": 1,
                "source_post_id": None, "media_dropbox_path": None, "scrape_info": None,
                "error": None,
            },
            {
                "status": "processed",
                "classification": {"project": "design", "type": "image", "tags": [], "summary": "img3", "confidence": 0.9},
                "obsidian_path": "design/3.md", "todoist_task_id": None, "api_cost_cents": 1,
                "source_post_id": None, "media_dropbox_path": None, "scrape_info": None,
                "error": None,
            },
        ],
    )

    result = run_batch(
        supabase_client=mock_supabase,
        anthropic_client=MagicMock(),
        dropbox_client_factory=lambda: MagicMock(),
        openai_api_key="x",
        todoist_token="t",
        todoist_projects={},
        vault_root="/personal-os",
        rules_md="",
        tag_vocab_md="",
        apify_api_token="apify_token",
        apify_tweet_scraper_actor="xquik~x-tweet-scraper",
    )

    assert process_mock.call_count == 3, "expected original + 2 fan-out items processed"
    assert result["items_processed"] == 3


def test_run_batch_does_not_fan_out_when_single_image(mocker):
    original = {
        "id": "i1",
        "source": "telegram",
        "source_message_id": "100",
        "media_type": "link",
        "raw_text": "https://x.com/u/status/single",
        "media_dropbox_path": None,
    }

    mocker.patch("bot.processor.fetch_pending_items", return_value=[original])
    mocker.patch("bot.processor.fetch_recent_corrections", return_value=[])
    mocker.patch("bot.processor.insert_run", return_value={"id": "run-1"})
    mocker.patch("bot.processor.update_classified")
    download_mock = mocker.patch("bot.processor._download_image_to_dropbox")

    process_mock = mocker.patch(
        "bot.processor.process_item",
        return_value={
            "status": "processed",
            "classification": {"project": "design", "type": "image", "tags": [], "summary": "img1", "confidence": 0.9},
            "obsidian_path": "design/1.md", "todoist_task_id": None, "api_cost_cents": 1,
            "source_post_id": "sp-1", "media_dropbox_path": "/_inbox/x.jpg",
            "scrape_info": {
                "source_post_id": "sp-1",
                "image_urls": ["a.jpg"],
                "post_text": "single image post",
                "midjourney_params": {},
            },
            "error": None,
        },
    )

    result = run_batch(
        supabase_client=MagicMock(),
        anthropic_client=MagicMock(),
        dropbox_client_factory=lambda: MagicMock(),
        openai_api_key="x",
        todoist_token="t",
        todoist_projects={},
        vault_root="/personal-os",
        rules_md="",
        tag_vocab_md="",
        apify_api_token="apify_token",
    )

    assert process_mock.call_count == 1
    assert result["items_processed"] == 1
    download_mock.assert_not_called()  # no fan-out → no extra downloads


# ---------------------------------------------------------------------------
# Session 5 — daily cost cap
# ---------------------------------------------------------------------------


def _stub_processed_result(cost_cents: int, idx: int) -> dict:
    return {
        "status": "processed",
        "classification": {
            "project": "personal",
            "type": "idea",
            "tags": [],
            "summary": f"item-{idx}",
            "confidence": 0.9,
        },
        "obsidian_path": f"personal/item-{idx}.md",
        "todoist_task_id": None,
        "api_cost_cents": cost_cents,
        "error": None,
    }


def test_run_batch_halts_when_cap_reached(mocker):
    pending = [
        {"id": f"i{n}", "media_type": "text", "raw_text": f"item {n}", "media_dropbox_path": None}
        for n in range(5)
    ]
    mocker.patch("bot.processor.fetch_pending_items", return_value=pending)
    mocker.patch("bot.processor.fetch_recent_corrections", return_value=[])
    process_mock = mocker.patch(
        "bot.processor.process_item",
        side_effect=[_stub_processed_result(50, i) for i in range(5)],
    )
    update_mock = mocker.patch("bot.processor.update_classified")
    mocker.patch("bot.processor.insert_run", return_value={"id": "run-x"})

    result = run_batch(
        supabase_client=MagicMock(),
        anthropic_client=MagicMock(),
        dropbox_client_factory=lambda: MagicMock(),
        openai_api_key="x",
        todoist_token="t",
        todoist_projects={},
        vault_root="/personal-os",
        rules_md="",
        tag_vocab_md="",
        daily_cap_cents=150,
        today_already_spent_cents=0,
    )

    # 3 items × 50¢ = 150¢ — cap hits before item 4 starts.
    assert process_mock.call_count == 3
    assert update_mock.call_count == 3
    assert result["items_processed"] == 3
    assert result["total_cost_cents"] == 150
    assert result["halted_at_cap"] is True
    assert result["items_remaining_pending"] == 2


def test_run_batch_unlimited_when_no_cap(mocker):
    pending = [
        {"id": f"i{n}", "media_type": "text", "raw_text": f"item {n}", "media_dropbox_path": None}
        for n in range(5)
    ]
    mocker.patch("bot.processor.fetch_pending_items", return_value=pending)
    mocker.patch("bot.processor.fetch_recent_corrections", return_value=[])
    mocker.patch(
        "bot.processor.process_item",
        side_effect=[_stub_processed_result(50, i) for i in range(5)],
    )
    mocker.patch("bot.processor.update_classified")
    mocker.patch("bot.processor.insert_run", return_value={"id": "run-x"})

    result = run_batch(
        supabase_client=MagicMock(),
        anthropic_client=MagicMock(),
        dropbox_client_factory=lambda: MagicMock(),
        openai_api_key="x",
        todoist_token="t",
        todoist_projects={},
        vault_root="/personal-os",
        rules_md="",
        tag_vocab_md="",
        # No daily_cap_cents kwarg → unlimited (the /process path).
    )

    assert result["items_processed"] == 5
    assert result["halted_at_cap"] is False
    assert result["items_remaining_pending"] == 0


def test_run_batch_respects_already_spent_today(mocker):
    pending = [
        {"id": f"i{n}", "media_type": "text", "raw_text": f"item {n}", "media_dropbox_path": None}
        for n in range(5)
    ]
    mocker.patch("bot.processor.fetch_pending_items", return_value=pending)
    mocker.patch("bot.processor.fetch_recent_corrections", return_value=[])
    process_mock = mocker.patch(
        "bot.processor.process_item",
        side_effect=[_stub_processed_result(50, i) for i in range(5)],
    )
    mocker.patch("bot.processor.update_classified")
    mocker.patch("bot.processor.insert_run", return_value={"id": "run-x"})

    result = run_batch(
        supabase_client=MagicMock(),
        anthropic_client=MagicMock(),
        dropbox_client_factory=lambda: MagicMock(),
        openai_api_key="x",
        todoist_token="t",
        todoist_projects={},
        vault_root="/personal-os",
        rules_md="",
        tag_vocab_md="",
        daily_cap_cents=150,
        today_already_spent_cents=120,
    )

    # 120 already + first item 50 = 170 ≥ 150 → halt after item 1.
    assert process_mock.call_count == 1
    assert result["items_processed"] == 1
    assert result["total_cost_cents"] == 50
    assert result["halted_at_cap"] is True
    assert result["items_remaining_pending"] == 4


def test_run_batch_does_not_halt_when_below_cap(mocker):
    pending = [
        {"id": f"i{n}", "media_type": "text", "raw_text": f"item {n}", "media_dropbox_path": None}
        for n in range(3)
    ]
    mocker.patch("bot.processor.fetch_pending_items", return_value=pending)
    mocker.patch("bot.processor.fetch_recent_corrections", return_value=[])
    mocker.patch(
        "bot.processor.process_item",
        side_effect=[_stub_processed_result(5, i) for i in range(3)],
    )
    mocker.patch("bot.processor.update_classified")
    mocker.patch("bot.processor.insert_run", return_value={"id": "run-x"})

    result = run_batch(
        supabase_client=MagicMock(),
        anthropic_client=MagicMock(),
        dropbox_client_factory=lambda: MagicMock(),
        openai_api_key="x",
        todoist_token="t",
        todoist_projects={},
        vault_root="/personal-os",
        rules_md="",
        tag_vocab_md="",
        daily_cap_cents=200,
        today_already_spent_cents=0,
    )

    assert result["items_processed"] == 3
    assert result["halted_at_cap"] is False
    assert result["items_remaining_pending"] == 0


def test_is_video_url_detects_known_extensions():
    from bot.processor import _is_video_url

    assert _is_video_url("https://video.twimg.com/abc.mp4") is True
    assert _is_video_url("https://example.com/clip.MOV") is True
    assert _is_video_url("https://cdn.example.com/v.webm") is True
    assert _is_video_url("https://video.twimg.com/abc.mp4?tag=12") is True
    assert _is_video_url("https://cdn.example.com/v.mp4#t=30") is True

    assert _is_video_url("https://pbs.twimg.com/media/photo.jpg") is False
    assert _is_video_url("https://video.twimg.com/playlist.m3u8") is False
    assert _is_video_url("https://pbs.twimg.com/photo.jpg?name=large") is False
    assert _is_video_url("") is False
    assert _is_video_url(None) is False


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
    video_download.assert_called_once()
    assert video_download.call_args.args[1] == "https://video.twimg.com/clip.mp4"
    image_download.assert_not_called()
    assert item["media_type"] == "video"


def test_fan_out_skips_video_urls(mocker):
    from bot.processor import _fan_out_additional_items_from_scrape

    inserted_rows = [{"id": "child-B"}, {"id": "child-C"}]  # C entry lets .mp4 reach image_download when guard is absent
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
    assert image_download.call_count == 1
    assert image_download.call_args.args[1] == "https://pbs.twimg.com/media/B.jpg"
    # Ensure the .mp4 URL was never passed to the image helper, even by exception masking.
    all_call_urls = [c.args[1] for c in image_download.call_args_list]
    assert "https://video.twimg.com/clip.mp4" not in all_call_urls


# ---------------------------------------------------------------------------
# Session 6b — Task 2: handle_x_url surfaces video_durations
# ---------------------------------------------------------------------------


def test_handle_x_url_fresh_scrape_returns_video_durations(mocker):
    from bot.processor import handle_x_url
    from bot.scraper import ScrapeResult

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
        MagicMock(), "https://x.com/u/status/1",
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
    # Cached short-circuit must not call the scraper or insert helpers.
    fetch_tweet_mock = mocker.patch("bot.processor.scraper.fetch_tweet")
    insert_mock = mocker.patch("bot.processor.insert_source_post")

    result = handle_x_url(
        MagicMock(), "https://x.com/u/status/1",
        token="t", actor="xquik~x-tweet-scraper",
    )

    assert result["source_post_id"] == "sp-cached"
    # Cached path: no durations available (we don't persist them). Defaults to {}.
    assert result["video_durations"] == {}
    fetch_tweet_mock.assert_not_called()
    insert_mock.assert_not_called()


# ---------------------------------------------------------------------------
# _is_long_video
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Session 6b — Task 4: _tutorial_classification helper
# ---------------------------------------------------------------------------


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
    # confidence stays well above NEEDS_REVIEW_THRESHOLD so the item
    # bypasses triage — there's nothing to review on a hardcoded route.
    assert result["confidence"] >= 0.95
    # Free — no Haiku call.
    assert result["_cost_cents"] == 0
    # Summary is a watch-cue derived from the tweet text.
    assert "Watch" in result["summary"]
    assert "Next.js dashboard" in result["summary"]
    # _tutorial_video_url is consumed by the Obsidian writer (Task 7) to render
    # a clickable Video section. Locked in here so a refactor catches a rename.
    assert result["_tutorial_video_url"] == "https://video.twimg.com/clip.mp4"


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


# ---------------------------------------------------------------------------
# Session 6b — Task 5: Phase-A routes long videos to the tutorial path
# ---------------------------------------------------------------------------


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

    # Payload must NOT be empty/None — an empty payload is what causes the
    # classifier to hallucinate a summary from nothing.
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


def test_transcription_failure_classification_marks_needs_review():
    from bot.processor import (
        NEEDS_REVIEW_THRESHOLD,
        _transcription_failure_classification,
    )

    result = _transcription_failure_classification(error_text="401 Unauthorized")

    assert result["confidence"] < NEEDS_REVIEW_THRESHOLD
    assert result["project"] == "personal"  # default bucket — Ryan refiles via triage
    assert result["type"] == "voice"
    assert result["_cost_cents"] == 0  # no Haiku call
    assert "transcription failed" in result["summary"].lower()
    assert "401" in result["summary"]
    assert "transcription-failed" in result["tags"]


def test_transcription_failure_classification_truncates_long_error():
    from bot.processor import (
        _TRANSCRIPTION_ERROR_SUMMARY_MAX,
        _transcription_failure_classification,
    )

    long_error = "x" * 500
    result = _transcription_failure_classification(error_text=long_error)
    assert len(result["summary"]) <= _TRANSCRIPTION_ERROR_SUMMARY_MAX


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
    mocker.patch("bot.processor.move_dropbox_media", return_value=None)

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
    mocker.patch("bot.processor.move_dropbox_media", return_value=None)
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
