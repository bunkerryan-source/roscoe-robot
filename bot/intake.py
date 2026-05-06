from typing import Optional, TypedDict


class IntakeResult(TypedDict):
    raw_text: Optional[str]
    media_type: str
    media_telegram_file_id: Optional[str]


def parse_update(update: dict) -> IntakeResult:
    msg = update.get("message") or update.get("channel_post")
    if not msg:
        raise ValueError("update has no message")

    is_forward = any(k in msg for k in ("forward_origin", "forward_from", "forward_from_chat"))
    if is_forward:
        return {
            "raw_text": msg.get("text") or msg.get("caption"),
            "media_type": "forward",
            "media_telegram_file_id": None,
        }

    if "photo" in msg:
        largest = max(msg["photo"], key=lambda p: p.get("file_size", 0))
        return {
            "raw_text": msg.get("caption"),
            "media_type": "image",
            "media_telegram_file_id": largest["file_id"],
        }

    if "video" in msg:
        return {
            "raw_text": msg.get("caption"),
            "media_type": "video",
            "media_telegram_file_id": msg["video"]["file_id"],
        }

    if "voice" in msg:
        return {
            "raw_text": msg.get("caption"),
            "media_type": "voice",
            "media_telegram_file_id": msg["voice"]["file_id"],
        }

    if "document" in msg:
        return {
            "raw_text": msg.get("caption"),
            "media_type": "document",
            "media_telegram_file_id": msg["document"]["file_id"],
        }

    if "text" in msg:
        text = msg["text"]
        stripped = text.strip()
        if stripped.startswith(("http://", "https://")) and " " not in stripped:
            return {
                "raw_text": text,
                "media_type": "link",
                "media_telegram_file_id": None,
            }
        return {
            "raw_text": text,
            "media_type": "text",
            "media_telegram_file_id": None,
        }

    raise ValueError("update has unsupported content")
