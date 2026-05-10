import json
from pathlib import Path

import pytest
import respx
from httpx import Response

from bot.enrichment import (
    fetch_og_metadata,
    fetch_youtube_transcript,
    transcribe_voice,
)


FIXTURES = Path(__file__).parent / "fixtures"


@respx.mock
def test_og_metadata_extracts_all_fields_when_present():
    html = (FIXTURES / "og_html_basic.html").read_text(encoding="utf-8")
    respx.get("https://example.com/post").mock(return_value=Response(200, text=html))

    result = fetch_og_metadata("https://example.com/post")

    assert result["title"] == "OG Title — overrides HTML title"
    assert result["description"] == "A short OG description."
    assert result["image"] == "https://example.com/cover.jpg"
    assert result["site_name"] == "Example Blog"


@respx.mock
def test_og_metadata_falls_back_to_html_title_when_no_og():
    html = (FIXTURES / "og_html_no_meta.html").read_text(encoding="utf-8")
    respx.get("https://example.com/bare").mock(return_value=Response(200, text=html))

    result = fetch_og_metadata("https://example.com/bare")

    assert result["title"] == "Just an HTML title"
    assert result["description"] == ""
    assert result["image"] == ""


@respx.mock
def test_og_metadata_returns_empty_dict_on_http_error():
    respx.get("https://example.com/down").mock(return_value=Response(500))

    result = fetch_og_metadata("https://example.com/down")

    assert result == {"title": "", "description": "", "image": "", "site_name": ""}


@respx.mock
def test_og_metadata_handles_network_failure():
    respx.get("https://example.com/timeout").mock(side_effect=Exception("connection refused"))

    result = fetch_og_metadata("https://example.com/timeout")

    assert result == {"title": "", "description": "", "image": "", "site_name": ""}


def test_youtube_transcript_extracts_video_id_from_watch_url(mocker):
    fixture = json.loads((FIXTURES / "youtube_transcript.json").read_text())
    mock_get = mocker.patch(
        "bot.enrichment.YouTubeTranscriptApi.get_transcript",
        return_value=fixture,
    )

    result = fetch_youtube_transcript("https://www.youtube.com/watch?v=abcDEF12345")

    mock_get.assert_called_once_with("abcDEF12345", languages=("en",))
    assert "MCP servers" in result
    assert "Anthropic" in result


def test_youtube_transcript_extracts_video_id_from_short_url(mocker):
    fixture = json.loads((FIXTURES / "youtube_transcript.json").read_text())
    mock_get = mocker.patch(
        "bot.enrichment.YouTubeTranscriptApi.get_transcript",
        return_value=fixture,
    )

    fetch_youtube_transcript("https://youtu.be/abcDEF12345?t=42")

    mock_get.assert_called_once_with("abcDEF12345", languages=("en",))


def test_youtube_transcript_raises_on_unparseable_url():
    with pytest.raises(ValueError, match="could not extract YouTube video ID"):
        fetch_youtube_transcript("https://example.com/not-youtube")


def test_youtube_transcript_raises_on_api_failure(mocker):
    mocker.patch(
        "bot.enrichment.YouTubeTranscriptApi.get_transcript",
        side_effect=Exception("no captions"),
    )

    with pytest.raises(ValueError, match="no transcript available"):
        fetch_youtube_transcript("https://www.youtube.com/watch?v=abcDEF12345")


def test_transcribe_voice_calls_openai_with_audio_bytes(mocker):
    fixture_text = "Remind me to follow up with the Walmart contact tomorrow morning."

    fake_response = mocker.MagicMock()
    fake_response.text = fixture_text

    fake_client = mocker.MagicMock()
    fake_client.audio.transcriptions.create.return_value = fake_response

    fake_openai_class = mocker.patch("bot.enrichment.OpenAI", return_value=fake_client)

    result = transcribe_voice("test-key", b"fake-audio-bytes", file_extension=".ogg")

    fake_openai_class.assert_called_once_with(api_key="test-key")
    call_kwargs = fake_client.audio.transcriptions.create.call_args.kwargs
    assert call_kwargs["model"] == "whisper-1"
    file_arg = call_kwargs["file"]
    assert file_arg[1] == b"fake-audio-bytes"
    assert file_arg[0].endswith(".ogg")
    assert result == fixture_text
