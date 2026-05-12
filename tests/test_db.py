from unittest.mock import MagicMock

from bot.db import (
    fetch_items_for_summary,
    fetch_needs_review_items,
    fetch_pending_items,
    fetch_recent_corrections,
    get_source_post_by_id,
    get_source_post_by_url,
    insert_correction,
    insert_item,
    insert_run,
    insert_source_post,
    update_classified,
    update_media_path,
)


def _mock_supabase_with_inserted_row(row: dict):
    """Builds a mock that mimics: client.table("items").insert(...).execute()."""
    client = MagicMock()
    execute_result = MagicMock()
    execute_result.data = [row]
    client.table.return_value.insert.return_value.execute.return_value = execute_result
    return client


def test_insert_item_returns_row_with_id():
    expected = {
        "id": "abc-123",
        "status": "pending",
        "source": "telegram",
        "source_message_id": "42",
        "raw_text": "hello",
        "media_type": "text",
        "media_telegram_file_id": None,
    }
    client = _mock_supabase_with_inserted_row(expected)

    row = insert_item(
        client,
        source_message_id="42",
        raw_text="hello",
        media_type="text",
        media_telegram_file_id=None,
    )

    assert row["id"] == "abc-123"
    client.table.assert_called_once_with("items")
    insert_call = client.table.return_value.insert.call_args[0][0]
    assert insert_call["status"] == "pending"
    assert insert_call["source_message_id"] == "42"
    assert insert_call["media_type"] == "text"


def test_insert_item_for_image_includes_file_id():
    client = _mock_supabase_with_inserted_row({"id": "x"})
    insert_item(
        client,
        source_message_id="99",
        raw_text="caption",
        media_type="image",
        media_telegram_file_id="AgAC...",
    )
    payload = client.table.return_value.insert.call_args[0][0]
    assert payload["media_telegram_file_id"] == "AgAC..."
    assert payload["raw_text"] == "caption"


def test_update_media_path_calls_supabase():
    client = MagicMock()
    update_media_path(client, "abc-123", "/personal-os-inbox/2026-05-05/abc-123.jpg")
    client.table.assert_called_once_with("items")
    client.table.return_value.update.assert_called_once_with(
        {"media_dropbox_path": "/personal-os-inbox/2026-05-05/abc-123.jpg"}
    )
    client.table.return_value.update.return_value.eq.assert_called_once_with("id", "abc-123")


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
        {"correction_type": "project", "original_value": {}, "corrected_value": {}, "note": None},
    ]

    result = fetch_recent_corrections(mock_client, limit=30)

    mock_client.table.assert_called_with("corrections")
    select_call = mock_client.table.return_value.select.call_args.args[0]
    assert "correction_type" in select_call
    assert "original_value" in select_call
    assert "corrected_value" in select_call
    mock_client.table.return_value.select.return_value.order.assert_called_with(
        "created_at", desc=True
    )
    assert len(result) == 1


def test_get_source_post_by_url_returns_row_when_present():
    mock_client = MagicMock()
    row = {"id": "sp-1", "source_url": "https://x.com/u/status/1", "image_urls": ["a.jpg"]}
    mock_client.table.return_value.select.return_value.eq.return_value.limit.return_value.execute.return_value.data = [row]

    result = get_source_post_by_url(mock_client, "https://x.com/u/status/1")

    mock_client.table.assert_called_with("source_posts")
    mock_client.table.return_value.select.return_value.eq.assert_called_with(
        "source_url", "https://x.com/u/status/1"
    )
    assert result == row


def test_get_source_post_by_url_returns_none_when_missing():
    mock_client = MagicMock()
    mock_client.table.return_value.select.return_value.eq.return_value.limit.return_value.execute.return_value.data = []

    result = get_source_post_by_url(mock_client, "https://x.com/u/status/missing")

    assert result is None


def test_get_source_post_by_id_returns_row_when_present():
    mock_client = MagicMock()
    row = {"id": "sp-7", "source_url": "https://x.com/u/status/7", "post_text": "hello", "image_urls": ["a.jpg"]}
    mock_client.table.return_value.select.return_value.eq.return_value.limit.return_value.execute.return_value.data = [row]

    result = get_source_post_by_id(mock_client, "sp-7")

    mock_client.table.assert_called_with("source_posts")
    mock_client.table.return_value.select.return_value.eq.assert_called_with("id", "sp-7")
    assert result == row


def test_get_source_post_by_id_returns_none_when_missing():
    mock_client = MagicMock()
    mock_client.table.return_value.select.return_value.eq.return_value.limit.return_value.execute.return_value.data = []

    result = get_source_post_by_id(mock_client, "sp-missing")

    assert result is None


def test_fetch_items_for_summary_filters_on_processed_at_window():
    mock_client = MagicMock()
    chain = mock_client.table.return_value.select.return_value
    chain.gte.return_value.lt.return_value.order.return_value.execute.return_value.data = [
        {"id": "a", "project": "design", "type": "image", "status": "processed"},
        {"id": "b", "project": "acute", "type": "todo", "status": "processed"},
    ]

    result = fetch_items_for_summary(
        mock_client,
        since="2026-05-11T00:00:00+00:00",
        until="2026-05-12T00:00:00+00:00",
    )

    mock_client.table.assert_called_with("items")
    select_arg = mock_client.table.return_value.select.call_args.args[0]
    for col in ("project", "type", "status", "summary", "api_cost_cents", "processed_at"):
        assert col in select_arg
    chain.gte.assert_called_with("processed_at", "2026-05-11T00:00:00+00:00")
    chain.gte.return_value.lt.assert_called_with("processed_at", "2026-05-12T00:00:00+00:00")
    assert len(result) == 2


def test_insert_source_post_returns_inserted_id_and_writes_all_fields():
    from datetime import datetime, timezone

    mock_client = MagicMock()
    mock_client.table.return_value.insert.return_value.execute.return_value.data = [{"id": "sp-new"}]
    posted = datetime(2025, 6, 5, 20, 19, 24, tzinfo=timezone.utc)

    new_id = insert_source_post(
        mock_client,
        source="x",
        source_url="https://x.com/u/status/1",
        post_text="hi --sref 999",
        author_handle="@u",
        author_name="U",
        posted_at=posted,
        image_urls=["a.jpg", "b.jpg"],
        midjourney_params={"sref": "999"},
        raw_response={"id": "1"},
    )

    assert new_id == "sp-new"
    mock_client.table.assert_called_with("source_posts")
    payload = mock_client.table.return_value.insert.call_args.args[0]
    assert payload["source"] == "x"
    assert payload["source_url"] == "https://x.com/u/status/1"
    assert payload["post_text"] == "hi --sref 999"
    assert payload["author_handle"] == "@u"
    assert payload["posted_at"] == posted.isoformat()
    assert payload["image_urls"] == ["a.jpg", "b.jpg"]
    assert payload["midjourney_params"] == {"sref": "999"}
    assert payload["raw_scraper_response"] == {"id": "1"}


def test_insert_source_post_serializes_none_posted_at_as_none():
    mock_client = MagicMock()
    mock_client.table.return_value.insert.return_value.execute.return_value.data = [{"id": "sp-2"}]

    insert_source_post(
        mock_client,
        source="x",
        source_url="https://x.com/u/status/2",
        post_text="",
        author_handle=None,
        author_name=None,
        posted_at=None,
        image_urls=[],
        midjourney_params={},
        raw_response={},
    )

    payload = mock_client.table.return_value.insert.call_args.args[0]
    assert payload["posted_at"] is None


def test_insert_correction_writes_row_and_returns_id():
    mock_client = MagicMock()
    mock_client.table.return_value.insert.return_value.execute.return_value.data = [{"id": "c-1"}]

    new_id = insert_correction(
        mock_client,
        item_id="item-1",
        correction_type="project",
        original_value={"project": "personal"},
        corrected_value={"project": "design"},
        note="Tweets from @sdsmith always design",
    )

    assert new_id == "c-1"
    mock_client.table.assert_called_with("corrections")
    payload = mock_client.table.return_value.insert.call_args.args[0]
    assert payload["item_id"] == "item-1"
    assert payload["correction_type"] == "project"
    assert payload["original_value"] == {"project": "personal"}
    assert payload["corrected_value"] == {"project": "design"}
    assert payload["note"] == "Tweets from @sdsmith always design"


def test_fetch_needs_review_items_filters_by_status_and_orders_oldest_first():
    mock_client = MagicMock()
    chain = mock_client.table.return_value.select.return_value.eq.return_value.order.return_value
    chain.limit.return_value.execute.return_value.data = [
        {"id": "a", "status": "needs_review", "project": "design"},
        {"id": "b", "status": "needs_review", "project": "claude-build"},
    ]

    result = fetch_needs_review_items(mock_client, limit=20)

    mock_client.table.assert_called_with("items")
    select_arg = mock_client.table.return_value.select.call_args.args[0]
    for col in ("id", "raw_text", "media_type", "media_dropbox_path", "project", "type", "tags", "summary"):
        assert col in select_arg
    mock_client.table.return_value.select.return_value.eq.assert_called_with("status", "needs_review")
    mock_client.table.return_value.select.return_value.eq.return_value.order.assert_called_with(
        "processed_at", desc=False
    )
    chain.limit.assert_called_with(20)
    assert len(result) == 2


def test_fetch_needs_review_items_returns_empty_when_no_rows():
    mock_client = MagicMock()
    mock_client.table.return_value.select.return_value.eq.return_value.order.return_value.limit.return_value.execute.return_value.data = []

    result = fetch_needs_review_items(mock_client)

    assert result == []


def test_insert_correction_allows_null_values_and_note():
    mock_client = MagicMock()
    mock_client.table.return_value.insert.return_value.execute.return_value.data = [{"id": "c-2"}]

    insert_correction(
        mock_client,
        item_id="item-2",
        correction_type="discard",
        original_value=None,
        corrected_value=None,
    )

    payload = mock_client.table.return_value.insert.call_args.args[0]
    assert payload["original_value"] is None
    assert payload["corrected_value"] is None
    assert payload["note"] is None
