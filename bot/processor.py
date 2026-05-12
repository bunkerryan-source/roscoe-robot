"""Per-item processing orchestrator. Pure-Python; no FastAPI imports.

This module is the spec's heart. It exposes:

- `enrich_item(item, ...)` — produce the classifier payload + needs_vision flag.
- `process_item(item, ...)` — full per-item pipeline. Never raises.
- `run_batch(...)` — drain the pending queue. Failures isolate per item.
"""
import logging
import re
from datetime import datetime, timezone
from urllib.parse import urlparse

import httpx

from bot import scraper
from bot.db import (
    fetch_pending_items,
    fetch_recent_corrections,
    get_source_post_by_id,
    get_source_post_by_url,
    insert_run,
    insert_source_post,
    update_classified,
)
from bot.enrichment import (
    fetch_og_metadata,
    fetch_youtube_transcript,
    transcribe_voice,
)
from bot.filers import (
    create_todoist_task,
    move_dropbox_media,
    write_obsidian_note,
)
from bot.llm import build_classifier_system_prompt, classify_item
from bot.vision import refine_with_vision

X_URL_RE = re.compile(r"https?://(?:x|twitter)\.com/[^\s]+", re.I)

_VIDEO_EXTENSIONS = (".mp4", ".mov", ".webm")


def _is_video_url(url: str | None) -> bool:
    """Return True iff the URL path ends in a known video extension."""
    if not url:
        return False
    path = urlparse(url).path
    return any(path.lower().endswith(ext) for ext in _VIDEO_EXTENSIONS)


def extract_x_url(text: str | None) -> str | None:
    """Return the first X/Twitter URL in `text`, or None."""
    if not text:
        return None
    m = X_URL_RE.search(text)
    if not m:
        return None
    return m.group(0).rstrip(".,)]\"'")


def handle_x_url(supabase_client, url: str, *, token: str, actor: str) -> dict:
    """Return source_post info for the URL, scraping + inserting if not cached.

    Returns a dict with keys:
      - source_post_id
      - image_urls (list)
      - post_text (str)
      - midjourney_params (dict)
    Raises scraper.ScraperError on scrape failure (caller decides fallback).
    """
    existing = get_source_post_by_url(supabase_client, url)
    if existing:
        return {
            "source_post_id": existing["id"],
            "image_urls": existing.get("image_urls") or [],
            "post_text": existing.get("post_text") or "",
            "midjourney_params": existing.get("midjourney_params") or {},
        }
    result = scraper.fetch_tweet(url, token=token, actor=actor)
    new_id = insert_source_post(
        supabase_client,
        source="x",
        source_url=result.source_url,
        post_text=result.post_text,
        author_handle=result.author_handle,
        author_name=result.author_name,
        posted_at=result.posted_at,
        image_urls=result.image_urls,
        midjourney_params=result.midjourney_params,
        raw_response=result.raw_response or {},
    )
    return {
        "source_post_id": new_id,
        "image_urls": result.image_urls,
        "post_text": result.post_text,
        "midjourney_params": result.midjourney_params,
    }


def _download_image_to_dropbox(
    dropbox_client,
    image_url: str,
    item_id: str,
    *,
    index: int,
    vault_root: str,
    project: str = "_inbox",
) -> str:
    """Download an image URL and upload to Dropbox inside the vault.

    Files land at `<vault_root>/<project>/_attachments/<item_id>-<index>.jpg`
    so Obsidian can resolve the wiki-link embed. The post-classify move step
    in `process_item` relocates the file to the right project's _attachments
    folder once classification is done.
    """
    from dropbox.files import WriteMode

    with httpx.Client(timeout=30.0, follow_redirects=True) as client:
        resp = client.get(image_url)
        resp.raise_for_status()
        content = resp.content
    ext = ".jpg"  # X images are always jpg via media_url_https
    dropbox_path = f"{vault_root}/{project}/_attachments/{item_id}-{index}{ext}"
    dropbox_client.files_upload(f=content, path=dropbox_path, mode=WriteMode("overwrite"))
    return dropbox_path

logger = logging.getLogger(__name__)


# Project keywords used for the "decisive caption → skip vision" rule.
_PROJECT_KEYWORDS = {
    "acute": ("acute", "freight", "ltl", "truckload", "walmart", "shipping", "carrier"),
    "abp": ("abp", "c3bank", "bridge loan", "construction loan"),
    "lake-arrowhead": ("lake arrowhead", "cabin", "kitchen tile", "bathroom tile"),
    "church": ("come follow me", "gospel", "church", "talk", "sunday"),
    "claude-build": ("claude", "anthropic", "mcp", "skill", "agent"),
    "design": ("hero", "nav", "typography", "color palette", "branding"),
    "personal": ("surf", "woodwork", "leather", "carlsbad"),
}

_VISION_SKIP_MIN_CAPTION_LEN = 8
NEEDS_REVIEW_THRESHOLD = 0.6


def _caption_is_decisive(caption: str) -> bool:
    """Per spec: caption length > 8 chars AND matches a known project keyword."""
    if not caption or len(caption) <= _VISION_SKIP_MIN_CAPTION_LEN:
        return False
    lowered = caption.lower()
    for keywords in _PROJECT_KEYWORDS.values():
        for kw in keywords:
            if kw in lowered:
                return True
    return False


def _download_dropbox_bytes(dropbox_client, path: str) -> bytes:
    _, response = dropbox_client.files_download(path=path)
    return response.content


def enrich_item(
    item: dict,
    *,
    openai_api_key: str,
    dropbox_client=None,
) -> tuple[str, bool]:
    """Return (classifier_payload_text, needs_vision)."""
    media_type = item["media_type"]
    raw_text = item.get("raw_text") or ""

    if media_type in ("text", "forward"):
        return raw_text, False

    if media_type == "voice":
        if dropbox_client is None or not item.get("media_dropbox_path"):
            return raw_text, False
        try:
            audio_bytes = _download_dropbox_bytes(dropbox_client, item["media_dropbox_path"])
            transcript = transcribe_voice(openai_api_key, audio_bytes, file_extension=".ogg")
            return f"[voice transcript]\n{transcript}", False
        except Exception as e:
            logger.warning("voice transcription failed: %s", e)
            return raw_text, False

    if media_type == "link":
        url = raw_text.strip()
        try:
            transcript = fetch_youtube_transcript(url)
            return f"[link]\nURL: {url}\n[YouTube transcript]\n{transcript}", False
        except ValueError:
            pass  # not a YouTube link or no transcript

        try:
            og = fetch_og_metadata(url)
            return (
                f"[link]\nURL: {url}\nTitle: {og['title']}\nDescription: {og['description']}\nSite: {og['site_name']}",
                False,
            )
        except Exception as e:
            logger.warning("OG fetch failed for %s: %s", url, e)
            return f"[link]\nURL: {url}", False

    if media_type == "image":
        caption = raw_text
        if _caption_is_decisive(caption):
            return f"[image with decisive caption]\nCaption: {caption}", False
        return f"[image]\nCaption: {caption}\n(vision required)", True

    if media_type == "video":
        return f"[video]\nCaption: {raw_text}", False

    return raw_text, False


def _post_classify_dropbox_path(item_id: str, project: str, type_: str, original_path: str, vault_root: str) -> str:
    """Compute where media should live after classification.

    All media lives inside the Obsidian vault under
    `<vault_root>/<project>/_attachments/<item_id>.<ext>` so that Obsidian
    can resolve embeds via wiki-link syntax. The future design dashboard
    reads from `<vault_root>/design/_attachments/`.
    """
    ext = "." + original_path.rsplit(".", 1)[-1] if "." in original_path else ""
    project = project or "personal"
    return f"{vault_root}/{project}/_attachments/{item_id}{ext}"


def process_item(
    item: dict,
    *,
    anthropic_client,
    dropbox_client,
    openai_api_key: str,
    todoist_token: str,
    todoist_projects: dict,
    vault_root: str,
    system_blocks: list[dict],
    supabase_client=None,
    apify_api_token: str | None = None,
    apify_tweet_scraper_actor: str | None = None,
) -> dict:
    """Run the full pipeline for one item. Never raises — failures land in the result."""
    out = {
        "status": "processed",
        "classification": None,
        "obsidian_path": None,
        "todoist_task_id": None,
        "api_cost_cents": 0,
        "source_post_id": None,
        "media_dropbox_path": item.get("media_dropbox_path"),
        "scrape_info": None,
        "error": None,
    }

    try:
        # Phase A — X URL scrape (Session 4). Runs before enrichment so the
        # scraped post text can be passed into the classifier payload, and
        # multi-image fan-out can be deferred to run_batch.
        #
        # Two paths:
        # 1. Fresh capture (no source_post_id yet) — detect X URL, scrape live
        #    (or hit cache), set out["scrape_info"] so run_batch will fan-out
        #    additional images.
        # 2. Fan-out child (source_post_id already set) — load cached post_text
        #    from source_posts so the classifier sees the same context as the
        #    parent. DO NOT set out["scrape_info"]: run_batch keys on that to
        #    decide fan-out, and children must not re-fan-out (would infinite
        #    loop).
        scrape_info = None
        if item.get("source_post_id") and supabase_client is not None:
            try:
                sp = get_source_post_by_id(supabase_client, item["source_post_id"])
                if sp:
                    scrape_info = {
                        "source_post_id": sp["id"],
                        "post_text": sp.get("post_text") or "",
                        "midjourney_params": sp.get("midjourney_params") or {},
                        "image_urls": sp.get("image_urls") or [],
                    }
                    out["source_post_id"] = sp["id"]
            except Exception as e:
                logger.warning("source_post lookup failed for item %s: %s", item.get("id"), e)
        else:
            x_url = extract_x_url(item.get("raw_text", ""))
            if x_url and supabase_client is not None and apify_api_token:
                try:
                    scrape_info = handle_x_url(
                        supabase_client, x_url,
                        token=apify_api_token,
                        actor=apify_tweet_scraper_actor or "xquik~x-tweet-scraper",
                    )
                    out["source_post_id"] = scrape_info["source_post_id"]
                    out["scrape_info"] = scrape_info
                    # Download first image only if the item doesn't already carry user-uploaded media.
                    if scrape_info["image_urls"] and not item.get("media_dropbox_path"):
                        first_img = scrape_info["image_urls"][0]
                        staged = _download_image_to_dropbox(
                            dropbox_client, first_img, item["id"],
                            index=0, vault_root=vault_root, project="_inbox",
                        )
                        item["media_dropbox_path"] = staged
                        item["media_type"] = "image"
                        out["media_dropbox_path"] = staged
                except scraper.ScraperError as e:
                    logger.warning("X scrape failed for %s: %s — falling back to bare URL", x_url, e)
                except Exception as e:
                    logger.warning("X scrape unexpected error for %s: %s — falling back", x_url, e)

        payload, _needs_vision = enrich_item(
            item,
            openai_api_key=openai_api_key,
            dropbox_client=dropbox_client,
        )
        # Note: vision-call wiring is deferred to Phase B. For now we send
        # text-only and rely on the caption + scraped post text.

        # Inject scraped post body into the classifier payload (never into raw_text).
        if scrape_info and scrape_info.get("post_text"):
            scraped_block = f"[scraped X post]\n{scrape_info['post_text']}"
            if scrape_info.get("midjourney_params"):
                scraped_block += f"\n[midjourney params] {scrape_info['midjourney_params']}"
            payload = f"{payload}\n\n{scraped_block}" if payload.strip() else scraped_block

        if not payload.strip():
            payload = (
                f"[{item.get('media_type', 'unknown')} item with no caption "
                f"or transcript — classify as best-guess from media_type]"
            )

        classification = classify_item(anthropic_client, system_blocks, payload)
        out["classification"] = classification
        out["api_cost_cents"] = classification.get("_cost_cents", 0)

        # Phase B — second-pass vision for design+image items. Refines tags,
        # visual_subtype, and summary by actually looking at the image. Skipped
        # for any other project/type combo to keep cost discipline.
        if (
            classification.get("project") == "design"
            and classification.get("type") == "image"
            and item.get("media_dropbox_path")
        ):
            try:
                image_bytes = _download_dropbox_bytes(dropbox_client, item["media_dropbox_path"])
                refinement = refine_with_vision(
                    anthropic_client,
                    image_bytes=image_bytes,
                    text_context=item.get("raw_text") or "",
                    scraped_post_text=(scrape_info or {}).get("post_text", ""),
                    prior_classification={"project": "design", "type": "image"},
                )
                if refinement is not None:
                    if refinement.visual_subtype:
                        classification["visual_subtype"] = refinement.visual_subtype
                    if refinement.tags:
                        classification["tags"] = refinement.tags
                    if refinement.summary:
                        classification["summary"] = refinement.summary
                    out["api_cost_cents"] += refinement.cost_cents
            except Exception as e:
                logger.warning("vision refinement failed for item %s: %s", item.get("id"), e)

        new_media_path = item.get("media_dropbox_path")
        if item.get("media_dropbox_path") and item["media_type"] in ("image", "video", "voice"):
            try:
                new_media_path = _post_classify_dropbox_path(
                    item["id"],
                    classification.get("project") or "personal",
                    classification.get("type") or "image",
                    item["media_dropbox_path"],
                    vault_root,
                )
                move_dropbox_media(
                    dropbox_client,
                    from_path=item["media_dropbox_path"],
                    to_path=new_media_path,
                )
                out["media_dropbox_path"] = new_media_path
            except Exception as e:
                logger.warning("media move failed for item %s: %s", item["id"], e)
                new_media_path = item["media_dropbox_path"]

        obsidian_path = write_obsidian_note(
            dropbox_client=dropbox_client,
            vault_root=vault_root,
            item_id=item["id"],
            classification=classification,
            raw_text=item.get("raw_text") or "",
            media_dropbox_path=new_media_path,
        )
        out["obsidian_path"] = obsidian_path

        if classification.get("type") == "todo":
            project = classification.get("project") or "personal"
            project_id = todoist_projects.get(project)
            if project_id:
                try:
                    out["todoist_task_id"] = create_todoist_task(
                        api_token=todoist_token,
                        project_id=project_id,
                        content=classification.get("summary") or item.get("raw_text") or "(no content)",
                        description=item.get("raw_text") if classification.get("summary") else None,
                    )
                except Exception as e:
                    logger.warning("Todoist create failed for item %s: %s", item["id"], e)

        if (classification.get("confidence") or 0.0) < NEEDS_REVIEW_THRESHOLD:
            out["status"] = "needs_review"

    except Exception as e:
        logger.exception("process_item failed for %s", item.get("id"))
        out["status"] = "failed"
        out["error"] = str(e)

    return out


def _fan_out_additional_items_from_scrape(
    supabase_client,
    dropbox_client,
    original_item: dict,
    scrape_info: dict,
    vault_root: str,
) -> list[dict]:
    """Create N-1 additional items rows for images 2..N of a multi-image scrape.

    Returns the newly-inserted item dicts so run_batch can append them to the
    drain queue. Each new item shares the source_post_id with the original
    and carries `media_type='image'` with a staged Dropbox path.
    """
    image_urls = scrape_info.get("image_urls") or []
    if len(image_urls) <= 1:
        return []

    new_items: list[dict] = []
    for idx, img_url in enumerate(image_urls[1:], start=1):
        try:
            row = {
                "source": original_item.get("source", "telegram"),
                "source_message_id": original_item["source_message_id"],
                "raw_text": original_item.get("raw_text"),
                "media_type": "image",
                "status": "pending",
                "source_post_id": scrape_info["source_post_id"],
            }
            inserted = supabase_client.table("items").insert(row).execute()
            new_row = inserted.data[0]
            staged = _download_image_to_dropbox(
                dropbox_client, img_url, new_row["id"],
                index=idx, vault_root=vault_root, project="_inbox",
            )
            supabase_client.table("items").update(
                {"media_dropbox_path": staged}
            ).eq("id", new_row["id"]).execute()
            new_row["media_dropbox_path"] = staged
            new_items.append(new_row)
        except Exception:
            logger.exception("fan-out failed for image %d of source_post %s", idx, scrape_info.get("source_post_id"))
    return new_items


def run_batch(
    *,
    supabase_client,
    anthropic_client,
    dropbox_client_factory,
    openai_api_key: str,
    todoist_token: str,
    todoist_projects: dict,
    vault_root: str,
    rules_md: str,
    tag_vocab_md: str,
    apify_api_token: str | None = None,
    apify_tweet_scraper_actor: str | None = None,
    trigger: str = "on-demand",
    limit: int = 50,
    daily_cap_cents: int | None = None,
    today_already_spent_cents: int = 0,
) -> dict:
    started = datetime.now(timezone.utc)

    pending = fetch_pending_items(supabase_client, limit=limit)
    corrections = fetch_recent_corrections(supabase_client, limit=30)
    system_blocks = build_classifier_system_prompt(rules_md, tag_vocab_md, corrections)

    counts = {"items_processed": 0, "items_needs_review": 0, "items_failed": 0, "total_cost_cents": 0}

    dropbox_client = dropbox_client_factory()

    # Use index-based iteration so fan-out can append to `pending` safely.
    idx = 0
    halted_at_cap = False
    while idx < len(pending):
        # Session 5 cap check — fires BEFORE each item so we halt within ≤1
        # item-cost of the cap. /process passes daily_cap_cents=None to bypass.
        if daily_cap_cents is not None:
            projected = today_already_spent_cents + counts["total_cost_cents"]
            if projected >= daily_cap_cents:
                halted_at_cap = True
                break

        item = pending[idx]
        idx += 1

        result = process_item(
            item,
            anthropic_client=anthropic_client,
            dropbox_client=dropbox_client,
            openai_api_key=openai_api_key,
            todoist_token=todoist_token,
            todoist_projects=todoist_projects,
            vault_root=vault_root,
            system_blocks=system_blocks,
            supabase_client=supabase_client,
            apify_api_token=apify_api_token,
            apify_tweet_scraper_actor=apify_tweet_scraper_actor,
        )

        counts["total_cost_cents"] += result["api_cost_cents"] or 0
        if result["status"] == "failed":
            counts["items_failed"] += 1
            try:
                supabase_client.table("items").update(
                    {
                        "status": "failed",
                        "error": result["error"],
                        "processed_at": datetime.now(timezone.utc).isoformat(),
                    }
                ).eq("id", item["id"]).execute()
            except Exception:
                logger.exception("could not mark item %s failed", item["id"])
            continue

        if result["status"] == "needs_review":
            counts["items_needs_review"] += 1
        else:
            counts["items_processed"] += 1

        try:
            update_classified(
                supabase_client,
                item_id=item["id"],
                classification=result["classification"],
                obsidian_path=result["obsidian_path"],
                todoist_task_id=result["todoist_task_id"],
                api_cost_cents=result["api_cost_cents"],
                status=result["status"],
                source_post_id=result.get("source_post_id"),
                media_dropbox_path=result.get("media_dropbox_path"),
            )
        except Exception:
            logger.exception("update_classified failed for %s", item["id"])

        # Multi-image fan-out: append additional items to the queue so they're
        # processed in this same batch run, sharing the source_post_id.
        scrape_info = result.get("scrape_info")
        if scrape_info and len(scrape_info.get("image_urls", [])) > 1:
            try:
                extra = _fan_out_additional_items_from_scrape(
                    supabase_client, dropbox_client, item, scrape_info, vault_root,
                )
                pending.extend(extra)
            except Exception:
                logger.exception("fan-out wrapper failed for item %s", item["id"])

    completed = datetime.now(timezone.utc)

    try:
        insert_run(
            supabase_client,
            trigger=trigger,
            items_processed=counts["items_processed"],
            items_needs_review=counts["items_needs_review"],
            items_failed=counts["items_failed"],
            total_cost_cents=counts["total_cost_cents"],
            started_at=started,
            completed_at=completed,
        )
    except Exception:
        logger.exception("insert_run failed")

    counts["duration_seconds"] = (completed - started).total_seconds()
    counts["halted_at_cap"] = halted_at_cap
    counts["items_remaining_pending"] = max(0, len(pending) - idx)
    return counts
