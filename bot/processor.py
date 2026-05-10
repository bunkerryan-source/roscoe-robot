"""Per-item processing orchestrator. Pure-Python; no FastAPI imports.

This module is the spec's heart. It exposes:

- `enrich_item(item, ...)` — produce the classifier payload + needs_vision flag.
- `process_item(item, ...)` — full per-item pipeline. Never raises.
- `run_batch(...)` — drain the pending queue. Failures isolate per item.
"""
import logging
from datetime import datetime, timezone

from bot.db import (
    fetch_pending_items,
    fetch_recent_corrections,
    insert_run,
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


def _post_classify_dropbox_path(item_id: str, project: str, type_: str, original_path: str) -> str:
    """Compute where media should live after classification.
    Design captures → /inspiration/<project>/. Project media → /projects/<project>/media/."""
    ext = "." + original_path.rsplit(".", 1)[-1] if "." in original_path else ""
    if project == "design":
        return f"/inspiration/design/{item_id}{ext}"
    return f"/projects/{project}/media/{item_id}{ext}"


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
) -> dict:
    """Run the full pipeline for one item. Never raises — failures land in the result."""
    out = {
        "status": "processed",
        "classification": None,
        "obsidian_path": None,
        "todoist_task_id": None,
        "api_cost_cents": 0,
        "error": None,
    }

    try:
        payload, _needs_vision = enrich_item(
            item,
            openai_api_key=openai_api_key,
            dropbox_client=dropbox_client,
        )
        # Note: vision-call wiring is deferred to Session 4. For now we send
        # text-only and rely on the caption.

        if not payload.strip():
            payload = (
                f"[{item.get('media_type', 'unknown')} item with no caption "
                f"or transcript — classify as best-guess from media_type]"
            )

        classification = classify_item(anthropic_client, system_blocks, payload)
        out["classification"] = classification
        out["api_cost_cents"] = classification.get("_cost_cents", 0)

        new_media_path = item.get("media_dropbox_path")
        if item.get("media_dropbox_path") and item["media_type"] in ("image", "video", "voice"):
            try:
                new_media_path = _post_classify_dropbox_path(
                    item["id"],
                    classification.get("project") or "personal",
                    classification.get("type") or "image",
                    item["media_dropbox_path"],
                )
                move_dropbox_media(
                    dropbox_client,
                    from_path=item["media_dropbox_path"],
                    to_path=new_media_path,
                )
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
    trigger: str = "on-demand",
    limit: int = 50,
) -> dict:
    started = datetime.now(timezone.utc)

    pending = fetch_pending_items(supabase_client, limit=limit)
    corrections = fetch_recent_corrections(supabase_client, limit=30)
    system_blocks = build_classifier_system_prompt(rules_md, tag_vocab_md, corrections)

    counts = {"items_processed": 0, "items_needs_review": 0, "items_failed": 0, "total_cost_cents": 0}

    dropbox_client = dropbox_client_factory()

    for item in pending:
        result = process_item(
            item,
            anthropic_client=anthropic_client,
            dropbox_client=dropbox_client,
            openai_api_key=openai_api_key,
            todoist_token=todoist_token,
            todoist_projects=todoist_projects,
            vault_root=vault_root,
            system_blocks=system_blocks,
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
            )
        except Exception:
            logger.exception("update_classified failed for %s", item["id"])

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
    return counts
