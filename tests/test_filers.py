import json
from datetime import date
from unittest.mock import MagicMock

import pytest
import respx
from httpx import Response

from bot.filers import create_todoist_task, move_dropbox_media, write_obsidian_note


def test_obsidian_note_path_format():
    classification = {
        "project": "claude-build",
        "type": "idea",
        "tags": ["ai", "mcp"],
        "visual_subtype": None,
        "summary": "Build a Todoist MCP server with parent-task auto-grouping.",
        "confidence": 0.88,
    }
    fake_dbx = MagicMock()

    obsidian_path = write_obsidian_note(
        dropbox_client=fake_dbx,
        vault_root="/personal-os",
        item_id="abcd-1234",
        classification=classification,
        raw_text="Idea: build a Todoist MCP server.",
        media_dropbox_path=None,
        capture_date=date(2026, 5, 6),
    )

    assert obsidian_path.startswith("claude-build/")
    assert obsidian_path.endswith(".md")
    assert "2026-05-06" in obsidian_path
    assert "todoist" in obsidian_path.lower() or "mcp" in obsidian_path.lower()


def test_obsidian_note_body_contains_frontmatter_and_summary():
    classification = {
        "project": "design",
        "type": "image",
        "tags": ["hero", "dark-mode"],
        "visual_subtype": "hero",
        "summary": "Hero with dark gradient background.",
        "confidence": 0.93,
        "subdomain": None,
    }
    fake_dbx = MagicMock()

    write_obsidian_note(
        dropbox_client=fake_dbx,
        vault_root="/personal-os",
        item_id="img-9999",
        classification=classification,
        raw_text="",
        media_dropbox_path="/personal-os/design/_attachments/img-9999.jpg",
        capture_date=date(2026, 5, 6),
    )

    upload_call = fake_dbx.files_upload.call_args
    note_bytes = upload_call.kwargs["f"] if "f" in upload_call.kwargs else upload_call.args[0]
    note_text = note_bytes.decode("utf-8")

    assert note_text.startswith("---\n")
    assert "id: img-9999" in note_text
    assert "type: image" in note_text
    assert "project: design" in note_text
    assert "visual_subtype: hero" in note_text
    assert "confidence: 0.93" in note_text
    assert "Hero with dark gradient background." in note_text
    # Obsidian wiki-link syntax with bare filename (vault-relative resolution)
    assert "![[img-9999.jpg]]" in note_text


def test_obsidian_note_writes_to_correct_dropbox_path():
    classification = {
        "project": "acute",
        "type": "todo",
        "tags": ["prospecting"],
        "visual_subtype": None,
        "summary": "Follow up with Walmart contact.",
        "confidence": 0.9,
        "subdomain": None,
    }
    fake_dbx = MagicMock()

    obsidian_path = write_obsidian_note(
        dropbox_client=fake_dbx,
        vault_root="/personal-os",
        item_id="todo-1",
        classification=classification,
        raw_text="follow up with walmart",
        media_dropbox_path=None,
        capture_date=date(2026, 5, 6),
    )

    upload_call = fake_dbx.files_upload.call_args
    full_path = upload_call.kwargs.get("path") or upload_call.args[1]
    assert full_path == f"/personal-os/{obsidian_path}"


def _todoist_ok_handler(task_id: str):
    """Build a respx side_effect that echoes back the request's temp_id/uuid as success."""
    def handler(request):
        body = json.loads(request.content)
        cmd = body["commands"][0]
        return Response(200, json={
            "sync_status": {cmd["uuid"]: "ok"},
            "temp_id_mapping": {cmd["temp_id"]: task_id},
        })
    return handler


@respx.mock
def test_create_todoist_task_posts_to_correct_endpoint():
    respx.post("https://api.todoist.com/api/v1/sync").mock(
        side_effect=_todoist_ok_handler("8888888888")
    )

    task_id = create_todoist_task(
        api_token="test-token",
        project_id="1000001",
        content="Follow up with Walmart contact",
    )

    assert task_id == "8888888888"

    request = respx.calls.last.request
    assert request.headers["Authorization"] == "Bearer test-token"
    body = json.loads(request.content)
    cmd = body["commands"][0]
    assert cmd["type"] == "item_add"
    assert cmd["args"]["content"] == "Follow up with Walmart contact"
    assert cmd["args"]["project_id"] == "1000001"


@respx.mock
def test_create_todoist_task_includes_description_when_provided():
    respx.post("https://api.todoist.com/api/v1/sync").mock(
        side_effect=_todoist_ok_handler("9999999999")
    )

    create_todoist_task(
        api_token="t",
        project_id="100",
        content="Call vendor",
        description="See raw capture for context",
    )

    body = json.loads(respx.calls.last.request.content)
    assert body["commands"][0]["args"]["description"] == "See raw capture for context"


@respx.mock
def test_create_todoist_task_raises_on_http_error():
    respx.post("https://api.todoist.com/api/v1/sync").mock(
        return_value=Response(500, text="server error")
    )

    with pytest.raises(RuntimeError, match="Todoist"):
        create_todoist_task(api_token="t", project_id="1", content="x")


@respx.mock
def test_create_todoist_task_raises_when_command_rejected():
    """Todoist returns 200 OK at the HTTP level but the per-command status
    can be an error object (e.g., project not found)."""
    def handler(request):
        body = json.loads(request.content)
        cmd = body["commands"][0]
        return Response(200, json={
            "sync_status": {cmd["uuid"]: {"error_code": 22, "error": "Project not found"}},
            "temp_id_mapping": {},
        })
    respx.post("https://api.todoist.com/api/v1/sync").mock(side_effect=handler)

    with pytest.raises(RuntimeError, match="rejected"):
        create_todoist_task(api_token="t", project_id="bogus", content="x")


def test_move_dropbox_media_calls_files_move_v2():
    fake_dbx = MagicMock()
    fake_dbx.files_move_v2.return_value = MagicMock(
        metadata=MagicMock(path_display="/personal-os/design/_attachments/img-9999.jpg")
    )

    result = move_dropbox_media(
        fake_dbx,
        from_path="/personal-os/_inbox/2026-05-06/img-9999.jpg",
        to_path="/personal-os/design/_attachments/img-9999.jpg",
    )

    fake_dbx.files_move_v2.assert_called_once_with(
        from_path="/personal-os/_inbox/2026-05-06/img-9999.jpg",
        to_path="/personal-os/design/_attachments/img-9999.jpg",
        autorename=False,
    )
    assert result == "/personal-os/design/_attachments/img-9999.jpg"


def test_move_dropbox_media_propagates_errors():
    from dropbox.exceptions import ApiError

    fake_dbx = MagicMock()
    fake_dbx.files_move_v2.side_effect = ApiError("req-id", "user-msg", "err-summary", None)

    with pytest.raises(ApiError):
        move_dropbox_media(fake_dbx, from_path="/a", to_path="/b")
