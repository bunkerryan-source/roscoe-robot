import asyncio
import logging
from datetime import datetime, timezone
from pathlib import Path

import anthropic
import httpx
from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, HTTPException, Request

# Load .env for local dev. On the droplet, systemd's EnvironmentFile= already
# populates the environment, and load_dotenv() does not override existing vars.
load_dotenv()

from bot.config import Config
from bot.db import get_client, insert_item, update_media_path
from bot.intake import parse_update
from bot.media import DropboxRefreshClient, download_from_telegram, upload_with_fallback
from bot.processor import run_batch

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

config = Config.from_env()
supabase = get_client(config.supabase_url, config.supabase_service_key)
dropbox_factory = DropboxRefreshClient(
    config.dropbox_refresh_token,
    config.dropbox_app_key,
    config.dropbox_app_secret,
)
anthropic_client = anthropic.Anthropic(api_key=config.anthropic_api_key)

# Read seed files at startup so the processor doesn't re-read on every batch.
_RULES_PATH = Path("_meta/rules.md")
_TAG_VOCAB_PATH = Path("_meta/tag_vocab.md")
RULES_MD = _RULES_PATH.read_text(encoding="utf-8") if _RULES_PATH.exists() else ""
TAG_VOCAB_MD = _TAG_VOCAB_PATH.read_text(encoding="utf-8") if _TAG_VOCAB_PATH.exists() else ""

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

    chat_id = msg["chat"]["id"]
    message_id = msg["message_id"]
    text = (msg.get("text") or "").strip()

    if text == "/process":
        background_tasks.add_task(send_ack, chat_id=chat_id, reply_to=message_id)
        background_tasks.add_task(_run_batch_and_reply, chat_id=chat_id, reply_to=message_id)
        return {"ok": True}

    try:
        intake = parse_update(update)
    except ValueError as exc:
        logger.warning("could not parse update: %s", exc)
        return {"ok": True}

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
        dropbox_path = f"/personal-os/_inbox/{date_path}/{item_id}{ext}"
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


async def send_message(chat_id: int, reply_to: int, text: str) -> None:
    try:
        async with httpx.AsyncClient(timeout=10.0) as http:
            await http.post(
                f"https://api.telegram.org/bot{config.bot_token}/sendMessage",
                json={
                    "chat_id": chat_id,
                    "text": text,
                    "reply_to_message_id": reply_to,
                },
            )
    except Exception:
        logger.exception("send_message failed for chat %s", chat_id)


async def _run_batch_and_reply(chat_id: int, reply_to: int) -> None:
    try:
        # run_batch is sync and may take seconds. Offload to a thread so the
        # event loop stays free to serve other webhook requests during the run.
        result = await asyncio.to_thread(
            run_batch,
            supabase_client=supabase,
            anthropic_client=anthropic_client,
            dropbox_client_factory=dropbox_factory.get_client,
            openai_api_key=config.openai_api_key,
            todoist_token=config.todoist_api_token,
            todoist_projects=config.todoist_projects,
            vault_root=config.obsidian_vault_dropbox_path,
            rules_md=RULES_MD,
            tag_vocab_md=TAG_VOCAB_MD,
            apify_api_token=config.apify_api_token,
            apify_tweet_scraper_actor=config.apify_tweet_scraper_actor,
        )
        cost_dollars = result["total_cost_cents"] / 100.0
        text = (
            f"processed {result['items_processed']} items "
            f"in {result['duration_seconds']:.1f}s · "
            f"${cost_dollars:.2f} · "
            f"{result['items_needs_review']} needs review"
        )
        if result["items_failed"]:
            text += f" · {result['items_failed']} failed"
    except Exception:
        logger.exception("/process run failed")
        text = "/process failed — check journalctl"

    await send_message(chat_id, reply_to, text)


def ext_for_media_type(media_type: str) -> str:
    return {
        "image": ".jpg",
        "video": ".mp4",
        "voice": ".ogg",
        "document": ".bin",
    }.get(media_type, ".bin")
