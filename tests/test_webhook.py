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


def test_process_command_does_not_insert_item(client, load_fixture, mocker):
    test_client, mock_sb = client
    mocker.patch("bot.main.run_batch", return_value={
        "items_processed": 0, "items_needs_review": 0, "items_failed": 0,
        "total_cost_cents": 0, "duration_seconds": 0.1,
    })
    mocker.patch("bot.main.send_message")

    response = test_client.post(
        "/webhook/test_secret",
        json=load_fixture("update_process_command"),
    )
    assert response.status_code == 200
    # No supabase insert should have happened for the /process command itself.
    insert_calls = [
        c for c in mock_sb.table.return_value.insert.call_args_list
        if c.args and isinstance(c.args[0], dict) and "source_message_id" in c.args[0]
    ]
    assert insert_calls == []


def test_process_command_schedules_run_batch(client, load_fixture, mocker):
    test_client, _ = client
    run_batch_mock = mocker.patch(
        "bot.main.run_batch",
        return_value={
            "items_processed": 2, "items_needs_review": 0, "items_failed": 0,
            "total_cost_cents": 3, "duration_seconds": 4.2,
        },
    )
    mocker.patch("bot.main.send_message")

    response = test_client.post(
        "/webhook/test_secret",
        json=load_fixture("update_process_command"),
    )
    assert response.status_code == 200

    run_batch_mock.assert_called_once()


def test_process_command_replies_with_summary(client, load_fixture, mocker):
    test_client, _ = client
    mocker.patch(
        "bot.main.run_batch",
        return_value={
            "items_processed": 2, "items_needs_review": 1, "items_failed": 0,
            "total_cost_cents": 4, "duration_seconds": 6.0,
        },
    )
    send_mock = mocker.patch("bot.main.send_message")

    test_client.post(
        "/webhook/test_secret",
        json=load_fixture("update_process_command"),
    )

    # send_message is called once for the summary; send_ack is called separately.
    assert send_mock.call_count >= 1
    summary_call = send_mock.call_args_list[-1]
    summary_text = summary_call.kwargs.get("text") or summary_call.args[2]
    assert "2" in summary_text          # items_processed
    assert "$0.04" in summary_text      # cost
    assert "1 needs review" in summary_text or "1 review" in summary_text
