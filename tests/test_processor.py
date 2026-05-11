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
