import logging
from datetime import datetime, timezone

import httpx
from fastapi import BackgroundTasks, FastAPI, HTTPException, Request

from bot.config import Config
from bot.db import get_client, insert_item, update_media_path
from bot.intake import parse_update
from bot.media import DropboxRefreshClient, download_from_telegram, upload_with_fallback

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

config = Config.from_env()
supabase = get_client(config.supabase_url, config.supabase_service_key)
dropbox_factory = DropboxRefreshClient(
    config.dropbox_refresh_token,
    config.dropbox_app_key,
    config.dropbox_app_secret,
)

app = FastAPI()


@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.post("/webhook/{secret}")
async def webhook(secret: str, request: Request, background_tasks: BackgroundTasks):
    if secret != config.webhook_secret:
        raise HTTPException(status_code=403, detail="invalid webhook secret")

    update = await request.json()

    msg = update.get("message") or update.get("channel_post") or {}
    sender_id = (msg.get("from") or {}).get("id")
    if sender_id != config.my_telegram_id:
        logger.info("dropping update from unauthorized sender id=%s", sender_id)
        return {"ok": True}

    try:
        intake = parse_update(update)
    except ValueError as exc:
        logger.warning("could not parse update: %s", exc)
        return {"ok": True}

    chat_id = msg["chat"]["id"]
    message_id = msg["message_id"]

    item = insert_item(
        supabase,
        source_message_id=str(message_id),
        raw_text=intake["raw_text"],
        media_type=intake["media_type"],
        media_telegram_file_id=intake["media_telegram_file_id"],
    )

    if intake["media_telegram_file_id"]:
        background_tasks.add_task(
            handle_media,
            item_id=item["id"],
            file_id=intake["media_telegram_file_id"],
            ext=ext_for_media_type(intake["media_type"]),
        )

    background_tasks.add_task(send_ack, chat_id=chat_id, reply_to=message_id)
    return {"ok": True}


async def handle_media(item_id: str, file_id: str, ext: str) -> None:
    try:
        content = await download_from_telegram(config.bot_token, file_id)
        date_path = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        dropbox_path = f"/personal-os-inbox/{date_path}/{item_id}{ext}"
        result_path = upload_with_fallback(
            dropbox_factory.get_client,
            dropbox_path=dropbox_path,
            content=content,
        )
        update_media_path(supabase, item_id, result_path)
    except Exception:
        logger.exception("media handling failed for item %s", item_id)


async def send_ack(chat_id: int, reply_to: int) -> None:
    try:
        async with httpx.AsyncClient(timeout=10.0) as http:
            await http.post(
                f"https://api.telegram.org/bot{config.bot_token}/sendMessage",
                json={
                    "chat_id": chat_id,
                    "text": "\U0001f44d",
                    "reply_to_message_id": reply_to,
                },
            )
    except Exception:
        logger.exception("ack failed for chat %s", chat_id)


def ext_for_media_type(media_type: str) -> str:
    return {
        "image": ".jpg",
        "video": ".mp4",
        "voice": ".ogg",
        "document": ".bin",
    }.get(media_type, ".bin")
