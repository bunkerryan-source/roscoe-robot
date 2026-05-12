"""Daily-summary message text for Telegram delivery.

Pure functions over already-fetched item rows. Caller (bot/main.py) handles
the date math, Supabase query, and Telegram POST.
"""
from __future__ import annotations

from collections import defaultdict


def _format_cost(cents: int) -> str:
    return f"${cents / 100:.2f}"


def _group_by_project(items: list[dict]) -> dict[str, list[dict]]:
    by_project: dict[str, list[dict]] = defaultdict(list)
    for it in items:
        by_project[it.get("project") or "unknown"].append(it)
    return dict(by_project)


def _pluralize(count: int, singular: str, plural: str | None = None) -> str:
    plural = plural or singular + "s"
    return f"{count} {singular if count == 1 else plural}"


def build_morning_summary(items: list[dict], total_cost_cents: int) -> str:
    if not items:
        return "\U0001F305 Morning brief — nothing processed overnight."

    lines = ["\U0001F305 Morning brief"]
    by_project = _group_by_project(items)
    for project, project_items in sorted(by_project.items()):
        todos = sum(1 for i in project_items if i.get("type") == "todo")
        suffix = f" — {_pluralize(todos, 'todo')}" if todos else ""
        lines.append(f"  • {project} ({len(project_items)}){suffix}")

    needs_review = sum(1 for i in items if i.get("status") == "needs_review")
    lines.append("")
    lines.append(f"{_pluralize(len(items), 'item')} processed · {_format_cost(total_cost_cents)}")
    if needs_review:
        lines.append(f"⚠️  {_pluralize(needs_review, 'item')} needs review")
    return "\n".join(lines)


def build_evening_summary(items: list[dict], total_cost_cents: int) -> str:
    if not items:
        return "\U0001F319 Evening brief — nothing captured today."

    lines = ["\U0001F319 Evening brief"]
    by_project = _group_by_project(items)
    for project, project_items in sorted(by_project.items()):
        lines.append(f"  • {project} ({len(project_items)})")

    needs_review = sum(1 for i in items if i.get("status") == "needs_review")
    lines.append("")
    lines.append(f"{_pluralize(len(items), 'item')} today · {_format_cost(total_cost_cents)}")
    if needs_review:
        lines.append(f"⚠️  {_pluralize(needs_review, 'item')} needs review")
    return "\n".join(lines)
