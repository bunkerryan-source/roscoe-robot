import pytest
from bot.intake import parse_update


def test_text(load_fixture):
    result = parse_update(load_fixture("update_text"))
    assert result["media_type"] == "text"
    assert result["raw_text"] == "Remember to call Walmart contact"
    assert result["media_telegram_file_id"] is None


def test_link_detected_from_url_only_text(load_fixture):
    result = parse_update(load_fixture("update_link"))
    assert result["media_type"] == "link"
    assert result["raw_text"] == "https://x.com/somebody/status/123"
    assert result["media_telegram_file_id"] is None


def test_photo_with_caption_picks_largest_size(load_fixture):
    result = parse_update(load_fixture("update_photo_with_caption"))
    assert result["media_type"] == "image"
    assert result["raw_text"] == "for Lake Arrowhead bathroom"
    assert result["media_telegram_file_id"] == "large_file_id"


def test_photo_without_caption(load_fixture):
    result = parse_update(load_fixture("update_photo_no_caption"))
    assert result["media_type"] == "image"
    assert result["raw_text"] is None
    assert result["media_telegram_file_id"] == "large_file_id"


def test_video(load_fixture):
    result = parse_update(load_fixture("update_video"))
    assert result["media_type"] == "video"
    assert result["raw_text"] == "design tutorial - watch later"
    assert result["media_telegram_file_id"] == "video_file_id"


def test_voice(load_fixture):
    result = parse_update(load_fixture("update_voice"))
    assert result["media_type"] == "voice"
    assert result["raw_text"] is None
    assert result["media_telegram_file_id"] == "voice_file_id"


def test_forward_preserves_text(load_fixture):
    result = parse_update(load_fixture("update_forward"))
    assert result["media_type"] == "forward"
    assert "Forwarded important article" in result["raw_text"]
    assert result["media_telegram_file_id"] is None


def test_empty_update_raises():
    with pytest.raises(ValueError, match="no message"):
        parse_update({"update_id": 1})


def test_unsupported_message_content_raises():
    update = {
        "update_id": 1,
        "message": {
            "message_id": 1,
            "from": {"id": 12345},
            "chat": {"id": 12345, "type": "private"},
            "date": 0,
            "sticker": {"file_id": "sticker1"},
        },
    }
    with pytest.raises(ValueError, match="unsupported"):
        parse_update(update)
