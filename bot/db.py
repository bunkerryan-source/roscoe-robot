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


def fetch_recent_corrections(client, limit: int = 30) -> list[dict]:
    response = (
        client.table("corrections")
        .select("original_class, corrected_class")
        .order("created_at", desc=True)
        .limit(limit)
        .execute()
    )
    return response.data or []
