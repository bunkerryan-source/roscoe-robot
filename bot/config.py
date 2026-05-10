import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    # Existing — Session 2
    bot_token: str
    my_telegram_id: int
    webhook_secret: str
    supabase_url: str
    supabase_service_key: str
    dropbox_app_key: str
    dropbox_app_secret: str
    dropbox_refresh_token: str

    # New — Session 3
    anthropic_api_key: str
    openai_api_key: str
    todoist_api_token: str
    todoist_parents: dict[str, str]
    obsidian_vault_dropbox_path: str

    @classmethod
    def from_env(cls) -> "Config":
        required = [
            "BOT_TOKEN", "MY_TELEGRAM_ID", "WEBHOOK_SECRET",
            "SUPABASE_URL", "SUPABASE_SERVICE_KEY",
            "DROPBOX_APP_KEY", "DROPBOX_APP_SECRET", "DROPBOX_REFRESH_TOKEN",
            "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "TODOIST_API_TOKEN",
            "TODOIST_PARENT_ACUTE", "TODOIST_PARENT_ABP",
            "TODOIST_PARENT_LAKE_ARROWHEAD", "TODOIST_PARENT_CHURCH",
            "TODOIST_PARENT_CLAUDE_BUILD", "TODOIST_PARENT_DESIGN",
            "TODOIST_PARENT_PERSONAL",
            "OBSIDIAN_VAULT_DROPBOX_PATH",
        ]
        missing = [k for k in required if not os.environ.get(k, "").strip()]
        if missing:
            raise ValueError(f"missing required env vars: {missing}")

        try:
            my_id = int(os.environ["MY_TELEGRAM_ID"])
        except ValueError as e:
            raise ValueError("MY_TELEGRAM_ID must be an integer") from e

        return cls(
            bot_token=os.environ["BOT_TOKEN"],
            my_telegram_id=my_id,
            webhook_secret=os.environ["WEBHOOK_SECRET"],
            supabase_url=os.environ["SUPABASE_URL"],
            supabase_service_key=os.environ["SUPABASE_SERVICE_KEY"],
            dropbox_app_key=os.environ["DROPBOX_APP_KEY"],
            dropbox_app_secret=os.environ["DROPBOX_APP_SECRET"],
            dropbox_refresh_token=os.environ["DROPBOX_REFRESH_TOKEN"],
            anthropic_api_key=os.environ["ANTHROPIC_API_KEY"],
            openai_api_key=os.environ["OPENAI_API_KEY"],
            todoist_api_token=os.environ["TODOIST_API_TOKEN"],
            todoist_parents={
                "acute": os.environ["TODOIST_PARENT_ACUTE"],
                "abp": os.environ["TODOIST_PARENT_ABP"],
                "lake-arrowhead": os.environ["TODOIST_PARENT_LAKE_ARROWHEAD"],
                "church": os.environ["TODOIST_PARENT_CHURCH"],
                "claude-build": os.environ["TODOIST_PARENT_CLAUDE_BUILD"],
                "design": os.environ["TODOIST_PARENT_DESIGN"],
                "personal": os.environ["TODOIST_PARENT_PERSONAL"],
            },
            obsidian_vault_dropbox_path=os.environ["OBSIDIAN_VAULT_DROPBOX_PATH"],
        )
