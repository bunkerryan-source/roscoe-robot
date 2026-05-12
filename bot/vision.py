"""Second-pass vision classifier for design+image items.

Called only when the text-only classification returns project=design AND
type=image. Returns refined visual_subtype, tags, and summary, plus the
API cost. Returns None if the model produced unparseable output — caller
keeps the text-only classification in that case.
"""
from __future__ import annotations

import base64
import json
from dataclasses import dataclass

from bot.llm import _extract_json, cost_cents_from_usage


@dataclass
class VisionRefinement:
    visual_subtype: str | None
    tags: list[str]
    summary: str
    cost_cents: int


VISION_SYSTEM_PROMPT = """You are refining a design-inspiration capture by looking at the image.

The prior text-only pass already determined this is project=design, type=image. Your job is to look at the image and produce:
- visual_subtype: one of [hero, nav, pricing, dashboard, typography, color-palette, branding, mobile, illustration, photography, layout, other]
- tags: list of 3-8 design-relevant tags (color palette, layout pattern, era, mood, etc.)
- summary: 1-2 sentence description of what makes this image notable as design inspiration

Return ONLY a single JSON object with those three keys. No prose, no markdown fences."""


def refine_with_vision(
    client,
    *,
    image_bytes: bytes,
    text_context: str,
    scraped_post_text: str,
    prior_classification: dict,
    model: str = "claude-haiku-4-5-20251001",
    max_tokens: int = 400,
) -> VisionRefinement | None:
    """Run a vision pass over the image and return a refinement, or None if unparseable."""
    user_text_parts: list[str] = []
    if text_context:
        user_text_parts.append(f"User caption: {text_context}")
    if scraped_post_text:
        user_text_parts.append(f"Source post text: {scraped_post_text}")
    user_text_parts.append(f"Prior classification: {json.dumps(prior_classification)}")
    user_text = "\n\n".join(user_text_parts)

    image_b64 = base64.b64encode(image_bytes).decode("ascii")

    response = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=VISION_SYSTEM_PROMPT,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/jpeg",
                            "data": image_b64,
                        },
                    },
                    {"type": "text", "text": user_text},
                ],
            }
        ],
    )

    cost = cost_cents_from_usage({
        "input_tokens": response.usage.input_tokens,
        "output_tokens": response.usage.output_tokens,
        "cache_creation_input_tokens": getattr(response.usage, "cache_creation_input_tokens", 0) or 0,
        "cache_read_input_tokens": getattr(response.usage, "cache_read_input_tokens", 0) or 0,
    })

    text_blocks = [b.text for b in response.content if getattr(b, "type", None) == "text" or hasattr(b, "text")]
    raw = "".join(text_blocks).strip()

    try:
        parsed = json.loads(_extract_json(raw))
    except (json.JSONDecodeError, ValueError):
        return None

    if not isinstance(parsed, dict):
        return None

    return VisionRefinement(
        visual_subtype=parsed.get("visual_subtype"),
        tags=parsed.get("tags") or [],
        summary=parsed.get("summary") or "",
        cost_cents=cost,
    )
