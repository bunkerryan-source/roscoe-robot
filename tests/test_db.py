from unittest.mock import MagicMock

from bot.db import (
    fetch_pending_items,
    fetch_recent_corrections,
    insert_item,
    insert_run,
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
        {"original_class": {}, "corrected_class": {}},
    ]

    result = fetch_recent_corrections(mock_client, limit=30)

    mock_client.table.assert_called_with("corrections")
    mock_client.table.return_value.select.return_value.order.assert_called_with(
        "created_at", desc=True
    )
    assert len(result) == 1
