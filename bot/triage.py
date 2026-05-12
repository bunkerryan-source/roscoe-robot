"""Telegram inline-keyboard builders and callback-data parsing for the triage UI.

Pure helpers — no Supabase, no Telegram I/O. Caller (bot/main.py) owns the
async work and the state changes; this module just shapes the JSON Telegram
expects and the small string format we use for callback_data.

callback_data wire format: "<action>:<payload>". Payload may itself contain
colons (the setproj action uses "<item_id>:<project>"), so we partition on
the first colon only.
"""
from __future__ import annotations

PROJECTS = [
    "acute",
    "abp",
    "lake-arrowhead",
    "church",
    "claude-build",
    "design",
    "personal",
]


def build_review_keyboard(needs_review_count: int) -> dict | None:
    """Single-button keyboard appended to daily summaries when items await triage.

    Returns None when there's nothing to review so the caller can simply skip
    `reply_markup` rather than render an empty row.
    """
    if needs_review_count <= 0:
        return None
    suffix = "s" if needs_review_count != 1 else ""
    label = f"Review {needs_review_count} item{suffix}"
    return {"inline_keyboard": [[{"text": label, "callback_data": "review:start"}]]}


def build_item_action_keyboard(item_id: str) -> dict:
    """Four-button keyboard attached to each item during a review session."""
    return {
        "inline_keyboard": [
            [
                {"text": "✅ Keep", "callback_data": f"keep:{item_id}"},
                {"text": "\U0001F4C2 Refile", "callback_data": f"refile:{item_id}"},
            ],
            [
                {"text": "\U0001F4DD → Todo", "callback_data": f"todo:{item_id}"},
                {"text": "\U0001F5D1 Discard", "callback_data": f"discard:{item_id}"},
            ],
        ]
    }


def build_project_picker_keyboard(item_id: str) -> dict:
    """Two-column grid of project buttons used after the user taps Refile."""
    rows = []
    for i in range(0, len(PROJECTS), 2):
        row = [
            {"text": p, "callback_data": f"setproj:{item_id}:{p}"}
            for p in PROJECTS[i : i + 2]
        ]
        rows.append(row)
    return {"inline_keyboard": rows}


def parse_callback_data(data: str) -> tuple[str, str]:
    """Split callback_data into (action, payload) on the first colon."""
    action, _, payload = data.partition(":")
    return action, payload
