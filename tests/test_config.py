import pytest
from bot.config import Config


def test_config_loads_all_keys(env):
    c = Config.from_env()
    assert c.bot_token == "test_bot_token"
    assert c.my_telegram_id == 12345
    assert c.webhook_secret == "test_secret"
    assert c.supabase_url == "https://test.supabase.co"
    assert c.supabase_service_key == "test_service_key"
    assert c.dropbox_app_key == "test_app_key"
    assert c.dropbox_app_secret == "test_app_secret"
    assert c.dropbox_refresh_token == "test_refresh_token"


def test_config_raises_on_missing_key(monkeypatch, env):
    monkeypatch.delenv("BOT_TOKEN")
    with pytest.raises(ValueError, match="BOT_TOKEN"):
        Config.from_env()


def test_config_raises_on_non_integer_telegram_id(monkeypatch, env):
    monkeypatch.setenv("MY_TELEGRAM_ID", "not_a_number")
    with pytest.raises(ValueError, match="MY_TELEGRAM_ID"):
        Config.from_env()
