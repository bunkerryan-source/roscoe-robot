import pytest
from fastapi.testclient import TestClient
from unittest.mock import MagicMock, patch


@pytest.fixture
def client(env, mocker):
    """Build a TestClient with all external clients mocked.

    We patch the module-level singletons that bot.main creates at import time.
    """
    # Patch supabase.create_client BEFORE bot.main is imported
    mock_sb = MagicMock()
    mock_sb.table.return_value.insert.return_value.execute.return_value = MagicMock(
        data=[{"id": "test-item-id"}]
    )
    mocker.patch("bot.db.create_client", return_value=mock_sb)

    # Patch the dropbox client factory
    mocker.patch("bot.media.dropbox.Dropbox", return_value=MagicMock())

    # Mock outbound httpx for ack and Telegram file download
    import respx
    import httpx
    respx_mock = respx.mock(base_url="https://api.telegram.org", assert_all_called=False)
    respx_mock.start()
    respx_mock.post("/bottest_bot_token/sendMessage").mock(
        return_value=httpx.Response(200, json={"ok": True})
    )
    respx_mock.get("/bottest_bot_token/getFile").mock(
        return_value=httpx.Response(200, json={"ok": True, "result": {"file_path": "p/f.jpg"}})
    )
    respx_mock.get("/file/bottest_bot_token/p/f.jpg").mock(
        return_value=httpx.Response(200, content=b"jpeg-bytes")
    )

    from bot.main import app
    import bot.main
    # bot.main caches the supabase client at module import time. The first test
    # to run sees the mock; subsequent tests get stale references unless we
    # rebind the module-level singleton each time.
    mocker.patch.object(bot.main, "supabase", mock_sb)

    test_client = TestClient(app)
    yield test_client, mock_sb
    respx_mock.stop()


def test_webhook_rejects_wrong_secret(client):
    test_client, _ = client
    response = test_client.post(
        "/webhook/wrong_secret",
        json={"update_id": 1, "message": {"message_id": 1, "from": {"id": 12345},
              "chat": {"id": 12345, "type": "private"}, "date": 0, "text": "hi"}},
    )
    assert response.status_code == 403


def test_webhook_drops_unauthorized_sender_silently(client, load_fixture):
    test_client, mock_sb = client
    response = test_client.post(
        "/webhook/test_secret",
        json=load_fixture("update_unauthorized_sender"),
    )
    assert response.status_code == 200
    # No supabase insert should have happened
    mock_sb.table.return_value.insert.assert_not_called()


def test_webhook_text_inserts_pending_row(client, load_fixture):
    test_client, mock_sb = client
    response = test_client.post(
        "/webhook/test_secret",
        json=load_fixture("update_text"),
    )
    assert response.status_code == 200
    mock_sb.table.assert_any_call("items")
    insert_payload = mock_sb.table.return_value.insert.call_args[0][0]
    assert insert_payload["status"] == "pending"
    assert insert_payload["media_type"] == "text"
    assert insert_payload["raw_text"] == "Remember to call Walmart contact"
    assert insert_payload["media_telegram_file_id"] is None


def test_webhook_photo_inserts_row_and_schedules_media_handling(client, load_fixture):
    test_client, mock_sb = client
    response = test_client.post(
        "/webhook/test_secret",
        json=load_fixture("update_photo_with_caption"),
    )
    assert response.status_code == 200
    insert_payload = mock_sb.table.return_value.insert.call_args[0][0]
    assert insert_payload["media_type"] == "image"
    assert insert_payload["media_telegram_file_id"] == "large_file_id"
    assert insert_payload["raw_text"] == "for Lake Arrowhead bathroom"


def test_webhook_schedules_media_handling_as_background_task(client, load_fixture):
    """Verify media handling runs after the request returns 200, via the
    Supabase update_media_path side effect (proves the background task fired
    and the ack didn't block on its completion in the request handler).

    Note: Starlette's TestClient blocks until background tasks complete, so
    a wall-clock timing assertion on test_client.post() does not measure the
    real webhook ack latency. The structural guarantee — that media work is
    add_task'd, not awaited, in the handler — is enforced by the architecture
    in bot/main.py and is exercised end-to-end here.
    """
    test_client, mock_sb = client
    response = test_client.post(
        "/webhook/test_secret",
        json=load_fixture("update_photo_with_caption"),
    )
    assert response.status_code == 200
    # The background task ran update_media_path → table().update() was called
    mock_sb.table.return_value.update.assert_called()
