"""Per-item enrichment: OG metadata, YouTube transcripts, Whisper voice transcription.

These are the helpers `processor.enrich_item` calls before classification.
Each is independent and pure-Python; no FastAPI imports.

Note: `fetch_og_metadata` is intentionally synchronous so it can be called
from sync code (the processor) without spinning up an event loop. The
processor itself is sync and runs inside an async background task — so any
asyncio.run() inside the processor would deadlock. Sync httpx.Client avoids
that entirely.
"""
import re

import httpx
from openai import OpenAI
from youtube_transcript_api import YouTubeTranscriptApi


_OG_RE = {
    "title": re.compile(r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)["\']', re.I),
    "description": re.compile(r'<meta[^>]+property=["\']og:description["\'][^>]+content=["\']([^"\']+)["\']', re.I),
    "image": re.compile(r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']', re.I),
    "site_name": re.compile(r'<meta[^>]+property=["\']og:site_name["\'][^>]+content=["\']([^"\']+)["\']', re.I),
}
_TITLE_RE = re.compile(r"<title[^>]*>([^<]+)</title>", re.I)


def fetch_og_metadata(url: str) -> dict:
    """Fetch OG metadata for a URL. Returns empty strings for any field
    missing or on error."""
    out = {"title": "", "description": "", "image": "", "site_name": ""}
    try:
        with httpx.Client(timeout=8.0, follow_redirects=True) as client:
            response = client.get(url, headers={"User-Agent": "roscoe-robot/0.3"})
            if response.status_code >= 400:
                return out
            html = response.text
    except Exception:
        return out

    for key, pattern in _OG_RE.items():
        m = pattern.search(html)
        if m:
            out[key] = m.group(1)

    if not out["title"]:
        m = _TITLE_RE.search(html)
        if m:
            out["title"] = m.group(1).strip()

    return out


_YT_ID_RE = re.compile(
    r"(?:youtube\.com/watch\?(?:.*&)?v=|youtu\.be/)([A-Za-z0-9_-]{11})"
)


def fetch_youtube_transcript(url: str) -> str:
    """Extract a YouTube transcript. Raises ValueError if URL is not YouTube
    or if no transcript is available."""
    m = _YT_ID_RE.search(url)
    if not m:
        raise ValueError(f"could not extract YouTube video ID from: {url}")
    video_id = m.group(1)

    try:
        segments = YouTubeTranscriptApi.get_transcript(video_id, languages=("en",))
    except Exception as e:
        raise ValueError(f"no transcript available for {video_id}: {e}") from e

    return " ".join(s["text"] for s in segments)


def transcribe_voice(
    openai_api_key: str,
    audio_bytes: bytes,
    *,
    file_extension: str = ".ogg",
) -> str:
    """Transcribe a voice clip via OpenAI Whisper. Returns the transcript text."""
    client = OpenAI(api_key=openai_api_key)
    result = client.audio.transcriptions.create(
        model="whisper-1",
        file=(f"audio{file_extension}", audio_bytes),
    )
    return result.text
