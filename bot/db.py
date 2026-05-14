from datetime import datetime, timezone
from typing import Optional

from supabase import create_client, Client


def get_client(url: str, service_key: str) -> Client:
    return create_client(url, service_key)


def insert_item(
    client: Client,
    *,
    source_message_id: str,
    raw_text: Optional[str],
    media_type: str,
    media_telegram_file_id: Optional[str],
) -> dict:
    payload = {
        "status": "pending",
        "source": "telegram",
        "source_message_id": source_message_id,
        "raw_text": raw_text,
        "media_type": media_type,
        "media_telegram_file_id": media_telegram_file_id,
    }
    result = client.table("items").insert(payload).execute()
    return result.data[0]


def update_media_path(client: Client, item_id: str, dropbox_path: str) -> None:
    client.table("items").update({"media_dropbox_path": dropbox_path}).eq("id", item_id).execute()


def fetch_pending_items(client, limit: int = 50) -> list[dict]:
    response = (
        client.table("items")
        .select("*")
        .eq("status", "pending")
        .order("created_at")
        .limit(limit)
        .execute()
    )
    return response.data or []


def update_classified(
    client,
    item_id: str,
    *,
    classification: dict,
    obsidian_path: str,
    todoist_task_id: str | None,
    api_cost_cents: int,
    status: str = "processed",
    source_post_id: str | None = None,
    media_dropbox_path: str | None = None,
    error: str | None = None,
) -> None:
    payload = {
        "status": status,
        "processed_at": datetime.now(timezone.utc).isoformat(),
        "project": classification.get("project"),
        "subdomain": classification.get("subdomain"),
        "type": classification.get("type"),
        "tags": classification.get("tags") or [],
        "visual_subtype": classification.get("visual_subtype"),
        "summary": classification.get("summary"),
        "obsidian_path": obsidian_path,
        "todoist_task_id": todoist_task_id,
        "classified_by": "claude-haiku-4-5-20251001",
        "confidence": classification.get("confidence"),
        "api_cost_cents": api_cost_cents,
    }
    if source_post_id is not None:
        payload["source_post_id"] = source_post_id
    if media_dropbox_path is not None:
        payload["media_dropbox_path"] = media_dropbox_path
    if error is not None:
        payload["error"] = error
    client.table("items").update(payload).eq("id", item_id).execute()


def insert_run(
    client,
    *,
    trigger: str,
    items_processed: int,
    items_needs_review: int,
    items_failed: int,
    total_cost_cents: int,
    started_at: datetime,
    completed_at: datetime,
) -> dict:
    payload = {
        "trigger": trigger,
        "items_processed": items_processed,
        "items_needs_review": items_needs_review,
        "items_failed": items_failed,
        "total_cost_cents": total_cost_cents,
        "started_at": started_at.isoformat(),
        "completed_at": completed_at.isoformat(),
    }
    response = client.table("runs").insert(payload).execute()
    return response.data[0]


def fetch_items_for_summary(client, *, since: str, until: str) -> list[dict]:
    """Return items processed in the window [since, until) for daily summaries.

    `since` and `until` are UTC ISO 8601 strings. Filters on `processed_at` so
    only items that actually finished classification are counted — pending or
    failed items don't pollute the brief.
    """
    response = (
        client.table("items")
        .select("id, project, type, status, summary, api_cost_cents, processed_at")
        .gte("processed_at", since)
        .lt("processed_at", until)
        .order("processed_at")
        .execute()
    )
    return response.data or []


def fetch_item(client, item_id: str) -> dict | None:
    """Single item by id — read-side helper for triage handlers that need to
    capture the pre-change state before writing a corrections row.
    """
    response = (
        client.table("items")
        .select("*")
        .eq("id", item_id)
        .limit(1)
        .execute()
    )
    rows = response.data or []
    return rows[0] if rows else None


def update_item_fields(client, item_id: str, **fields) -> None:
    """Patch arbitrary columns on an item by id.

    Used by triage handlers to flip status/type/project without rebuilding
    the whole classification payload that update_classified takes. Caller
    chooses the columns so the corrections-row pair stays in sync.
    """
    client.table("items").update(fields).eq("id", item_id).execute()


def fetch_needs_review_items(client, limit: int = 20) -> list[dict]:
    """Items that landed in needs_review and are awaiting human triage.

    Ordered oldest-first so the triage walk through the queue mirrors the
    capture order. Selects only the columns the triage card renders so the
    payload stays small.
    """
    response = (
        client.table("items")
        .select(
            "id, raw_text, media_type, media_dropbox_path, project, type, "
            "tags, summary, source_post_id"
        )
        .eq("status", "needs_review")
        .order("processed_at", desc=False)
        .limit(limit)
        .execute()
    )
    return response.data or []


def fetch_recent_corrections(client, limit: int = 30) -> list[dict]:
    response = (
        client.table("corrections")
        .select("correction_type, original_value, corrected_value, note")
        .order("created_at", desc=True)
        .limit(limit)
        .execute()
    )
    return response.data or []


def get_source_post_by_url(client, source_url: str) -> dict | None:
    """Return existing source_posts row matched by source_url, or None."""
    response = (
        client.table("source_posts")
        .select("*")
        .eq("source_url", source_url)
        .limit(1)
        .execute()
    )
    rows = response.data or []
    return rows[0] if rows else None


def get_source_post_by_id(client, source_post_id: str) -> dict | None:
    """Return source_posts row by id, or None. Used by fan-out children to
    load cached post_text without re-scraping."""
    response = (
        client.table("source_posts")
        .select("*")
        .eq("id", source_post_id)
        .limit(1)
        .execute()
    )
    rows = response.data or []
    return rows[0] if rows else None


def insert_source_post(
    client,
    *,
    source: str,
    source_url: str,
    post_text: str,
    author_handle: str | None,
    author_name: str | None,
    posted_at,
    image_urls: list[str],
    midjourney_params: dict,
    raw_response: dict,
) -> str:
    """Insert a source_posts row and return its id."""
    payload = {
        "source": source,
        "source_url": source_url,
        "post_text": post_text,
        "author_handle": author_handle,
        "author_name": author_name,
        "posted_at": posted_at.isoformat() if posted_at else None,
        "image_urls": image_urls,
        "midjourney_params": midjourney_params,
        "raw_scraper_response": raw_response,
    }
    response = client.table("source_posts").insert(payload).execute()
    return response.data[0]["id"]


def insert_correction(
    client,
    *,
    item_id: str,
    correction_type: str,
    original_value: dict | None,
    corrected_value: dict | None,
    note: str | None = None,
) -> str:
    """Insert a corrections row and return its id."""
    payload = {
        "item_id": item_id,
        "correction_type": correction_type,
        "original_value": original_value,
        "corrected_value": corrected_value,
        "note": note,
    }
    response = client.table("corrections").insert(payload).execute()
    return response.data[0]["id"]
