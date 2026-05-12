from bot.summary import build_evening_summary, build_morning_summary


def _item(project, type_, status, summary="x"):
    return {"project": project, "type": type_, "status": status, "summary": summary}


def test_morning_summary_groups_by_project_with_counts():
    items = [
        _item("acute", "todo", "processed", "follow up vendor"),
        _item("acute", "article", "processed", "freight pricing"),
        _item("design", "image", "processed", "warm beige hero"),
        _item("design", "image", "needs_review", "unclear"),
    ]
    out = build_morning_summary(items, total_cost_cents=12)

    assert "acute (2)" in out
    assert "design (2)" in out
    assert "$0.12" in out


def test_morning_summary_calls_out_todos():
    items = [
        _item("acute", "todo", "processed"),
        _item("acute", "todo", "processed"),
        _item("design", "image", "processed"),
    ]
    out = build_morning_summary(items, total_cost_cents=3)

    assert "acute" in out
    assert "todo" in out.lower()  # somewhere in the line for acute, "2 todos"


def test_morning_summary_flags_needs_review_count():
    items = [
        _item("personal", "todo", "processed"),
        _item("personal", "image", "needs_review"),
        _item("design", "image", "needs_review"),
    ]
    out = build_morning_summary(items, total_cost_cents=4)

    assert "2" in out  # two needs_review items
    assert "review" in out.lower()


def test_morning_summary_empty_returns_nothing_processed_message():
    out = build_morning_summary([], total_cost_cents=0)
    assert "nothing processed" in out.lower()


def test_evening_summary_lists_today_captures():
    items = [_item("personal", "todo", "processed", "call dentist")]
    out = build_evening_summary(items, total_cost_cents=2)

    assert "personal" in out
    assert "$0.02" in out


def test_evening_summary_empty_returns_nothing_captured_message():
    out = build_evening_summary([], total_cost_cents=0)
    assert "nothing" in out.lower()


def test_morning_summary_handles_singular_needs_review_grammar():
    items = [_item("design", "image", "needs_review")]
    out = build_morning_summary(items, total_cost_cents=1)

    # Should not say "1 items" — should be "1 item"
    assert "1 item" in out
    assert "1 items" not in out


def test_morning_summary_includes_total_item_count():
    items = [
        _item("acute", "todo", "processed"),
        _item("design", "image", "processed"),
        _item("personal", "idea", "processed"),
    ]
    out = build_morning_summary(items, total_cost_cents=5)

    # Total items count should appear somewhere
    assert "3 items" in out or "3 processed" in out
