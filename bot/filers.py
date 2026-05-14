"""Filers: Obsidian note writer, Todoist task creator, Dropbox media mover.

Pure-Python; no FastAPI imports. Each function takes already-built clients/
tokens so the caller (processor) can wire them up once.
"""
import re
import uuid
from datetime import date as _date
from datetime import datetime, timezone

import httpx
from dropbox.files import WriteMode


_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slugify(text: str, max_len: int = 60) -> str:
    s = _SLUG_RE.sub("-", text.lower()).strip("-")
    if not s:
        s = "untitled"
    return s[:max_len].rstrip("-")


def _format_frontmatter(item_id: str, classification: dict, media_dropbox_path: str | None) -> str:
    lines = ["---"]
    lines.append(f"id: {item_id}")
    lines.append(f"created: {datetime.now(timezone.utc).isoformat()}")
    lines.append(f"project: {classification.get('project', '')}")
    if classification.get("subdomain"):
        lines.append(f"subdomain: {classification['subdomain']}")
    lines.append(f"type: {classification.get('type', '')}")
    if classification.get("visual_subtype"):
        lines.append(f"visual_subtype: {classification['visual_subtype']}")
    tags = classification.get("tags") or []
    if tags:
        lines.append("tags:")
        for t in tags:
            lines.append(f"  - {t}")
    if media_dropbox_path:
        lines.append(f"dropbox_path: {media_dropbox_path}")
    lines.append(f"confidence: {classification.get('confidence', 0.0)}")
    lines.append("source: telegram")
    lines.append("---")
    return "\n".join(lines)


def write_obsidian_note(
    dropbox_client,
    vault_root: str,
    item_id: str,
    classification: dict,
    raw_text: str,
    media_dropbox_path: str | None,
    *,
    capture_date: _date | None = None,
) -> str:
    """Write a `.md` note for one item to the Obsidian vault.
    Returns the obsidian_path (relative to vault root)."""
    capture_date = capture_date or datetime.now(timezone.utc).date()
    project = classification.get("project") or "personal"

    summary = (classification.get("summary") or "").strip()
    slug_source = summary or raw_text or item_id
    slug = _slugify(slug_source)

    obsidian_path = f"{project}/{capture_date.isoformat()}-{slug}.md"

    frontmatter = _format_frontmatter(item_id, classification, media_dropbox_path)
    body_parts = [frontmatter, "", summary]
    if raw_text and raw_text != summary:
        body_parts += ["", "## Raw capture", "", raw_text]
    if media_dropbox_path and classification.get("type") == "image":
        # Use Obsidian wiki-link syntax so the embed resolves to the file
        # anywhere inside the vault (e.g., the project's _attachments/ folder).
        # Filenames are UUIDs so basename collisions are not a concern.
        filename = media_dropbox_path.rsplit("/", 1)[-1]
        body_parts += ["", f"![[{filename}]]"]
    if classification.get("type") == "tutorial" and classification.get("_tutorial_video_url"):
        # Direct link to the X-hosted mp4 so the note is one click from watch.
        body_parts += ["", "## Video", "", classification["_tutorial_video_url"]]
    note = "\n".join(body_parts).encode("utf-8")

    full_path = f"{vault_root}/{obsidian_path}"
    dropbox_client.files_upload(f=note, path=full_path, mode=WriteMode("overwrite"))

    return obsidian_path


def create_todoist_task(
    api_token: str,
    project_id: str,
    content: str,
    *,
    description: str | None = None,
) -> str:
    """POST a new top-level Todoist task in the given project. Returns the new task ID.

    Uses Todoist's unified API v1 sync endpoint. The legacy REST v2 endpoint
    (/rest/v2/tasks) was deprecated and now returns 410 Gone.
    """
    temp_id = str(uuid.uuid4())
    cmd_uuid = str(uuid.uuid4())
    args: dict = {"content": content, "project_id": project_id}
    if description:
        args["description"] = description

    body = {
        "commands": [
            {"type": "item_add", "temp_id": temp_id, "uuid": cmd_uuid, "args": args}
        ]
    }

    response = httpx.post(
        "https://api.todoist.com/api/v1/sync",
        headers={"Authorization": f"Bearer {api_token}"},
        json=body,
        timeout=10.0,
    )
    if response.status_code >= 400:
        raise RuntimeError(f"Todoist API failed ({response.status_code}): {response.text}")

    data = response.json()
    cmd_status = data.get("sync_status", {}).get(cmd_uuid)
    if cmd_status != "ok":
        raise RuntimeError(f"Todoist item_add rejected: {cmd_status} (full response: {data})")

    new_task_id = data.get("temp_id_mapping", {}).get(temp_id)
    if not new_task_id:
        raise RuntimeError(f"Todoist did not return a task id: {data}")
    return str(new_task_id)


def move_dropbox_media(dropbox_client, *, from_path: str, to_path: str) -> str:
    """Move a file in Dropbox. Returns the destination path."""
    result = dropbox_client.files_move_v2(
        from_path=from_path,
        to_path=to_path,
        autorename=False,
    )
    return result.metadata.path_display
