"""One-off migration: move bot-stored media inside the Obsidian vault.

Before this script ran, the bot stored media in three locations OUTSIDE the
vault (/personal-os-inbox/, /inspiration/<project>/, /projects/<project>/media/),
which left Obsidian unable to render image embeds. After this script runs,
all media lives at /personal-os/<project>/_attachments/<uuid>.ext (or
/personal-os/_inbox/<date>/<uuid>.ext for unclassified items), and notes use
Obsidian wiki-link syntax `![[filename]]` which resolves anywhere in the vault.

Usage:
    python scripts/migrate_media_into_vault.py --dry-run    # plan only, no writes
    python scripts/migrate_media_into_vault.py              # execute moves + DB + note rewrites

Run from the repo root with the same .env that the bot uses.
"""
from __future__ import annotations

import argparse
import logging
import re
import sys
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()

from bot.config import Config
from bot.db import get_client
from bot.media import DropboxRefreshClient

logger = logging.getLogger("migrate")
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")


@dataclass
class MovePlan:
    item_id: str
    from_path: str
    to_path: str
    obsidian_path: str | None  # vault-relative, e.g. "design/2026-05-10-foo.md"
    rewrite_note: bool         # True if the note has an embed we need to fix


def compute_new_path(old_path: str, project: str | None, item_id: str) -> str | None:
    """Map an old media path to its new vault-internal home.

    Returns None if the path is already in the new layout (idempotent skip).
    """
    if old_path.startswith("/personal-os/_inbox/") or old_path.startswith("/personal-os/") and "/_attachments/" in old_path:
        return None  # already migrated

    ext = "." + old_path.rsplit(".", 1)[-1] if "." in old_path.rsplit("/", 1)[-1] else ""

    # /personal-os-inbox/<date>/<uuid>.ext → /personal-os/_inbox/<date>/<uuid>.ext
    m = re.match(r"^/personal-os-inbox/([^/]+)/(.+)$", old_path)
    if m:
        date, filename = m.groups()
        if project:
            # already classified — file should live at the project's _attachments
            return f"/personal-os/{project}/_attachments/{item_id}{ext}"
        return f"/personal-os/_inbox/{date}/{filename}"

    # /inspiration/<project>/<filename> → /personal-os/<project>/_attachments/<filename>
    m = re.match(r"^/inspiration/([^/]+)/(.+)$", old_path)
    if m:
        proj, filename = m.groups()
        return f"/personal-os/{proj}/_attachments/{filename}"

    # /projects/<project>/media/<filename> → /personal-os/<project>/_attachments/<filename>
    m = re.match(r"^/projects/([^/]+)/media/(.+)$", old_path)
    if m:
        proj, filename = m.groups()
        return f"/personal-os/{proj}/_attachments/{filename}"

    return None  # unrecognized — leave alone


def build_plans(supabase) -> list[MovePlan]:
    """Read all items with media set and return a list of planned moves."""
    result = (
        supabase.table("items")
        .select("id,media_dropbox_path,obsidian_path,project")
        .not_.is_("media_dropbox_path", "null")
        .execute()
    )
    rows = result.data or []
    plans: list[MovePlan] = []
    for row in rows:
        old = row["media_dropbox_path"]
        new = compute_new_path(old, row.get("project"), row["id"])
        if new is None or new == old:
            continue
        plans.append(MovePlan(
            item_id=row["id"],
            from_path=old,
            to_path=new,
            obsidian_path=row.get("obsidian_path"),
            rewrite_note=bool(row.get("obsidian_path")),
        ))
    return plans


def rewrite_note_embed(dbx, vault_root: str, obsidian_path: str, new_media_path: str) -> bool:
    """Download the note, replace any old-style image embed with a wiki-link to
    the new filename, upload back. Returns True if a change was made.
    """
    full_note_path = f"{vault_root}/{obsidian_path}"
    try:
        _, response = dbx.files_download(full_note_path)
        original = response.content.decode("utf-8")
    except Exception as e:
        logger.warning("could not download note %s: %s", full_note_path, e)
        return False

    new_filename = new_media_path.rsplit("/", 1)[-1]
    # Replace any markdown image embed (![alt](path)) that points outside the vault.
    pattern = re.compile(r"!\[[^\]]*\]\((/[^)]+)\)")
    new_content, n = pattern.subn(f"![[{new_filename}]]", original)
    if n == 0:
        # also handle pre-existing wiki-links that point to the wrong filename
        # (rare; happens if an earlier migration partially ran)
        return False
    try:
        from dropbox.files import WriteMode
        dbx.files_upload(
            f=new_content.encode("utf-8"),
            path=full_note_path,
            mode=WriteMode("overwrite"),
        )
        return True
    except Exception as e:
        logger.error("failed to upload rewritten note %s: %s", full_note_path, e)
        return False


def execute_plan(plan: MovePlan, dbx, supabase, vault_root: str, dry_run: bool) -> None:
    logger.info("[%s] item=%s %s -> %s", "DRY-RUN" if dry_run else "MOVE", plan.item_id, plan.from_path, plan.to_path)
    if dry_run:
        if plan.rewrite_note:
            logger.info("       note rewrite: %s", plan.obsidian_path)
        return

    # 1. Move the file in Dropbox
    try:
        dbx.files_move_v2(from_path=plan.from_path, to_path=plan.to_path, autorename=False)
    except Exception as e:
        msg = str(e).lower()
        if "not_found" in msg:
            logger.warning("       source file missing, skipping: %s", plan.from_path)
            return
        if "to/conflict" in msg or "already" in msg:
            logger.info("       destination already exists, treating as already-moved")
        else:
            logger.error("       move failed: %s", e)
            return

    # 2. Update Supabase
    supabase.table("items").update({"media_dropbox_path": plan.to_path}).eq("id", plan.item_id).execute()

    # 3. Rewrite note if applicable
    if plan.rewrite_note and plan.obsidian_path:
        ok = rewrite_note_embed(dbx, vault_root, plan.obsidian_path, plan.to_path)
        if ok:
            logger.info("       note rewritten: %s", plan.obsidian_path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="show plans without executing")
    args = parser.parse_args()

    config = Config.from_env()
    supabase = get_client(config.supabase_url, config.supabase_service_key)
    dropbox_factory = DropboxRefreshClient(
        config.dropbox_refresh_token,
        config.dropbox_app_key,
        config.dropbox_app_secret,
    )
    dbx = dropbox_factory.get_client()

    plans = build_plans(supabase)
    logger.info("%d move(s) planned", len(plans))
    if not plans:
        logger.info("nothing to migrate; exiting cleanly")
        return 0

    # Summarize by destination pattern
    from collections import Counter
    by_destination = Counter()
    for p in plans:
        if "/_inbox/" in p.to_path:
            by_destination["_inbox (unclassified)"] += 1
        elif "/_attachments/" in p.to_path:
            project = p.to_path.split("/")[2]
            by_destination[f"{project}/_attachments"] += 1
    logger.info("Destinations:")
    for dest, n in sorted(by_destination.items()):
        logger.info("  %s: %d", dest, n)

    note_rewrites = sum(1 for p in plans if p.rewrite_note)
    logger.info("Notes to rewrite: %d", note_rewrites)

    for p in plans:
        execute_plan(p, dbx, supabase, config.obsidian_vault_dropbox_path, dry_run=args.dry_run)

    if args.dry_run:
        logger.info("DRY-RUN complete. Re-run without --dry-run to execute.")
    else:
        logger.info("Migration complete.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
