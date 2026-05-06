from unittest.mock import MagicMock
from bot.db import insert_item, update_media_path


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
