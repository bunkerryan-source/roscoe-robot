from unittest.mock import MagicMock

from bot.vision import VisionRefinement, refine_with_vision


def _fake_response(text: str, *, input_tokens=300, output_tokens=80, cache_read=0, cache_create=0):
    usage = MagicMock(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_creation_input_tokens=cache_create,
        cache_read_input_tokens=cache_read,
    )
    content_block = MagicMock()
    content_block.text = text
    return MagicMock(content=[content_block], usage=usage)


def test_refine_with_vision_returns_refinement_from_response():
    client = MagicMock()
    client.messages.create.return_value = _fake_response(
        '{"visual_subtype":"hero","tags":["editorial","warm-palette"],"summary":"Editorial hero with warm beige palette"}'
    )

    out = refine_with_vision(
        client,
        image_bytes=b"fake-jpg-bytes",
        text_context="design inspo — homepage hero",
        scraped_post_text="warm beige editorial --sref 123",
        prior_classification={"project": "design", "type": "image"},
    )

    assert isinstance(out, VisionRefinement)
    assert out.visual_subtype == "hero"
    assert out.tags == ["editorial", "warm-palette"]
    assert "warm beige" in out.summary
    assert out.cost_cents > 0


def test_refine_with_vision_strips_markdown_fences():
    client = MagicMock()
    client.messages.create.return_value = _fake_response(
        '```json\n{"visual_subtype":"nav","tags":[],"summary":"ok"}\n```'
    )

    out = refine_with_vision(
        client,
        image_bytes=b"x",
        text_context="",
        scraped_post_text="",
        prior_classification={"project": "design", "type": "image"},
    )

    assert out is not None
    assert out.visual_subtype == "nav"
    assert out.tags == []
    assert out.summary == "ok"


def test_refine_with_vision_returns_none_when_response_unparseable():
    client = MagicMock()
    client.messages.create.return_value = _fake_response(
        "I cannot see this image clearly."
    )

    out = refine_with_vision(
        client,
        image_bytes=b"x",
        text_context="",
        scraped_post_text="",
        prior_classification={"project": "design", "type": "image"},
    )

    assert out is None


def test_refine_with_vision_sends_image_content_block():
    """Wire-level check: the API call must include an image content block."""
    client = MagicMock()
    client.messages.create.return_value = _fake_response(
        '{"visual_subtype":"hero","tags":["a"],"summary":"x"}'
    )

    refine_with_vision(
        client,
        image_bytes=b"\xff\xd8\xff\xe0fake-jpeg",
        text_context="caption",
        scraped_post_text="post body",
        prior_classification={"project": "design", "type": "image"},
    )

    call_kwargs = client.messages.create.call_args.kwargs
    msg = call_kwargs["messages"][0]
    assert msg["role"] == "user"
    blocks = msg["content"]
    # one image block + one text block
    assert any(b.get("type") == "image" for b in blocks)
    assert any(b.get("type") == "text" for b in blocks)
    image_block = next(b for b in blocks if b["type"] == "image")
    assert image_block["source"]["type"] == "base64"
    assert image_block["source"]["media_type"] == "image/jpeg"
    text_block = next(b for b in blocks if b["type"] == "text")
    assert "caption" in text_block["text"]
    assert "post body" in text_block["text"]
