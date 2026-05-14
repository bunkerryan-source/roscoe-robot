import httpx
import pytest
import respx

from bot.scraper import fetch_tweet, ScrapeResult, ScraperError


APIFY_URL = "https://api.apify.com/v2/acts/xquik~x-tweet-scraper/run-sync-get-dataset-items"


@respx.mock
def test_fetch_tweet_parses_single_image_response():
    respx.post(APIFY_URL).mock(return_value=httpx.Response(200, json=[{
        "url": "https://x.com/user/status/123",
        "text": "test post --sref 999 --ar 1:1",
        "author": {"username": "user", "name": "Display Name"},
        "createdAt": "2026-05-10T12:00:00.000Z",
        "media": [
            {"type": "photo", "media_url_https": "https://pbs.twimg.com/media/A.jpg"}
        ],
    }]))
    result = fetch_tweet("https://x.com/user/status/123", token="t", actor="xquik~x-tweet-scraper")
    assert isinstance(result, ScrapeResult)
    assert result.post_text == "test post --sref 999 --ar 1:1"
    assert result.author_handle == "@user"
    assert result.author_name == "Display Name"
    assert result.image_urls == ["https://pbs.twimg.com/media/A.jpg"]
    assert result.midjourney_params == {"sref": "999", "ar": "1:1"}


@respx.mock
def test_fetch_tweet_parses_multi_image_response_preserves_order():
    respx.post(APIFY_URL).mock(return_value=httpx.Response(200, json=[{
        "url": "https://x.com/user/status/123",
        "text": "three images",
        "author": {"username": "user", "name": "U"},
        "createdAt": "2026-05-10T12:00:00.000Z",
        "media": [
            {"type": "photo", "media_url_https": "https://pbs.twimg.com/media/A.jpg"},
            {"type": "photo", "media_url_https": "https://pbs.twimg.com/media/B.jpg"},
            {"type": "photo", "media_url_https": "https://pbs.twimg.com/media/C.jpg"},
        ],
    }]))
    result = fetch_tweet("https://x.com/user/status/123", token="t", actor="xquik~x-tweet-scraper")
    assert result.image_urls == [
        "https://pbs.twimg.com/media/A.jpg",
        "https://pbs.twimg.com/media/B.jpg",
        "https://pbs.twimg.com/media/C.jpg",
    ]


@respx.mock
def test_fetch_tweet_handles_empty_result_array():
    respx.post(APIFY_URL).mock(return_value=httpx.Response(200, json=[]))
    with pytest.raises(ScraperError, match="empty"):
        fetch_tweet("https://x.com/user/status/deleted", token="t", actor="xquik~x-tweet-scraper")


@respx.mock
def test_fetch_tweet_raises_on_http_error():
    respx.post(APIFY_URL).mock(return_value=httpx.Response(429, text="rate limited"))
    with pytest.raises(ScraperError, match="429"):
        fetch_tweet("https://x.com/user/status/123", token="t", actor="xquik~x-tweet-scraper")


@respx.mock
def test_fetch_tweet_extracts_video_url_from_video_info_variants():
    respx.post(APIFY_URL).mock(return_value=httpx.Response(200, json=[{
        "url": "https://x.com/user/status/123",
        "text": "video post",
        "author": {"username": "user", "name": "U"},
        "createdAt": "2026-05-10T12:00:00.000Z",
        "media": [{
            "type": "video",
            "media_url_https": "https://pbs.twimg.com/amplify_video_thumb/x/img/thumb.jpg",
            "video_info": {
                "duration_millis": 12345,
                "variants": [
                    {"content_type": "video/mp4", "bitrate": 256000,
                     "url": "https://video.twimg.com/amplify_video/x/vid/240x240/lo.mp4"},
                    {"content_type": "video/mp4", "bitrate": 832000,
                     "url": "https://video.twimg.com/amplify_video/x/vid/720x720/hi.mp4"},
                    {"content_type": "application/x-mpegURL",
                     "url": "https://video.twimg.com/amplify_video/x/pl/master.m3u8"},
                ]
            }
        }],
    }]))
    result = fetch_tweet("https://x.com/user/status/123", token="t", actor="xquik~x-tweet-scraper")
    assert result.image_urls == ["https://video.twimg.com/amplify_video/x/vid/720x720/hi.mp4"]


@respx.mock
def test_fetch_tweet_raises_on_zero_output_diagnostic():
    # xquik returns this shape when a tweet ID is missing/deleted
    respx.post(APIFY_URL).mock(return_value=httpx.Response(200, json=[{
        "id": "diag:zero-output:1",
        "status": "zero-output",
        "message": "No tweets returned for the requested IDs.",
        "actor": "x-tweet-scraper",
    }]))
    with pytest.raises(ScraperError, match="diagnostic"):
        fetch_tweet("https://x.com/user/status/deleted", token="t", actor="xquik~x-tweet-scraper")


@respx.mock
def test_fetch_tweet_raises_on_apidojo_no_results_stub():
    # apidojo/tweet-scraper returns this when fed a single URL; we should reject it
    respx.post(APIFY_URL).mock(return_value=httpx.Response(200, json=[{"noResults": True}]))
    with pytest.raises(ScraperError, match="diagnostic"):
        fetch_tweet("https://x.com/user/status/123", token="t", actor="xquik~x-tweet-scraper")


@respx.mock
def test_fetch_tweet_parses_twitter_format_createdAt():
    # xquik returns Twitter RFC822-ish dates, not ISO
    respx.post(APIFY_URL).mock(return_value=httpx.Response(200, json=[{
        "url": "https://x.com/naval/status/1",
        "text": "test",
        "author": {"username": "naval", "name": "Naval"},
        "createdAt": "Thu Jun 05 20:19:24 +0000 2025",
        "media": [],
    }]))
    result = fetch_tweet("https://x.com/naval/status/1", token="t", actor="xquik~x-tweet-scraper")
    assert result.posted_at is not None
    assert result.posted_at.year == 2025
    assert result.posted_at.month == 6
    assert result.posted_at.day == 5


@respx.mock
def test_fetch_tweet_accepts_201_status_code():
    # Apify's run-sync endpoint returns 201, not 200, on success
    respx.post(APIFY_URL).mock(return_value=httpx.Response(201, json=[{
        "url": "https://x.com/u/status/1",
        "text": "ok",
        "author": {"username": "u", "name": "U"},
        "createdAt": "Thu Jun 05 20:19:24 +0000 2025",
        "media": [],
    }]))
    result = fetch_tweet("https://x.com/u/status/1", token="t", actor="xquik~x-tweet-scraper")
    assert result.post_text == "ok"


@respx.mock
def test_fetch_tweet_raw_response_captured_for_debugging():
    body = {
        "url": "https://x.com/u/status/1", "text": "hi",
        "author": {"username": "u", "name": "U"},
        "createdAt": "Thu Jun 05 20:19:24 +0000 2025",
        "media": [], "likeCount": 5,
    }
    respx.post(APIFY_URL).mock(return_value=httpx.Response(200, json=[body]))
    result = fetch_tweet("https://x.com/u/status/1", token="t", actor="xquik~x-tweet-scraper")
    assert result.raw_response == body


@respx.mock
def test_fetch_tweet_extracts_video_url_from_direct_media_url():
    # Some Apify actors flatten video entries to just media_url_https pointing
    # at the .mp4. Accept that shape too.
    respx.post(APIFY_URL).mock(return_value=httpx.Response(200, json=[{
        "url": "https://x.com/user/status/123",
        "text": "video",
        "author": {"username": "user", "name": "U"},
        "createdAt": "2026-05-10T12:00:00.000Z",
        "media": [{"type": "video", "media_url_https": "https://video.twimg.com/x.mp4", "video_info": {"variants": []}}],
    }]))
    result = fetch_tweet("https://x.com/user/status/123", token="t", actor="xquik~x-tweet-scraper")
    assert result.image_urls == ["https://video.twimg.com/x.mp4"]


@respx.mock
def test_fetch_tweet_orders_images_before_videos_in_mixed_post():
    respx.post(APIFY_URL).mock(return_value=httpx.Response(200, json=[{
        "url": "https://x.com/user/status/123",
        "text": "mixed",
        "author": {"username": "user", "name": "U"},
        "createdAt": "2026-05-10T12:00:00.000Z",
        "media": [
            {"type": "video", "media_url_https": "https://video.twimg.com/clip.mp4"},
            {"type": "photo", "media_url_https": "https://pbs.twimg.com/media/A.jpg"},
            {"type": "photo", "media_url_https": "https://pbs.twimg.com/media/B.jpg"},
        ],
    }]))
    result = fetch_tweet("https://x.com/user/status/123", token="t", actor="xquik~x-tweet-scraper")
    # Photos first (existing fan-out invariant), videos appended at the end.
    assert result.image_urls == [
        "https://pbs.twimg.com/media/A.jpg",
        "https://pbs.twimg.com/media/B.jpg",
        "https://video.twimg.com/clip.mp4",
    ]


@respx.mock
def test_fetch_tweet_records_video_duration_from_variants():
    respx.post(APIFY_URL).mock(return_value=httpx.Response(200, json=[{
        "url": "https://x.com/user/status/123",
        "text": "tutorial",
        "author": {"username": "user", "name": "U"},
        "createdAt": "2026-05-10T12:00:00.000Z",
        "media": [{
            "type": "video",
            "media_url_https": "https://pbs.twimg.com/amplify_video_thumb/x/img/thumb.jpg",
            "video_info": {
                "duration_millis": 600000,
                "variants": [
                    {"content_type": "video/mp4", "bitrate": 832000,
                     "url": "https://video.twimg.com/amplify_video/x/vid/720x720/hi.mp4"},
                ],
            },
        }],
    }]))
    result = fetch_tweet("https://x.com/user/status/123", token="t", actor="xquik~x-tweet-scraper")
    assert result.image_urls == ["https://video.twimg.com/amplify_video/x/vid/720x720/hi.mp4"]
    assert result.video_durations == {
        "https://video.twimg.com/amplify_video/x/vid/720x720/hi.mp4": 600000,
    }


@respx.mock
def test_fetch_tweet_video_durations_empty_when_no_videos():
    respx.post(APIFY_URL).mock(return_value=httpx.Response(200, json=[{
        "url": "https://x.com/user/status/123",
        "text": "two photos",
        "author": {"username": "user", "name": "U"},
        "createdAt": "2026-05-10T12:00:00.000Z",
        "media": [
            {"type": "photo", "media_url_https": "https://pbs.twimg.com/media/A.jpg"},
            {"type": "photo", "media_url_https": "https://pbs.twimg.com/media/B.jpg"},
        ],
    }]))
    result = fetch_tweet("https://x.com/user/status/123", token="t", actor="xquik~x-tweet-scraper")
    assert result.video_durations == {}
