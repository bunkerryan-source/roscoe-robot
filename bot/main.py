import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import anthropic
import httpx
from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, HTTPException, Request

# Load .env for local dev. On the droplet, systemd's EnvironmentFile= already
# populates the environment, and load_dotenv() does not override existing vars.
load_dotenv()

from bot.config import Config
from bot.db import fetch_items_for_summary, get_client, insert_item, update_media_path
from bot.intake import parse_update
from bot.media import DropboxRefreshClient, download_from_telegram, upload_with_fallback
from bot.processor import run_batch
from bot.scheduler import build_scheduler
from bot.summary import build_evening_summary, build_morning_summary
from bot.triage import build_review_keyboard, parse_callback_data

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

LA = ZoneInfo("America/Los_Angeles")


def _utc_window_for_yesterday_la() -> tuple[str, str]:
    """Return (since_utc_iso, until_utc_iso) bounding yesterday in LA local time."""
    now_la = datetime.now(LA)
    today_start_la = now_la.replace(hour=0, minute=0, second=0, microsecond=0)
    yesterday_start_la = today_start_la - timedelta(days=1)
    return (
        yesterday_start_la.astimezone(timezone.utc).isoformat(),
        today_start_la.astimezone(timezone.utc).isoformat(),
    )


def _utc_window_for_today_la() -> tuple[str, str]:
    """Return (since_utc_iso, until_utc_iso) bounding today-so-far in LA local time."""
    now_la = datetime.now(LA)
    start_la = now_la.replace(hour=0, minute=0, second=0, microsecond=0)
    return (
        start_la.astimezone(timezone.utc).isoformat(),
        now_la.astimezone(timezone.utc).isoformat(),
    )


async def _send_morning_summary() -> None:
    try:
        since, until = _utc_window_for_yesterday_la()
        items = await asyncio.to_thread(
            fetch_items_for_summary, supabase, since=since, until=until
        )
        total_cost = sum((it.get("api_cost_cents") or 0) for it in items)
        text = build_morning_summary(items, total_cost)
        needs_review = sum(1 for i in items if i.get("status") == "needs_review")
        await send_message(
            config.my_telegram_id,
            None,
            text,
            reply_markup=build_review_keyboard(needs_review),
        )
    except Exception:
        logger.exception("morning summary job failed")


async def _send_evening_summary() -> None:
    try:
        since, until = _utc_window_for_today_la()
        items = await asyncio.to_thread(
            fetch_items_for_summary, supabase, since=since, until=until
        )
        total_cost = sum((it.get("api_cost_cents") or 0) for it in items)
        text = build_evening_summary(items, total_cost)
        needs_review = sum(1 for i in items if i.get("status") == "needs_review")
        await send_message(
            config.my_telegram_id,
            None,
            text,
            reply_markup=build_review_keyboard(needs_review),
        )
    except Exception:
        logger.exception("evening summary job failed")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    scheduler = build_scheduler(
        morning_job=_send_morning_summary,
        evening_job=_send_evening_summary,
        timezone="America/Los_Angeles",
    )
    scheduler.start()
    logger.info(
        "APScheduler started — jobs: %s",
        [(j.id, str(j.next_run_time)) for j in scheduler.get_jobs()],
    )
    try:
        yield
    finally:
        scheduler.shutdown(wait=False)


app = FastAPI(lifespan=lifespan)


@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.post("/webhook/{secret}")
async def webhook(secret: str, request: Request, background_tasks: BackgroundTasks):
    if secret != config.webhook_secret:
        raise HTTPException(status_code=403, detail="invalid webhook secret")

    update = await request.json()

    if "callback_query" in update:
        cb = update["callback_query"]
        cb_sender_id = (cb.get("from") or {}).get("id")
        if cb_sender_id != config.my_telegram_id:
            logger.info("dropping callback_query from unauthorized sender id=%s", cb_sender_id)
            return {"ok": True}
        action, payload = parse_callback_data(cb.get("data") or "")
        cb_message = cb.get("message") or {}
        chat_id = (cb_message.get("chat") or {}).get("id")
        message_id = cb_message.get("message_id")
        background_tasks.add_task(
            _handle_triage_callback,
            action=action,
            payload=payload,
            chat_id=chat_id,
            message_id=message_id,
            callback_id=cb["id"],
        )
        return {"ok": True}

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


async def send_message(
    chat_id: int,
    reply_to: int | None,
    text: str,
    *,
    reply_markup: dict | None = None,
) -> None:
    payload: dict = {"chat_id": chat_id, "text": text}
    if reply_to is not None:
        payload["reply_to_message_id"] = reply_to
    if reply_markup is not None:
        payload["reply_markup"] = reply_markup
    try:
        async with httpx.AsyncClient(timeout=10.0) as http:
            await http.post(
                f"https://api.telegram.org/bot{config.bot_token}/sendMessage",
                json=payload,
            )
    except Exception:
        logger.exception("send_message failed for chat %s", chat_id)


async def _answer_callback_query(callback_id: str) -> None:
    """Clear Telegram's loading spinner on an inline-keyboard tap.

    Telegram retries the callback if we don't answer within ~10s, so this
    runs first regardless of how the action itself resolves.
    """
    try:
        async with httpx.AsyncClient(timeout=5.0) as http:
            await http.post(
                f"https://api.telegram.org/bot{config.bot_token}/answerCallbackQuery",
                json={"callback_query_id": callback_id},
            )
    except Exception:
        logger.exception("answerCallbackQuery failed for cb %s", callback_id)


async def _handle_triage_callback(
    *,
    action: str,
    payload: str,
    chat_id: int,
    message_id: int,
    callback_id: str,
) -> None:
    """Dispatch a callback_query to the matching triage handler.

    Always answers the callback first so the Telegram client clears its
    spinner — handlers can fail without leaving a stuck UI.
    """
    await _answer_callback_query(callback_id)
    logger.info("triage callback action=%s payload=%s", action, payload)
    # Real action branches are wired in subsequent Phase D tasks (D3-D5).


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
