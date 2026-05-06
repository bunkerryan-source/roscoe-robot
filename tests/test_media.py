import pytest
import respx
import httpx
from pathlib import Path
from unittest.mock import MagicMock
from bot.media import (
    DropboxRefreshClient,
    download_from_telegram,
    upload_with_fallback,
)


@pytest.mark.asyncio
async def test_download_from_telegram_two_step_flow():
    bot_token = "test_token"
    file_id = "AgACAgIAAxk..."
    file_path_returned = "photos/file_42.jpg"
    file_bytes = b"\xff\xd8\xff\xe0fake-jpeg-bytes"

    with respx.mock(base_url="https://api.telegram.org") as mock:
        mock.get(f"/bot{bot_token}/getFile", params={"file_id": file_id}).mock(
            return_value=httpx.Response(
                200, json={"ok": True, "result": {"file_path": file_path_returned}}
            )
        )
        mock.get(f"/file/bot{bot_token}/{file_path_returned}").mock(
            return_value=httpx.Response(200, content=file_bytes)
        )

        result = await download_from_telegram(bot_token, file_id)

    assert result == file_bytes


@pytest.mark.asyncio
async def test_download_from_telegram_raises_on_getfile_error():
    bot_token = "test_token"
    file_id = "expired_id"

    with respx.mock(base_url="https://api.telegram.org") as mock:
        mock.get(f"/bot{bot_token}/getFile", params={"file_id": file_id}).mock(
            return_value=httpx.Response(400, json={"ok": False, "description": "file expired"})
        )
        with pytest.raises(httpx.HTTPStatusError):
            await download_from_telegram(bot_token, file_id)


def test_dropbox_refresh_client_constructs_dropbox_with_refresh_token(mocker):
    mock_dropbox = mocker.patch("bot.media.dropbox.Dropbox")
    factory = DropboxRefreshClient("refresh_xyz", "key", "secret")
    factory.get_client()
    mock_dropbox.assert_called_once_with(
        oauth2_refresh_token="refresh_xyz",
        app_key="key",
        app_secret="secret",
    )


def test_upload_with_fallback_happy_path(mocker):
    fake_dbx = MagicMock()
    factory = MagicMock(return_value=fake_dbx)

    result = upload_with_fallback(
        factory,
        dropbox_path="/personal-os-inbox/2026-05-05/abc.jpg",
        content=b"jpeg-bytes",
        max_retries=3,
        retry_sleep_seconds=0,
    )

    assert result == "/personal-os-inbox/2026-05-05/abc.jpg"
    fake_dbx.files_upload.assert_called_once_with(b"jpeg-bytes", "/personal-os-inbox/2026-05-05/abc.jpg")


def test_upload_with_fallback_retries_then_succeeds(mocker):
    fake_dbx = MagicMock()
    fake_dbx.files_upload.side_effect = [Exception("flaky"), Exception("flaky again"), None]
    factory = MagicMock(return_value=fake_dbx)

    result = upload_with_fallback(
        factory,
        dropbox_path="/x.jpg",
        content=b"x",
        max_retries=3,
        retry_sleep_seconds=0,
    )

    assert result == "/x.jpg"
    assert fake_dbx.files_upload.call_count == 3


def test_upload_with_fallback_falls_back_to_local_after_max_retries(tmp_path, mocker):
    fake_dbx = MagicMock()
    fake_dbx.files_upload.side_effect = Exception("dropbox is down")
    factory = MagicMock(return_value=fake_dbx)

    fallback_dir = tmp_path / "dropbox-pending"

    result = upload_with_fallback(
        factory,
        dropbox_path="/personal-os-inbox/2026-05-05/abc.jpg",
        content=b"jpeg-bytes",
        max_retries=3,
        retry_sleep_seconds=0,
        fallback_dir=fallback_dir,
    )

    assert result.startswith("local-fallback://")
    fallback_files = list(fallback_dir.iterdir())
    assert len(fallback_files) == 1
    assert fallback_files[0].read_bytes() == b"jpeg-bytes"
    # Path encoding: slashes → __ so the filename is flat
    assert "personal-os-inbox__2026-05-05__abc.jpg" in fallback_files[0].name
