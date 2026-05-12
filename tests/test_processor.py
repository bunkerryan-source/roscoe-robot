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
