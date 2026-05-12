"""Seed and clean up throwaway needs_review items for triage smoke tests.

All test rows are marked with raw_text starting with [TRIAGE-TEST] so the
cleanup pass can find and remove them deterministically. Corrections rows
written against test items during a tap walk-through are also removed.

Usage (run from /opt/personal-os-v2 on the droplet, or repo root locally):
    venv/bin/python scripts/triage_test.py seed
    venv/bin/python scripts/triage_test.py cleanup
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

# Make `bot/` importable when this script is run as scripts/triage_test.py —
# Python only adds the script's own directory to sys.path, not the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

from bot.config import Config  # noqa: E402
from bot.db import get_client  # noqa: E402

TEST_MARKER = "[TRIAGE-TEST]"


def _build_items() -> list[dict]:
    now = datetime.now(timezone.utc).isoformat()
    return [
        {
            "status": "needs_review",
            "source": "telegram",
            "source_message_id": f"triage-test-{int(datetime.now().timestamp())}-1",
            "raw_text": f"{TEST_MARKER} tap Keep — this design hero is classified correctly",
            "media_type": "text",
            "project": "design",
            "type": "image",
            "tags": ["test", "hero"],
            "summary": "Test item — design hero meant for the Keep button",
            "confidence": 0.55,
            "processed_at": now,
            "classified_by": "triage-test-seed",
            "api_cost_cents": 0,
        },
        {
            "status": "needs_review",
            "source": "telegram",
            "source_message_id": f"triage-test-{int(datetime.now().timestamp())}-2",
            "raw_text": f"{TEST_MARKER} tap Refile then pick 'design' — bot misfiled as claude-build",
            "media_type": "text",
            "project": "claude-build",
            "type": "link",
            "tags": ["test"],
            "summary": "Test item — should have been design, tap Refile",
            "confidence": 0.45,
            "processed_at": now,
            "classified_by": "triage-test-seed",
            "api_cost_cents": 0,
        },
        {
            "status": "needs_review",
            "source": "telegram",
            "source_message_id": f"triage-test-{int(datetime.now().timestamp())}-3",
            "raw_text": f"{TEST_MARKER} tap Todo — should have been a todo, not a note",
            "media_type": "text",
            "project": "acute",
            "type": "note",
            "tags": ["test"],
            "summary": "Test item — convert to todo via Todo button",
            "confidence": 0.50,
            "processed_at": now,
            "classified_by": "triage-test-seed",
            "api_cost_cents": 0,
        },
        {
            "status": "needs_review",
            "source": "telegram",
            "source_message_id": f"triage-test-{int(datetime.now().timestamp())}-4",
            "raw_text": f"{TEST_MARKER} tap Discard — this is junk and should be trashed",
            "media_type": "text",
            "project": "personal",
            "type": "link",
            "tags": ["test"],
            "summary": "Test item — discard me",
            "confidence": 0.40,
            "processed_at": now,
            "classified_by": "triage-test-seed",
            "api_cost_cents": 0,
        },
    ]


def seed() -> None:
    config = Config.from_env()
    sb = get_client(config.supabase_url, config.supabase_service_key)
    items = _build_items()
    inserted = []
    for it in items:
        r = sb.table("items").insert(it).execute()
        row_id = r.data[0]["id"]
        inserted.append((row_id, it["raw_text"]))
    print(f"Inserted {len(inserted)} test items:")
    for row_id, text in inserted:
        print(f"  {row_id}  {text}")


def cleanup() -> None:
    config = Config.from_env()
    sb = get_client(config.supabase_url, config.supabase_service_key)

    rows = (
        sb.table("items")
        .select("id, raw_text")
        .like("raw_text", f"{TEST_MARKER}%")
        .execute()
        .data
        or []
    )
    if not rows:
        print("No triage-test items found.")
        return

    ids = [r["id"] for r in rows]
    corr_deleted = (
        sb.table("corrections").delete().in_("item_id", ids).execute().data or []
    )
    items_deleted = (
        sb.table("items").delete().in_("id", ids).execute().data or []
    )
    print(f"Deleted {len(items_deleted)} test items and {len(corr_deleted)} corrections rows.")
    for r in rows:
        print(f"  removed: {r['id']}  {r['raw_text'][:80]}")


def main() -> int:
    if len(sys.argv) != 2 or sys.argv[1] not in {"seed", "cleanup"}:
        print(__doc__)
        return 1
    if sys.argv[1] == "seed":
        seed()
    else:
        cleanup()
    return 0


if __name__ == "__main__":
    sys.exit(main())
