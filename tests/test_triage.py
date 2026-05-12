from bot.triage import (
    PROJECTS,
    build_item_action_keyboard,
    build_project_picker_keyboard,
    build_review_keyboard,
    parse_callback_data,
)


def test_review_keyboard_has_review_button_when_items_pending():
    kb = build_review_keyboard(needs_review_count=3)
    buttons = kb["inline_keyboard"][0]
    assert buttons[0]["text"] == "Review 3 items"
    assert buttons[0]["callback_data"] == "review:start"


def test_review_keyboard_singular_when_one_item():
    kb = build_review_keyboard(needs_review_count=1)
    assert kb["inline_keyboard"][0][0]["text"] == "Review 1 item"


def test_review_keyboard_returns_none_when_zero():
    assert build_review_keyboard(0) is None


def test_review_keyboard_returns_none_when_negative():
    assert build_review_keyboard(-1) is None


def test_item_action_keyboard_has_all_four_buttons():
    kb = build_item_action_keyboard("item-uuid-123")
    rows = kb["inline_keyboard"]
    flat = [b for row in rows for b in row]
    callbacks = [b["callback_data"] for b in flat]
    assert "keep:item-uuid-123" in callbacks
    assert "refile:item-uuid-123" in callbacks
    assert "todo:item-uuid-123" in callbacks
    assert "discard:item-uuid-123" in callbacks
    assert len(flat) == 4


def test_project_picker_keyboard_contains_all_projects():
    kb = build_project_picker_keyboard("abc-123")
    rows = kb["inline_keyboard"]
    flat = [b for row in rows for b in row]
    texts = [b["text"] for b in flat]
    for p in PROJECTS:
        assert p in texts
    for b in flat:
        assert b["callback_data"].startswith("setproj:abc-123:")


def test_parse_callback_data_round_trip():
    assert parse_callback_data("refile:abc-123") == ("refile", "abc-123")
    assert parse_callback_data("review:start") == ("review", "start")


def test_parse_callback_data_payload_with_colon():
    # setproj uses a two-part payload "<item_id>:<project>" — partition must
    # split only on the first colon so payload itself can contain colons.
    assert parse_callback_data("setproj:abc-123:design") == ("setproj", "abc-123:design")


def test_parse_callback_data_no_payload():
    assert parse_callback_data("noop") == ("noop", "")
