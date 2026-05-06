import logging
import time
from pathlib import Path
from typing import Callable, Optional

import dropbox
import httpx

logger = logging.getLogger(__name__)

DEFAULT_FALLBACK_DIR = Path("/opt/personal-os/dropbox-pending")


class DropboxRefreshClient:
    """Constructs short-lived Dropbox clients via the refresh-token flow.

    Each call to get_client() returns a fresh dropbox.Dropbox; the SDK handles
    refresh internally on first request. We build per-request rather than
    caching to avoid stale tokens and threading issues.
    """

    def __init__(self, refresh_token: str, app_key: str, app_secret: str):
        self._refresh_token = refresh_token
        self._app_key = app_key
        self._app_secret = app_secret

    def get_client(self) -> dropbox.Dropbox:
        return dropbox.Dropbox(
            oauth2_refresh_token=self._refresh_token,
            app_key=self._app_key,
            app_secret=self._app_secret,
        )


async def download_from_telegram(bot_token: str, file_id: str) -> bytes:
    """Two-step Telegram file download: getFile → file_path → GET file."""
    async with httpx.AsyncClient(timeout=30.0) as http:
        get_file = await http.get(
            f"https://api.telegram.org/bot{bot_token}/getFile",
            params={"file_id": file_id},
        )
        get_file.raise_for_status()
        file_path = get_file.json()["result"]["file_path"]

        download = await http.get(
            f"https://api.telegram.org/file/bot{bot_token}/{file_path}"
        )
        download.raise_for_status()
        return download.content


def upload_with_fallback(
    client_factory: Callable[[], dropbox.Dropbox],
    *,
    dropbox_path: str,
    content: bytes,
    max_retries: int = 3,
    retry_sleep_seconds: float = 2.0,
    fallback_dir: Optional[Path] = None,
) -> str:
    """Upload to Dropbox with exponential backoff. On total failure, write to
    a local fallback directory and return a `local-fallback://...` URI that
    a future batch can retry.
    """
    fallback_dir = fallback_dir or DEFAULT_FALLBACK_DIR
    last_err: Optional[BaseException] = None

    for attempt in range(max_retries):
        try:
            dbx = client_factory()
            dbx.files_upload(content, dropbox_path)
            return dropbox_path
        except Exception as exc:
            last_err = exc
            logger.warning(
                "dropbox upload attempt %d/%d failed for %s: %s",
                attempt + 1, max_retries, dropbox_path, exc,
            )
            if attempt < max_retries - 1 and retry_sleep_seconds > 0:
                time.sleep(retry_sleep_seconds * (2 ** attempt))

    fallback_dir.mkdir(parents=True, exist_ok=True)
    safe_name = dropbox_path.lstrip("/").replace("/", "__")
    fallback_path = fallback_dir / safe_name
    fallback_path.write_bytes(content)
    logger.error(
        "dropbox upload failed after %d attempts for %s, saved fallback to %s (last error: %s)",
        max_retries, dropbox_path, fallback_path, last_err,
    )
    return f"local-fallback://{fallback_path}"
