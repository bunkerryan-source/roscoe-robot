import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from bot.llm import (
    build_classifier_system_prompt,
    classify_item,
    cost_cents_from_usage,
)


# Haiku 4.5 pricing (per 1M tokens): input $1, output $5,
# cache write $1.25, cache read $0.10. Source: anthropic.com/pricing.
# We compute cost_cents = ceil(total_usd * 100).


def test_cost_cents_only_input_and_output():
    usage = {
        "input_tokens": 1_000,
        "output_tokens": 200,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 0,
    }
    # 1000 * $1 / 1M + 200 * $5 / 1M = $0.001 + $0.001 = $0.002 = 0.2 cents → 1 cent
    assert cost_cents_from_usage(usage) == 1


def test_cost_cents_with_cache_read():
    usage = {
        "input_tokens": 500,
        "output_tokens": 150,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 5_000,
    }
    # input  500   * $1.00 / 1M = $0.0005
    # output 150   * $5.00 / 1M = $0.00075
    # cache_r 5000 * $0.10 / 1M = $0.0005
    # total = $0.00175 → 0.175 cents → 1 cent (ceil)
    assert cost_cents_from_usage(usage) == 1


def test_cost_cents_with_cache_creation():
    usage = {
        "input_tokens": 500,
        "output_tokens": 150,
        "cache_creation_input_tokens": 5_000,
        "cache_read_input_tokens": 0,
    }
    # input  500   * $1.00 / 1M = $0.0005
    # output 150   * $5.00 / 1M = $0.00075
    # cache_w 5000 * $1.25 / 1M = $0.00625
    # total = $0.0075 → 0.75 cents → 1 cent (ceil)
    assert cost_cents_from_usage(usage) == 1


def test_cost_cents_handles_missing_keys():
    usage = {"input_tokens": 1_000_000, "output_tokens": 0}
    # 1M * $1 / 1M = $1.00 = 100 cents
    assert cost_cents_from_usage(usage) == 100


def test_system_prompt_has_two_blocks():
    blocks = build_classifier_system_prompt(
        rules_md="rule one\nrule two",
        tag_vocab_md="### claude-build\n- ai\n- mcp",
        recent_corrections=[],
    )
    assert len(blocks) == 2
    assert blocks[0]["type"] == "text"
    assert blocks[1]["type"] == "text"


def test_system_prompt_static_block_is_cached():
    blocks = build_classifier_system_prompt(
        rules_md="r",
        tag_vocab_md="t",
        recent_corrections=[],
    )
    assert blocks[0].get("cache_control") == {"type": "ephemeral"}


def test_system_prompt_dynamic_block_is_not_cached():
    blocks = build_classifier_system_prompt(
        rules_md="r",
        tag_vocab_md="t",
        recent_corrections=[],
    )
    assert "cache_control" not in blocks[1]


def test_system_prompt_static_block_includes_seven_projects():
    blocks = build_classifier_system_prompt(
        rules_md="",
        tag_vocab_md="",
        recent_corrections=[],
    )
    text = blocks[0]["text"]
    for p in ["acute", "abp", "lake-arrowhead", "church", "claude-build", "design", "personal"]:
        assert p in text


def test_system_prompt_static_block_includes_rules_and_vocab():
    blocks = build_classifier_system_prompt(
        rules_md="my-rule-marker-xyz",
        tag_vocab_md="my-tag-marker-abc",
        recent_corrections=[],
    )
    text = blocks[0]["text"]
    assert "my-rule-marker-xyz" in text
    assert "my-tag-marker-abc" in text


def test_system_prompt_dynamic_block_includes_corrections():
    corrections = [
        {
            "original_class": {"project": "personal", "type": "idea"},
            "corrected_class": {"project": "claude-build", "type": "todo"},
        },
    ]
    blocks = build_classifier_system_prompt(
        rules_md="",
        tag_vocab_md="",
        recent_corrections=corrections,
    )
    dyn = blocks[1]["text"]
    assert "personal" in dyn and "claude-build" in dyn
    assert "idea" in dyn and "todo" in dyn


def test_system_prompt_dynamic_block_handles_empty_corrections():
    blocks = build_classifier_system_prompt(
        rules_md="",
        tag_vocab_md="",
        recent_corrections=[],
    )
    assert isinstance(blocks[1]["text"], str)
    assert len(blocks[1]["text"]) > 0


def _load_fixture(name: str) -> dict:
    return json.loads(
        (Path(__file__).parent / "fixtures" / name).read_text(encoding="utf-8")
    )


def test_classify_item_returns_parsed_json_plus_cost():
    fixture = _load_fixture("classifier_response_text.json")

    fake_message = MagicMock()
    fake_message.content = [MagicMock(type="text", text=fixture["content"][0]["text"])]
    fake_message.usage = MagicMock(
        input_tokens=fixture["usage"]["input_tokens"],
        output_tokens=fixture["usage"]["output_tokens"],
        cache_creation_input_tokens=fixture["usage"]["cache_creation_input_tokens"],
        cache_read_input_tokens=fixture["usage"]["cache_read_input_tokens"],
    )

    fake_client = MagicMock()
    fake_client.messages.create.return_value = fake_message

    system_blocks = [
        {"type": "text", "text": "static", "cache_control": {"type": "ephemeral"}},
        {"type": "text", "text": "dynamic"},
    ]
    payload = "Idea: build an MCP server for Todoist."

    result = classify_item(fake_client, system_blocks, payload)

    assert result["project"] == "claude-build"
    assert result["type"] == "idea"
    assert "ai" in result["tags"]
    assert result["confidence"] == 0.88
    assert isinstance(result["_cost_cents"], int)
    assert result["_cost_cents"] >= 1


def test_classify_item_passes_system_blocks_through():
    fake_message = MagicMock()
    fake_message.content = [MagicMock(
        type="text",
        text='{"project":"personal","subdomain":null,"type":"idea","tags":[],"visual_subtype":null,"summary":"x","confidence":0.5}',
    )]
    fake_message.usage = MagicMock(
        input_tokens=10, output_tokens=10,
        cache_creation_input_tokens=0, cache_read_input_tokens=0,
    )
    fake_client = MagicMock()
    fake_client.messages.create.return_value = fake_message

    system_blocks = [
        {"type": "text", "text": "STATIC_MARKER", "cache_control": {"type": "ephemeral"}},
        {"type": "text", "text": "DYN_MARKER"},
    ]

    classify_item(fake_client, system_blocks, "x")

    call_kwargs = fake_client.messages.create.call_args.kwargs
    assert call_kwargs["system"] == system_blocks
    assert call_kwargs["model"] == "claude-haiku-4-5-20251001"
    assert call_kwargs["messages"][0]["content"] == "x"


def test_classify_item_raises_on_non_json_response():
    fake_message = MagicMock()
    fake_message.content = [MagicMock(type="text", text="I cannot classify this item.")]
    fake_message.usage = MagicMock(
        input_tokens=10, output_tokens=10,
        cache_creation_input_tokens=0, cache_read_input_tokens=0,
    )
    fake_client = MagicMock()
    fake_client.messages.create.return_value = fake_message

    with pytest.raises(ValueError, match="not valid JSON"):
        classify_item(fake_client, [{"type": "text", "text": "s"}], "x")
