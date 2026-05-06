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
