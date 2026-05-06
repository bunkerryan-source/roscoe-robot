import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    bot_token: str
    my_telegram_id: int
    webhook_secret: str
    supabase_url: str
    supabase_service_key: str
    dropbox_app_key: str
    dropbox_app_secret: str
    dropbox_refresh_token: str

    @classmethod
    def from_env(cls) -> "Config":
        def required(key: str) -> str:
            value = os.environ.get(key, "").strip()
            if not value:
                raise ValueError(f"missing or empty env var: {key}")
            return value

        try:
            telegram_id = int(required("MY_TELEGRAM_ID"))
        except (TypeError, ValueError) as exc:
            raise ValueError("MY_TELEGRAM_ID must be an integer") from exc

        return cls(
            bot_token=required("BOT_TOKEN"),
            my_telegram_id=telegram_id,
            webhook_secret=required("WEBHOOK_SECRET"),
            supabase_url=required("SUPABASE_URL"),
            supabase_service_key=required("SUPABASE_SERVICE_KEY"),
            dropbox_app_key=required("DROPBOX_APP_KEY"),
            dropbox_app_secret=required("DROPBOX_APP_SECRET"),
            dropbox_refresh_token=required("DROPBOX_REFRESH_TOKEN"),
        )
