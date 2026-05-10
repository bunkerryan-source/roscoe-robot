"""Anthropic API wrapper for the Personal OS classifier.

This module is the only one that imports `anthropic`. It exposes:

- `cost_cents_from_usage(usage)` — convert an Anthropic usage block to cents.
- `build_classifier_system_prompt(rules_md, tag_vocab_md, recent_corrections)` —
  assemble the two-block system prompt (static cached + dynamic uncached).
- `classify_item(client, system_blocks, payload)` — send one classify request
  and return parsed JSON plus `_cost_cents`.
"""
import json
import math
import re


# Strips a leading ```json or ``` and a trailing ```. Models sometimes wrap
# JSON in markdown fences despite being told not to.
_FENCE_RE = re.compile(r"^```(?:json)?\s*\n?(.*?)\n?```$", re.DOTALL)
# Last-ditch fallback: extract the first `{...}` object from arbitrary text.
_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)


def _extract_json(raw: str) -> str:
    """Return a string the json parser can consume. Tries: raw, fence-stripped,
    first {...} object. Caller still decides whether the result parses."""
    s = raw.strip()
    m = _FENCE_RE.match(s)
    if m:
        return m.group(1).strip()
    m = _OBJECT_RE.search(s)
    if m:
        return m.group(0).strip()
    return s


# Haiku 4.5 prices in USD per 1M tokens.
HAIKU_INPUT_PRICE = 1.00
HAIKU_OUTPUT_PRICE = 5.00
HAIKU_CACHE_WRITE_PRICE = 1.25
HAIKU_CACHE_READ_PRICE = 0.10


def cost_cents_from_usage(usage: dict) -> int:
    """Convert an Anthropic usage block to integer cents (ceiling)."""
    input_t = usage.get("input_tokens", 0)
    output_t = usage.get("output_tokens", 0)
    cache_w = usage.get("cache_creation_input_tokens", 0)
    cache_r = usage.get("cache_read_input_tokens", 0)

    usd = (
        input_t * HAIKU_INPUT_PRICE / 1_000_000
        + output_t * HAIKU_OUTPUT_PRICE / 1_000_000
        + cache_w * HAIKU_CACHE_WRITE_PRICE / 1_000_000
        + cache_r * HAIKU_CACHE_READ_PRICE / 1_000_000
    )
    return math.ceil(usd * 100)


STATIC_INSTRUCTIONS_HEADER = """\
You are the Personal OS classifier for Ryan Bunker. Given one captured item,
output a single JSON object describing how to file it. Output ONLY the JSON,
no prose, no markdown fences.

# Projects (pick exactly one)

- acute: Acute Logistics — sales, operations, freight content, customer research.
- abp: ABP Capital and C3bank. Use the `subdomain` field to specify "c3bank" or "abp".
- lake-arrowhead: Lake Arrowhead cabin and personal real estate.
- church: Come Follow Me lessons, talks, gospel study, family history.
- claude-build: Tools and automations being built (incl. this Personal OS).
- design: Visual inspiration library — heroes, nav, typography, brand, etc.
- personal: Catch-all — surfing, woodworking, family, miscellaneous.

# Type (pick exactly one)

- article, video, image, todo, idea, voice, link

# Visual subtype (only set when type=image; otherwise null)

- hero, nav, pricing-page, dashboard, typography, color-palette, branding,
  mobile, dark-mode, minimalist, brutalist, or a new descriptor if needed.

# Output schema

```json
{
  "project": "acute" | "abp" | "lake-arrowhead" | "church" | "claude-build" | "design" | "personal",
  "subdomain": null | "c3bank" | "abp",
  "type": "article" | "video" | "image" | "todo" | "idea" | "voice" | "link",
  "tags": ["..."],
  "visual_subtype": null | "hero" | ...,
  "summary": "1-2 sentence factual summary",
  "confidence": 0.0-1.0
}
```

`confidence` is your honest self-rating: 0.9+ when project + type are obvious;
0.6-0.8 when reasonable but ambiguous; below 0.6 when you're guessing.
The system surfaces low-confidence items for review.
"""


def build_classifier_system_prompt(
    rules_md: str,
    tag_vocab_md: str,
    recent_corrections: list[dict],
) -> list[dict]:
    """Assemble the classifier system prompt as two cache-aware blocks."""
    static_text = (
        STATIC_INSTRUCTIONS_HEADER
        + "\n\n# Tag vocabulary (prefer these; invent only when nothing fits)\n\n"
        + (tag_vocab_md or "(none)")
        + "\n\n# Learned rules (apply these unless they contradict the input)\n\n"
        + (rules_md or "(none)")
    )

    if recent_corrections:
        examples = []
        for i, c in enumerate(recent_corrections, start=1):
            examples.append(
                f"Example {i}:\n"
                f"  Originally classified as: {json.dumps(c['original_class'])}\n"
                f"  User corrected to:        {json.dumps(c['corrected_class'])}"
            )
        dynamic_text = (
            "# Recent corrections (newest first) — learn from these\n\n"
            + "\n\n".join(examples)
        )
    else:
        dynamic_text = "# Recent corrections\n\n(none yet)"

    return [
        {
            "type": "text",
            "text": static_text,
            "cache_control": {"type": "ephemeral"},
        },
        {
            "type": "text",
            "text": dynamic_text,
        },
    ]


def classify_item(
    client,
    system_blocks: list[dict],
    item_payload: str,
    *,
    model: str = "claude-haiku-4-5-20251001",
    max_tokens: int = 600,
) -> dict:
    """Send one classification request and return parsed JSON + cost."""
    response = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=system_blocks,
        messages=[{"role": "user", "content": item_payload}],
    )

    text_blocks = [b.text for b in response.content if b.type == "text"]
    raw = "".join(text_blocks).strip()

    candidate = _extract_json(raw)
    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError as e:
        raise ValueError(f"classifier response is not valid JSON: {raw[:200]}") from e

    parsed["_cost_cents"] = cost_cents_from_usage({
        "input_tokens": response.usage.input_tokens,
        "output_tokens": response.usage.output_tokens,
        "cache_creation_input_tokens": getattr(response.usage, "cache_creation_input_tokens", 0) or 0,
        "cache_read_input_tokens": getattr(response.usage, "cache_read_input_tokens", 0) or 0,
    })
    return parsed
