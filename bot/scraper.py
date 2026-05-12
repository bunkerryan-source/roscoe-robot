"""Apify-backed X/Twitter scraper.

Returns normalized ScrapeResult with post text, author, image URLs, and
extracted Midjourney params. Caller is responsible for inserting/looking up
source_posts rows; this module is pure I/O + parsing.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import httpx

from bot.midjourney import extract_params


class ScraperError(RuntimeError):
    """Raised when Apify returns no usable data."""


@dataclass
class ScrapeResult:
    source_url: str
    post_text: str
    author_handle: str | None
    author_name: str | None
    posted_at: datetime | None
    image_urls: list[str] = field(default_factory=list)
    midjourney_params: dict[str, str] = field(default_factory=dict)
    raw_response: dict[str, Any] | None = None


def fetch_tweet(url: str, *, token: str, actor: str, timeout: float = 60.0) -> ScrapeResult:
    """Synchronously scrape a single X URL via Apify and return ScrapeResult.

    Raises ScraperError on HTTP error, empty result, or diagnostic-only response.
    """
    endpoint = f"https://api.apify.com/v2/acts/{actor}/run-sync-get-dataset-items"
    payload = {"startUrls": [{"url": url}], "maxItems": 1}
    params = {"token": token}
    with httpx.Client(timeout=timeout) as client:
        resp = client.post(endpoint, params=params, json=payload)
    if resp.status_code not in (200, 201):
        raise ScraperError(f"Apify HTTP {resp.status_code}: {resp.text[:200]}")
    data = resp.json()
    if not isinstance(data, list) or not data:
        raise ScraperError(f"Apify returned empty dataset for {url}")
    tweet = data[0]
    if _is_diagnostic(tweet):
        raise ScraperError(
            f"Apify diagnostic response for {url}: "
            f"{tweet.get('status') or tweet.get('message') or tweet}"
        )
    return _normalize(url, tweet)


def _is_diagnostic(tweet: dict[str, Any]) -> bool:
    """Return True if the row is an actor diagnostic stub rather than a real tweet."""
    if not tweet.get("text") and not tweet.get("author"):
        return True
    if tweet.get("status") in ("zero-output", "not-found", "error"):
        return True
    if tweet.get("demo") is True or tweet.get("noResults") is True:
        return True
    return False


_VIDEO_EXTENSIONS = (".mp4", ".mov", ".webm")


def _extract_video_url(media_entry: dict[str, Any]) -> str | None:
    """Return the best video URL for a media entry, or None.

    Preference order:
      1. Highest-bitrate `video/mp4` variant in `video_info.variants`.
      2. Direct `media_url_https` if it ends in a known video extension.
    """
    variants = (media_entry.get("video_info") or {}).get("variants") or []
    mp4_variants = [
        v for v in variants
        if v.get("content_type") == "video/mp4" and v.get("url")
    ]
    if mp4_variants:
        best = max(mp4_variants, key=lambda v: v.get("bitrate") or 0)
        return best["url"]
    direct = media_entry.get("media_url_https")
    if direct and any(direct.lower().endswith(ext) for ext in _VIDEO_EXTENSIONS):
        return direct
    return None


def _normalize(source_url: str, tweet: dict[str, Any]) -> ScrapeResult:
    text = (tweet.get("text") or "").strip()
    author = tweet.get("author") or {}
    handle_raw = author.get("username")
    author_handle = f"@{handle_raw}" if handle_raw else None
    posted_at = _parse_twitter_date(tweet.get("createdAt"))
    media = tweet.get("media") or []
    image_urls: list[str] = [
        m.get("media_url_https") for m in media
        if m.get("type") == "photo" and m.get("media_url_https")
    ]
    # Videos and animated_gif entries: append video URL after photos so the
    # existing multi-image fan-out invariant ("image_urls[1:] are all images")
    # holds for pure-image tweets and mixed tweets order photos-first.
    for m in media:
        if m.get("type") in ("video", "animated_gif"):
            video_url = _extract_video_url(m)
            if video_url:
                image_urls.append(video_url)
    return ScrapeResult(
        source_url=source_url,
        post_text=text,
        author_handle=author_handle,
        author_name=author.get("name"),
        posted_at=posted_at,
        image_urls=image_urls,
        midjourney_params=extract_params(text),
        raw_response=tweet,
    )


def _parse_twitter_date(value: str | None) -> datetime | None:
    """Parse Twitter's RFC 822-ish createdAt (e.g. 'Thu Jun 05 20:19:24 +0000 2025').

    Also tolerates ISO 8601 for forward compatibility.
    """
    if not value:
        return None
    try:
        from email.utils import parsedate_to_datetime
        return parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError):
        pass
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
