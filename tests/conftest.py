import json
import os
from pathlib import Path

import pytest

# Required env vars — every test sets them via the fixture below
ENV_KEYS = [
    "BOT_TOKEN",
    "MY_TELEGRAM_ID",
    "WEBHOOK_SECRET",
    "SUPABASE_URL",
    "SUPABASE_SERVICE_KEY",
    "DROPBOX_APP_KEY",
    "DROPBOX_APP_SECRET",
    "DROPBOX_REFRESH_TOKEN",
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "TODOIST_API_TOKEN",
    "TODOIST_PARENT_ACUTE",
    "TODOIST_PARENT_ABP",
    "TODOIST_PARENT_LAKE_ARROWHEAD",
    "TODOIST_PARENT_CHURCH",
    "TODOIST_PARENT_CLAUDE_BUILD",
    "TODOIST_PARENT_DESIGN",
    "TODOIST_PARENT_PERSONAL",
    "OBSIDIAN_VAULT_DROPBOX_PATH",
]


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("BOT_TOKEN", "test_bot_token")
    monkeypatch.setenv("MY_TELEGRAM_ID", "12345")
    monkeypatch.setenv("WEBHOOK_SECRET", "test_secret")
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "test_service_key")
    monkeypatch.setenv("DROPBOX_APP_KEY", "test_app_key")
    monkeypatch.setenv("DROPBOX_APP_SECRET", "test_app_secret")
    monkeypatch.setenv("DROPBOX_REFRESH_TOKEN", "test_refresh_token")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test_anthropic_key")
    monkeypatch.setenv("OPENAI_API_KEY", "test_openai_key")
    monkeypatch.setenv("TODOIST_API_TOKEN", "test_todoist_token")
    monkeypatch.setenv("TODOIST_PARENT_ACUTE", "1000001")
    monkeypatch.setenv("TODOIST_PARENT_ABP", "1000002")
    monkeypatch.setenv("TODOIST_PARENT_LAKE_ARROWHEAD", "1000003")
    monkeypatch.setenv("TODOIST_PARENT_CHURCH", "1000004")
    monkeypatch.setenv("TODOIST_PARENT_CLAUDE_BUILD", "1000005")
    monkeypatch.setenv("TODOIST_PARENT_DESIGN", "1000006")
    monkeypatch.setenv("TODOIST_PARENT_PERSONAL", "1000007")
    monkeypatch.setenv("OBSIDIAN_VAULT_DROPBOX_PATH", "/personal-os")


FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def load_fixture():
    def _load(name: str) -> dict:
        path = FIXTURES_DIR / f"{name}.json"
        return json.loads(path.read_text())
    return _load
