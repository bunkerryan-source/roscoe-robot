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
    respx_mock.post("/bottest_bot_token/answerCallbackQuery").mock(
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


def test_webhook_routes_callback_query_to_dispatcher(client, mocker):
    test_client, _ = client
    handler = mocker.patch("bot.main._handle_triage_callback")
    response = test_client.post(
        "/webhook/test_secret",
        json={
            "update_id": 1,
            "callback_query": {
                "id": "cb-1",
                "from": {"id": 12345},
                "message": {"chat": {"id": 12345}, "message_id": 99},
                "data": "keep:item-uuid",
            },
        },
    )
    assert response.status_code == 200
    handler.assert_called_once()
    kwargs = handler.call_args.kwargs
    assert kwargs["action"] == "keep"
    assert kwargs["payload"] == "item-uuid"
    assert kwargs["callback_id"] == "cb-1"
    assert kwargs["chat_id"] == 12345
    assert kwargs["message_id"] == 99


def test_webhook_drops_callback_query_from_unauthorized_sender(client, mocker):
    test_client, _ = client
    handler = mocker.patch("bot.main._handle_triage_callback")
    response = test_client.post(
        "/webhook/test_secret",
        json={
            "update_id": 2,
            "callback_query": {
                "id": "cb-2",
                "from": {"id": 99999},
                "message": {"chat": {"id": 99999}, "message_id": 1},
                "data": "discard:x",
            },
        },
    )
    assert response.status_code == 200
    handler.assert_not_called()


def test_review_start_sends_first_needs_review_item_with_action_keyboard(client, mocker):
    test_client, mock_sb = client
    mock_sb.table.return_value.select.return_value.eq.return_value.order.return_value.limit.return_value.execute.return_value.data = [
        {
            "id": "item-1",
            "raw_text": "interesting hero treatment",
            "media_type": "image",
            "media_dropbox_path": "/inbox/x.jpg",
            "project": "design",
            "type": "image",
            "tags": ["hero"],
            "summary": "blue hero with rough texture",
            "source_post_id": None,
        },
        {"id": "item-2"},
    ]
    send_mock = mocker.patch("bot.main.send_message")

    response = test_client.post(
        "/webhook/test_secret",
        json={
            "update_id": 10,
            "callback_query": {
                "id": "cb-10",
                "from": {"id": 12345},
                "message": {"chat": {"id": 12345}, "message_id": 50},
                "data": "review:start",
            },
        },
    )

    assert response.status_code == 200
    send_mock.assert_called_once()
    args, kwargs = send_mock.call_args
    assert args[0] == 12345
    assert args[1] is None  # no reply_to anchor — fresh message
    text = args[2]
    assert "Review 1 of 2" in text
    assert "design" in text
    assert "interesting hero treatment" in text
    reply_markup = kwargs["reply_markup"]
    flat = [b for row in reply_markup["inline_keyboard"] for b in row]
    callbacks = [b["callback_data"] for b in flat]
    assert "keep:item-1" in callbacks
    assert "refile:item-1" in callbacks


def _stub_fetch_item(mock_sb, row: dict | None):
    """Wire mock_sb so fetch_item returns the given row (or None)."""
    data = [row] if row is not None else []
    mock_sb.table.return_value.select.return_value.eq.return_value.limit.return_value.execute.return_value.data = data


def test_callback_keep_marks_item_processed_and_writes_correction(client, mocker):
    test_client, mock_sb = client
    _stub_fetch_item(mock_sb, {
        "id": "item-x", "project": "design", "type": "image",
        "tags": ["hero"], "status": "needs_review",
    })
    send_mock = mocker.patch("bot.main.send_message")

    response = test_client.post(
        "/webhook/test_secret",
        json={
            "update_id": 20,
            "callback_query": {
                "id": "cb-keep",
                "from": {"id": 12345},
                "message": {"chat": {"id": 12345}, "message_id": 200},
                "data": "keep:item-x",
            },
        },
    )

    assert response.status_code == 200
    update_payload = mock_sb.table.return_value.update.call_args.args[0]
    assert update_payload == {"status": "processed"}
    mock_sb.table.return_value.update.return_value.eq.assert_called_with("id", "item-x")
    insert_payload = mock_sb.table.return_value.insert.call_args.args[0]
    assert insert_payload["item_id"] == "item-x"
    assert insert_payload["correction_type"] == "keep"
    assert insert_payload["original_value"] == {"project": "design", "type": "image", "tags": ["hero"]}
    assert insert_payload["corrected_value"] == {"project": "design", "type": "image", "tags": ["hero"]}
    send_mock.assert_called_once()
    text = send_mock.call_args.args[2]
    assert "Kept" in text


def test_callback_discard_marks_item_discarded_and_writes_correction(client, mocker):
    test_client, mock_sb = client
    _stub_fetch_item(mock_sb, {
        "id": "item-y", "project": "personal", "type": "link",
        "tags": [], "status": "needs_review",
    })
    send_mock = mocker.patch("bot.main.send_message")

    test_client.post(
        "/webhook/test_secret",
        json={
            "update_id": 21,
            "callback_query": {
                "id": "cb-disc",
                "from": {"id": 12345},
                "message": {"chat": {"id": 12345}, "message_id": 201},
                "data": "discard:item-y",
            },
        },
    )

    update_payload = mock_sb.table.return_value.update.call_args.args[0]
    assert update_payload == {"status": "discarded"}
    insert_payload = mock_sb.table.return_value.insert.call_args.args[0]
    assert insert_payload["correction_type"] == "discard"
    assert insert_payload["corrected_value"] == {"status": "discarded"}
    text = send_mock.call_args.args[2]
    assert "Discarded" in text


def test_callback_mark_todo_sets_type_todo_and_writes_correction(client, mocker):
    test_client, mock_sb = client
    _stub_fetch_item(mock_sb, {
        "id": "item-z", "project": "acute", "type": "note",
        "tags": [], "status": "needs_review",
    })
    send_mock = mocker.patch("bot.main.send_message")

    test_client.post(
        "/webhook/test_secret",
        json={
            "update_id": 22,
            "callback_query": {
                "id": "cb-todo",
                "from": {"id": 12345},
                "message": {"chat": {"id": 12345}, "message_id": 202},
                "data": "todo:item-z",
            },
        },
    )

    update_payload = mock_sb.table.return_value.update.call_args.args[0]
    assert update_payload == {"type": "todo", "status": "processed"}
    insert_payload = mock_sb.table.return_value.insert.call_args.args[0]
    assert insert_payload["correction_type"] == "type"
    assert insert_payload["original_value"] == {"type": "note"}
    assert insert_payload["corrected_value"] == {"type": "todo"}
    text = send_mock.call_args.args[2]
    assert "todo" in text.lower()


def test_callback_keep_on_missing_item_says_not_found(client, mocker):
    test_client, mock_sb = client
    _stub_fetch_item(mock_sb, None)
    send_mock = mocker.patch("bot.main.send_message")

    test_client.post(
        "/webhook/test_secret",
        json={
            "update_id": 23,
            "callback_query": {
                "id": "cb-missing",
                "from": {"id": 12345},
                "message": {"chat": {"id": 12345}, "message_id": 203},
                "data": "keep:gone",
            },
        },
    )

    text = send_mock.call_args.args[2]
    assert "not found" in text.lower()
    mock_sb.table.return_value.update.assert_not_called()


def test_review_start_with_empty_queue_says_nothing_to_review(client, mocker):
    test_client, mock_sb = client
    mock_sb.table.return_value.select.return_value.eq.return_value.order.return_value.limit.return_value.execute.return_value.data = []
    send_mock = mocker.patch("bot.main.send_message")

    response = test_client.post(
        "/webhook/test_secret",
        json={
            "update_id": 11,
            "callback_query": {
                "id": "cb-11",
                "from": {"id": 12345},
                "message": {"chat": {"id": 12345}, "message_id": 51},
                "data": "review:start",
            },
        },
    )

    assert response.status_code == 200
    send_mock.assert_called_once()
    text = send_mock.call_args.args[2]
    assert "Nothing needs review" in text
    assert "reply_markup" not in send_mock.call_args.kwargs or send_mock.call_args.kwargs.get("reply_markup") is None


def test_webhook_callback_query_setproj_payload_keeps_inner_colon(client, mocker):
    test_client, _ = client
    handler = mocker.patch("bot.main._handle_triage_callback")
    test_client.post(
        "/webhook/test_secret",
        json={
            "update_id": 3,
            "callback_query": {
                "id": "cb-3",
                "from": {"id": 12345},
                "message": {"chat": {"id": 12345}, "message_id": 7},
                "data": "setproj:item-abc:design",
            },
        },
    )
    kwargs = handler.call_args.kwargs
    assert kwargs["action"] == "setproj"
    assert kwargs["payload"] == "item-abc:design"


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
