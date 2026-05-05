# Session 2 — Capture Pipe Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the existing log-file capture with a durable pipe to Supabase + Dropbox. Every Telegram message lands as a row in the `items` table; every photo/video/voice file lands in Dropbox. No classification, no Claude calls, no processing. Bot stays sub-2-second responsive.

**Architecture:** FastAPI webhook on the existing droplet receives Telegram updates, verifies sender ID, parses the message, inserts a `pending` row into Supabase, and offloads media download/upload to a FastAPI background task. Dropbox uses OAuth refresh-token flow; failed Dropbox uploads spool to a local fallback directory and are retried on next run. Acks Telegram with 👍 in the background after the webhook returns 200.

**Tech Stack:** Python 3.11+, FastAPI, uvicorn, httpx (raw Telegram REST), supabase-py, dropbox SDK (refresh-token flow), python-dotenv. Tests: pytest, pytest-asyncio, respx (httpx mocking), pytest-mock.

---

## Spec reference

This plan implements the **Capture (bot intake)**, **Data model**, and **Failure modes** sections of [spec.md](../../../spec.md), constrained to what Session 2 delivers per the **Build sequence** section. No processing, no classification, no Claude calls in this session.

## Prerequisites (one-time setup the operator does before starting Task 1)

These are operator actions, not code tasks. Each yields a value that goes into `.env`.

- [ ] **P1: Create Supabase project for Roscoe.** Sign in to https://supabase.com, create new project named `roscoe-robot` in the `pahusgzghshomlzjfgwf` org, region `us-west-1` (closest to droplet's likely region; us-east-1 is fine too). Save the database password to a password manager. Capture:
  - `SUPABASE_URL` (Settings → API → Project URL — looks like `https://<project-ref>.supabase.co`)
  - `SUPABASE_SERVICE_KEY` (Settings → API → `service_role` secret — NOT the anon key)

- [ ] **P2: Create Dropbox app for Roscoe.** Go to https://www.dropbox.com/developers/apps → Create app → "Scoped access" → "App folder" (cleaner than full Dropbox). Name it `roscoe-robot`. Once created:
  - In **Settings**: capture `App key` and `App secret`.
  - In **Permissions**: enable `files.content.write`, `files.content.read`, `files.metadata.write`, `files.metadata.read`. Click Submit.
  - Note: this gives the bot access only to a `Apps/roscoe-robot/` folder under your Dropbox, not your whole Dropbox.

- [ ] **P3: Confirm Python 3.11+ on local dev machine.** Run `python --version` (or `py --version` on Windows). If <3.11, install via https://www.python.org/downloads/.

- [ ] **P4: Confirm SSH access to droplet still works.** Run `ssh root@64.23.170.115 "systemctl status personal-os --no-pager | head -5"`. Expected: shows the v1 service active.

- [ ] **P5: Capture existing droplet env vars.** SSH in and `cat /opt/personal-os/.env`. Save the existing values for `BOT_TOKEN`, `MY_TELEGRAM_ID`, `WEBHOOK_SECRET` — they will go into the new local `.env` and the new droplet `.env`.

After P1-P5, you should have these eight values written down somewhere safe:

```
BOT_TOKEN=...                    (from P5)
MY_TELEGRAM_ID=...               (from P5, integer)
WEBHOOK_SECRET=...               (from P5)
SUPABASE_URL=...                 (from P1)
SUPABASE_SERVICE_KEY=...         (from P1)
DROPBOX_APP_KEY=...              (from P2)
DROPBOX_APP_SECRET=...           (from P2)
DROPBOX_REFRESH_TOKEN=           (filled in during Task 11)
```

## File structure

What this plan creates. Map of what lives where:

```
roscoe-robot/
├── .env.example              ← template for the 8 vars (no real secrets)
├── .gitignore                ← Python + .env
├── pyproject.toml            ← pytest config, dev deps
├── requirements.txt          ← runtime deps (pinned)
├── bot/
│   ├── __init__.py
│   ├── config.py             ← env → typed Config dataclass
│   ├── db.py                 ← Supabase client + insert_item / update_media_path
│   ├── intake.py             ← Telegram update → IntakeResult dict
│   ├── media.py              ← Telegram download + Dropbox upload + local fallback
│   └── main.py               ← FastAPI app + /webhook route + background tasks
├── tests/
│   ├── __init__.py
│   ├── conftest.py           ← shared fixtures (env stub, supabase mock, dropbox mock)
│   ├── fixtures/
│   │   ├── update_text.json
│   │   ├── update_photo_with_caption.json
│   │   ├── update_photo_no_caption.json
│   │   ├── update_video.json
│   │   ├── update_voice.json
│   │   ├── update_link.json
│   │   ├── update_forward.json
│   │   └── update_unauthorized_sender.json
│   ├── test_config.py
│   ├── test_db.py
│   ├── test_intake.py
│   ├── test_media.py
│   └── test_webhook.py
├── migrations/
│   └── 001_initial_schema.sql ← items, corrections, runs tables + indexes
├── scripts/
│   └── setup_dropbox_oauth.py ← one-time: get refresh token from app key/secret
└── deploy/
    ├── deploy.sh              ← rsync local repo → droplet:/opt/personal-os-v2/
    └── personal-os-v2.service ← systemd unit pointing at v2
```

The `bot/` module has a hard rule: **only `main.py` knows FastAPI exists**. `config`, `db`, `intake`, `media` are pure-Python and unit-testable without spinning up an HTTP server. This keeps tests fast and the seams obvious for Session 3+ where we add a separate `processor` module that imports `db` and `media` but not `main`.

---

## Task 1: Repo skeleton + dev tooling

**Files:**
- Create: `.gitignore`, `.env.example`, `requirements.txt`, `pyproject.toml`, `bot/__init__.py`, `tests/__init__.py`, `tests/conftest.py`

- [ ] **Step 1: Write `.gitignore`**

```
# Python
__pycache__/
*.py[cod]
*$py.class
*.so
.Python
.venv/
venv/
env/
.pytest_cache/
.mypy_cache/
.ruff_cache/
*.egg-info/

# Env / secrets
.env
.env.local
.env.*.local

# OS
.DS_Store
Thumbs.db

# Editor
.vscode/
.idea/
*.swp
```

- [ ] **Step 2: Write `.env.example`** (template, no real secrets — committed to repo)

```
# Telegram bot — from BotFather, locked to this user
BOT_TOKEN=
MY_TELEGRAM_ID=
WEBHOOK_SECRET=

# Supabase — service role key (server-side only, never client-side)
SUPABASE_URL=https://your-project-ref.supabase.co
SUPABASE_SERVICE_KEY=

# Dropbox — refresh-token OAuth flow
DROPBOX_APP_KEY=
DROPBOX_APP_SECRET=
DROPBOX_REFRESH_TOKEN=
```

- [ ] **Step 3: Write `requirements.txt`** (pinned, runtime only)

```
fastapi==0.115.0
uvicorn[standard]==0.30.6
httpx==0.27.2
python-dotenv==1.0.1
supabase==2.7.4
dropbox==12.0.2
```

- [ ] **Step 4: Write `pyproject.toml`** (dev deps + pytest config)

```toml
[project]
name = "roscoe-robot"
version = "0.2.0"
description = "Personal OS capture bot"
requires-python = ">=3.11"

[project.optional-dependencies]
dev = [
    "pytest==8.3.3",
    "pytest-asyncio==0.24.0",
    "pytest-mock==3.14.0",
    "respx==0.21.1",
]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
```

- [ ] **Step 5: Create empty package files**

```bash
mkdir -p bot tests tests/fixtures migrations scripts deploy
touch bot/__init__.py tests/__init__.py
```

- [ ] **Step 6: Write `tests/conftest.py`**

```python
import os
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
```

- [ ] **Step 7: Set up local virtualenv + install deps**

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows PowerShell
pip install -r requirements.txt
pip install -e ".[dev]"
```

- [ ] **Step 8: Verify pytest runs (zero tests, exits 0)**

Run: `pytest`
Expected output: `no tests ran in 0.0Xs`, exit code 0.

- [ ] **Step 9: Commit**

```bash
git add .gitignore .env.example requirements.txt pyproject.toml bot/ tests/ migrations/ scripts/ deploy/
git commit -m "chore: scaffold repo, deps, pytest config"
```

---

## Task 2: Supabase schema migration

**Files:**
- Create: `migrations/001_initial_schema.sql`

- [ ] **Step 1: Write the migration**

`migrations/001_initial_schema.sql`:

```sql
-- Personal OS — initial schema
-- See spec.md "Data model (Supabase)" section.

create extension if not exists "uuid-ossp";

-- Items: one row per Telegram capture
create table public.items (
    id                      uuid primary key default uuid_generate_v4(),
    created_at              timestamptz not null default now(),
    processed_at            timestamptz,
    status                  text not null default 'pending'
                            check (status in ('pending','processed','needs_review','failed')),
    source                  text not null default 'telegram',
    source_message_id       text not null,
    raw_text                text,
    media_type              text not null
                            check (media_type in ('text','image','video','voice','link','forward','document')),
    media_dropbox_path      text,
    media_telegram_file_id  text,
    project                 text,
    subdomain               text,
    type                    text,
    tags                    text[],
    visual_subtype          text,
    summary                 text,
    obsidian_path           text,
    todoist_task_id         text,
    classified_by           text,
    confidence              real,
    api_cost_cents          integer,
    error                   text
);

create index items_status_idx        on public.items (status);
create index items_created_at_idx    on public.items (created_at desc);
create index items_project_idx       on public.items (project);
create index items_type_idx          on public.items (type);
create index items_tags_gin_idx      on public.items using gin (tags);
-- Full-text search index on summary + raw_text + caption-equivalent
create index items_fts_idx           on public.items using gin (
    to_tsvector('english', coalesce(summary,'') || ' ' || coalesce(raw_text,''))
);

-- Corrections: feedback loop, populated when user refiles in B+ triage
create table public.corrections (
    id                  uuid primary key default uuid_generate_v4(),
    item_id             uuid not null references public.items(id) on delete cascade,
    original_class      jsonb not null,
    corrected_class     jsonb not null,
    created_at          timestamptz not null default now()
);

create index corrections_created_at_idx on public.corrections (created_at desc);

-- Runs: per-batch observability
create table public.runs (
    id                      uuid primary key default uuid_generate_v4(),
    started_at              timestamptz not null default now(),
    completed_at            timestamptz,
    trigger                 text not null
                            check (trigger in ('scheduled-630','scheduled-1200','scheduled-2100',
                                               'on-demand','weekly-digest','monthly-rules')),
    items_processed         integer not null default 0,
    items_needs_review      integer not null default 0,
    items_failed            integer not null default 0,
    total_cost_cents        integer not null default 0,
    summary_message_id      text
);

create index runs_started_at_idx on public.runs (started_at desc);
```

- [ ] **Step 2: List existing tables in the new Supabase project (sanity check that it's empty)**

Use the Supabase MCP tool. Replace `<project-id>` with the project ID from prerequisite P1 (visible in Settings → General → Reference ID, or via `mcp__claude_ai_Supabase__list_projects`).

Tool call:
```
mcp__claude_ai_Supabase__list_tables(project_id="<project-id>", schemas=["public"], verbose=false)
```

Expected: empty list (no tables yet).

- [ ] **Step 3: Apply the migration via MCP**

Tool call:
```
mcp__claude_ai_Supabase__apply_migration(
    project_id="<project-id>",
    name="001_initial_schema",
    query=<contents of migrations/001_initial_schema.sql>
)
```

Expected: success message.

- [ ] **Step 4: Verify tables exist**

Tool call:
```
mcp__claude_ai_Supabase__list_tables(project_id="<project-id>", schemas=["public"], verbose=true)
```

Expected: three tables — `items`, `corrections`, `runs` — with the columns from the migration.

- [ ] **Step 5: Commit**

```bash
git add migrations/001_initial_schema.sql
git commit -m "feat(db): initial schema for items, corrections, runs"
```

---

## Task 3: Config loading (TDD)

**Files:**
- Create: `bot/config.py`, `tests/test_config.py`

- [ ] **Step 1: Write the failing test**

`tests/test_config.py`:

```python
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
```

- [ ] **Step 2: Run test, confirm it fails**

Run: `pytest tests/test_config.py -v`
Expected: ImportError or ModuleNotFoundError on `bot.config`.

- [ ] **Step 3: Write the implementation**

`bot/config.py`:

```python
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
```

- [ ] **Step 4: Run tests, confirm pass**

Run: `pytest tests/test_config.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add bot/config.py tests/test_config.py
git commit -m "feat(config): typed env loading with validation"
```

---

## Task 4: Supabase client wrapper (TDD)

**Files:**
- Create: `bot/db.py`, `tests/test_db.py`

The Supabase Python client returns a chained builder; tests mock the chain.

- [ ] **Step 1: Write the failing test**

`tests/test_db.py`:

```python
from unittest.mock import MagicMock
from bot.db import insert_item, update_media_path


def _mock_supabase_with_inserted_row(row: dict):
    """Builds a mock that mimics: client.table("items").insert(...).execute()."""
    client = MagicMock()
    execute_result = MagicMock()
    execute_result.data = [row]
    client.table.return_value.insert.return_value.execute.return_value = execute_result
    return client


def test_insert_item_returns_row_with_id():
    expected = {
        "id": "abc-123",
        "status": "pending",
        "source": "telegram",
        "source_message_id": "42",
        "raw_text": "hello",
        "media_type": "text",
        "media_telegram_file_id": None,
    }
    client = _mock_supabase_with_inserted_row(expected)

    row = insert_item(
        client,
        source_message_id="42",
        raw_text="hello",
        media_type="text",
        media_telegram_file_id=None,
    )

    assert row["id"] == "abc-123"
    client.table.assert_called_once_with("items")
    insert_call = client.table.return_value.insert.call_args[0][0]
    assert insert_call["status"] == "pending"
    assert insert_call["source_message_id"] == "42"
    assert insert_call["media_type"] == "text"


def test_insert_item_for_image_includes_file_id():
    client = _mock_supabase_with_inserted_row({"id": "x"})
    insert_item(
        client,
        source_message_id="99",
        raw_text="caption",
        media_type="image",
        media_telegram_file_id="AgAC...",
    )
    payload = client.table.return_value.insert.call_args[0][0]
    assert payload["media_telegram_file_id"] == "AgAC..."
    assert payload["raw_text"] == "caption"


def test_update_media_path_calls_supabase():
    client = MagicMock()
    update_media_path(client, "abc-123", "/personal-os-inbox/2026-05-05/abc-123.jpg")
    client.table.assert_called_once_with("items")
    client.table.return_value.update.assert_called_once_with(
        {"media_dropbox_path": "/personal-os-inbox/2026-05-05/abc-123.jpg"}
    )
    client.table.return_value.update.return_value.eq.assert_called_once_with("id", "abc-123")
```

- [ ] **Step 2: Run test, confirm fails**

Run: `pytest tests/test_db.py -v`
Expected: ModuleNotFoundError on `bot.db`.

- [ ] **Step 3: Write the implementation**

`bot/db.py`:

```python
from typing import Optional
from supabase import create_client, Client


def get_client(url: str, service_key: str) -> Client:
    return create_client(url, service_key)


def insert_item(
    client: Client,
    *,
    source_message_id: str,
    raw_text: Optional[str],
    media_type: str,
    media_telegram_file_id: Optional[str],
) -> dict:
    payload = {
        "status": "pending",
        "source": "telegram",
        "source_message_id": source_message_id,
        "raw_text": raw_text,
        "media_type": media_type,
        "media_telegram_file_id": media_telegram_file_id,
    }
    result = client.table("items").insert(payload).execute()
    return result.data[0]


def update_media_path(client: Client, item_id: str, dropbox_path: str) -> None:
    client.table("items").update({"media_dropbox_path": dropbox_path}).eq("id", item_id).execute()
```

- [ ] **Step 4: Run tests, confirm pass**

Run: `pytest tests/test_db.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add bot/db.py tests/test_db.py
git commit -m "feat(db): supabase wrappers for insert_item, update_media_path"
```

---

## Task 5: Telegram intake parser (TDD)

The intake parser is pure: it takes a Telegram update dict and returns an `IntakeResult` dict. No I/O, no external deps. We TDD across all media types.

**Files:**
- Create: `bot/intake.py`, `tests/test_intake.py`, `tests/fixtures/update_*.json`

- [ ] **Step 1: Write fixtures for each Telegram update type**

`tests/fixtures/update_text.json`:
```json
{
    "update_id": 1001,
    "message": {
        "message_id": 100,
        "from": {"id": 12345, "is_bot": false, "first_name": "Ryan"},
        "chat": {"id": 12345, "type": "private"},
        "date": 1746472200,
        "text": "Remember to call Walmart contact"
    }
}
```

`tests/fixtures/update_link.json`:
```json
{
    "update_id": 1002,
    "message": {
        "message_id": 101,
        "from": {"id": 12345, "is_bot": false, "first_name": "Ryan"},
        "chat": {"id": 12345, "type": "private"},
        "date": 1746472210,
        "text": "https://x.com/somebody/status/123",
        "entities": [{"type": "url", "offset": 0, "length": 35}]
    }
}
```

`tests/fixtures/update_photo_with_caption.json`:
```json
{
    "update_id": 1003,
    "message": {
        "message_id": 102,
        "from": {"id": 12345, "is_bot": false, "first_name": "Ryan"},
        "chat": {"id": 12345, "type": "private"},
        "date": 1746472220,
        "caption": "for Lake Arrowhead bathroom",
        "photo": [
            {"file_id": "small_file_id", "file_unique_id": "u1", "width": 90, "height": 90, "file_size": 1000},
            {"file_id": "medium_file_id", "file_unique_id": "u2", "width": 320, "height": 320, "file_size": 5000},
            {"file_id": "large_file_id", "file_unique_id": "u3", "width": 1280, "height": 1280, "file_size": 50000}
        ]
    }
}
```

`tests/fixtures/update_photo_no_caption.json`:
```json
{
    "update_id": 1004,
    "message": {
        "message_id": 103,
        "from": {"id": 12345, "is_bot": false, "first_name": "Ryan"},
        "chat": {"id": 12345, "type": "private"},
        "date": 1746472230,
        "photo": [
            {"file_id": "small_file_id", "file_unique_id": "u1", "width": 90, "height": 90, "file_size": 1000},
            {"file_id": "large_file_id", "file_unique_id": "u3", "width": 1280, "height": 1280, "file_size": 50000}
        ]
    }
}
```

`tests/fixtures/update_video.json`:
```json
{
    "update_id": 1005,
    "message": {
        "message_id": 104,
        "from": {"id": 12345, "is_bot": false, "first_name": "Ryan"},
        "chat": {"id": 12345, "type": "private"},
        "date": 1746472240,
        "caption": "design tutorial - watch later",
        "video": {
            "file_id": "video_file_id",
            "file_unique_id": "v1",
            "width": 1920,
            "height": 1080,
            "duration": 120,
            "file_size": 5000000,
            "mime_type": "video/mp4"
        }
    }
}
```

`tests/fixtures/update_voice.json`:
```json
{
    "update_id": 1006,
    "message": {
        "message_id": 105,
        "from": {"id": 12345, "is_bot": false, "first_name": "Ryan"},
        "chat": {"id": 12345, "type": "private"},
        "date": 1746472250,
        "voice": {
            "file_id": "voice_file_id",
            "file_unique_id": "vc1",
            "duration": 8,
            "mime_type": "audio/ogg",
            "file_size": 30000
        }
    }
}
```

`tests/fixtures/update_forward.json`:
```json
{
    "update_id": 1007,
    "message": {
        "message_id": 106,
        "from": {"id": 12345, "is_bot": false, "first_name": "Ryan"},
        "chat": {"id": 12345, "type": "private"},
        "date": 1746472260,
        "forward_origin": {
            "type": "user",
            "date": 1746470000,
            "sender_user": {"id": 999, "is_bot": false, "first_name": "Friend"}
        },
        "text": "Forwarded important article: https://example.com/article"
    }
}
```

`tests/fixtures/update_unauthorized_sender.json`:
```json
{
    "update_id": 1008,
    "message": {
        "message_id": 107,
        "from": {"id": 99999, "is_bot": false, "first_name": "Stranger"},
        "chat": {"id": 99999, "type": "private"},
        "date": 1746472270,
        "text": "spam attempt"
    }
}
```

- [ ] **Step 2: Add a fixture-loading helper to `tests/conftest.py`**

Append to `tests/conftest.py`:

```python
import json
from pathlib import Path

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def load_fixture():
    def _load(name: str) -> dict:
        path = FIXTURES_DIR / f"{name}.json"
        return json.loads(path.read_text())
    return _load
```

- [ ] **Step 3: Write the failing tests**

`tests/test_intake.py`:

```python
import pytest
from bot.intake import parse_update


def test_text(load_fixture):
    result = parse_update(load_fixture("update_text"))
    assert result["media_type"] == "text"
    assert result["raw_text"] == "Remember to call Walmart contact"
    assert result["media_telegram_file_id"] is None


def test_link_detected_from_url_only_text(load_fixture):
    result = parse_update(load_fixture("update_link"))
    assert result["media_type"] == "link"
    assert result["raw_text"] == "https://x.com/somebody/status/123"
    assert result["media_telegram_file_id"] is None


def test_photo_with_caption_picks_largest_size(load_fixture):
    result = parse_update(load_fixture("update_photo_with_caption"))
    assert result["media_type"] == "image"
    assert result["raw_text"] == "for Lake Arrowhead bathroom"
    assert result["media_telegram_file_id"] == "large_file_id"


def test_photo_without_caption(load_fixture):
    result = parse_update(load_fixture("update_photo_no_caption"))
    assert result["media_type"] == "image"
    assert result["raw_text"] is None
    assert result["media_telegram_file_id"] == "large_file_id"


def test_video(load_fixture):
    result = parse_update(load_fixture("update_video"))
    assert result["media_type"] == "video"
    assert result["raw_text"] == "design tutorial - watch later"
    assert result["media_telegram_file_id"] == "video_file_id"


def test_voice(load_fixture):
    result = parse_update(load_fixture("update_voice"))
    assert result["media_type"] == "voice"
    assert result["raw_text"] is None
    assert result["media_telegram_file_id"] == "voice_file_id"


def test_forward_preserves_text(load_fixture):
    result = parse_update(load_fixture("update_forward"))
    assert result["media_type"] == "forward"
    assert "Forwarded important article" in result["raw_text"]
    assert result["media_telegram_file_id"] is None


def test_empty_update_raises():
    with pytest.raises(ValueError, match="no message"):
        parse_update({"update_id": 1})


def test_unsupported_message_content_raises():
    update = {
        "update_id": 1,
        "message": {
            "message_id": 1,
            "from": {"id": 12345},
            "chat": {"id": 12345, "type": "private"},
            "date": 0,
            "sticker": {"file_id": "sticker1"},
        },
    }
    with pytest.raises(ValueError, match="unsupported"):
        parse_update(update)
```

- [ ] **Step 4: Run tests, confirm fail**

Run: `pytest tests/test_intake.py -v`
Expected: ModuleNotFoundError on `bot.intake`.

- [ ] **Step 5: Write the implementation**

`bot/intake.py`:

```python
from typing import Optional, TypedDict


class IntakeResult(TypedDict):
    raw_text: Optional[str]
    media_type: str
    media_telegram_file_id: Optional[str]


def parse_update(update: dict) -> IntakeResult:
    msg = update.get("message") or update.get("channel_post")
    if not msg:
        raise ValueError("update has no message")

    # Forwarded messages first — a forward can also have text/photo/etc.
    is_forward = any(k in msg for k in ("forward_origin", "forward_from", "forward_from_chat"))
    if is_forward:
        return {
            "raw_text": msg.get("text") or msg.get("caption"),
            "media_type": "forward",
            "media_telegram_file_id": None,
        }

    if "photo" in msg:
        # Telegram sends multiple sizes; pick the largest by file_size
        largest = max(msg["photo"], key=lambda p: p.get("file_size", 0))
        return {
            "raw_text": msg.get("caption"),
            "media_type": "image",
            "media_telegram_file_id": largest["file_id"],
        }

    if "video" in msg:
        return {
            "raw_text": msg.get("caption"),
            "media_type": "video",
            "media_telegram_file_id": msg["video"]["file_id"],
        }

    if "voice" in msg:
        return {
            "raw_text": msg.get("caption"),
            "media_type": "voice",
            "media_telegram_file_id": msg["voice"]["file_id"],
        }

    if "document" in msg:
        return {
            "raw_text": msg.get("caption"),
            "media_type": "document",
            "media_telegram_file_id": msg["document"]["file_id"],
        }

    if "text" in msg:
        text = msg["text"]
        stripped = text.strip()
        if stripped.startswith(("http://", "https://")) and " " not in stripped:
            return {
                "raw_text": text,
                "media_type": "link",
                "media_telegram_file_id": None,
            }
        return {
            "raw_text": text,
            "media_type": "text",
            "media_telegram_file_id": None,
        }

    raise ValueError("update has unsupported content")
```

- [ ] **Step 6: Run tests, confirm pass**

Run: `pytest tests/test_intake.py -v`
Expected: 9 passed.

- [ ] **Step 7: Commit**

```bash
git add bot/intake.py tests/test_intake.py tests/fixtures/ tests/conftest.py
git commit -m "feat(intake): parse Telegram updates into typed IntakeResult"
```

---

## Task 6: Media — Telegram download + Dropbox upload + local fallback (TDD)

**Files:**
- Create: `bot/media.py`, `tests/test_media.py`

This task implements three things:
1. `download_from_telegram(bot_token, file_id)` — async, returns bytes.
2. `DropboxRefreshClient` — wraps the Dropbox SDK with refresh-token flow.
3. `upload_with_fallback(...)` — sync upload with 3 retries + local-disk fallback.

- [ ] **Step 1: Write the failing tests**

`tests/test_media.py`:

```python
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
```

- [ ] **Step 2: Run tests, confirm fail**

Run: `pytest tests/test_media.py -v`
Expected: ModuleNotFoundError on `bot.media`.

- [ ] **Step 3: Write the implementation**

`bot/media.py`:

```python
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

    # All retries exhausted — fall back to local disk
    fallback_dir.mkdir(parents=True, exist_ok=True)
    safe_name = dropbox_path.lstrip("/").replace("/", "__")
    fallback_path = fallback_dir / safe_name
    fallback_path.write_bytes(content)
    logger.error(
        "dropbox upload failed after %d attempts for %s, saved fallback to %s (last error: %s)",
        max_retries, dropbox_path, fallback_path, last_err,
    )
    return f"local-fallback://{fallback_path}"
```

- [ ] **Step 4: Run tests, confirm pass**

Run: `pytest tests/test_media.py -v`
Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add bot/media.py tests/test_media.py
git commit -m "feat(media): telegram download + dropbox upload with local fallback"
```

---

## Task 7: FastAPI webhook (TDD)

This is the entry point. It composes everything from Tasks 3-6.

**Files:**
- Create: `bot/main.py`, `tests/test_webhook.py`

- [ ] **Step 1: Write the failing tests**

`tests/test_webhook.py`:

```python
import pytest
from fastapi.testclient import TestClient
from unittest.mock import MagicMock, patch


@pytest.fixture
def client(env, mocker):
    """Build a TestClient with all external clients mocked.

    We patch the module-level singletons that bot.main creates at import time.
    """
    # Patch supabase.create_client BEFORE bot.main is imported
    mock_sb = MagicMock()
    mock_sb.table.return_value.insert.return_value.execute.return_value = MagicMock(
        data=[{"id": "test-item-id"}]
    )
    mocker.patch("bot.db.create_client", return_value=mock_sb)

    # Patch the dropbox client factory
    mocker.patch("bot.media.dropbox.Dropbox", return_value=MagicMock())

    # Mock outbound httpx for ack and Telegram file download
    import respx
    import httpx
    respx_mock = respx.mock(base_url="https://api.telegram.org", assert_all_called=False)
    respx_mock.start()
    respx_mock.post("/bottest_bot_token/sendMessage").mock(
        return_value=httpx.Response(200, json={"ok": True})
    )
    respx_mock.get("/bottest_bot_token/getFile").mock(
        return_value=httpx.Response(200, json={"ok": True, "result": {"file_path": "p/f.jpg"}})
    )
    respx_mock.get("/file/bottest_bot_token/p/f.jpg").mock(
        return_value=httpx.Response(200, content=b"jpeg-bytes")
    )

    from bot.main import app
    test_client = TestClient(app)
    yield test_client, mock_sb
    respx_mock.stop()


def test_webhook_rejects_wrong_secret(client):
    test_client, _ = client
    response = test_client.post(
        "/webhook/wrong_secret",
        json={"update_id": 1, "message": {"message_id": 1, "from": {"id": 12345},
              "chat": {"id": 12345, "type": "private"}, "date": 0, "text": "hi"}},
    )
    assert response.status_code == 403


def test_webhook_drops_unauthorized_sender_silently(client, load_fixture):
    test_client, mock_sb = client
    response = test_client.post(
        "/webhook/test_secret",
        json=load_fixture("update_unauthorized_sender"),
    )
    assert response.status_code == 200
    # No supabase insert should have happened
    mock_sb.table.return_value.insert.assert_not_called()


def test_webhook_text_inserts_pending_row(client, load_fixture):
    test_client, mock_sb = client
    response = test_client.post(
        "/webhook/test_secret",
        json=load_fixture("update_text"),
    )
    assert response.status_code == 200
    mock_sb.table.assert_any_call("items")
    insert_payload = mock_sb.table.return_value.insert.call_args[0][0]
    assert insert_payload["status"] == "pending"
    assert insert_payload["media_type"] == "text"
    assert insert_payload["raw_text"] == "Remember to call Walmart contact"
    assert insert_payload["media_telegram_file_id"] is None


def test_webhook_photo_inserts_row_and_schedules_media_handling(client, load_fixture):
    test_client, mock_sb = client
    response = test_client.post(
        "/webhook/test_secret",
        json=load_fixture("update_photo_with_caption"),
    )
    assert response.status_code == 200
    insert_payload = mock_sb.table.return_value.insert.call_args[0][0]
    assert insert_payload["media_type"] == "image"
    assert insert_payload["media_telegram_file_id"] == "large_file_id"
    assert insert_payload["raw_text"] == "for Lake Arrowhead bathroom"


def test_webhook_returns_quickly_even_if_media_processing_takes_time(client, load_fixture):
    """Webhook ack must not be blocked on Dropbox upload."""
    import time
    test_client, _ = client
    start = time.monotonic()
    response = test_client.post(
        "/webhook/test_secret",
        json=load_fixture("update_photo_with_caption"),
    )
    elapsed = time.monotonic() - start
    assert response.status_code == 200
    assert elapsed < 1.0  # webhook returned in <1sec; media handling is background
```

- [ ] **Step 2: Run tests, confirm fail**

Run: `pytest tests/test_webhook.py -v`
Expected: ModuleNotFoundError on `bot.main`.

- [ ] **Step 3: Write the implementation**

`bot/main.py`:

```python
import logging
from datetime import datetime, timezone

import httpx
from fastapi import BackgroundTasks, FastAPI, HTTPException, Request

from bot.config import Config
from bot.db import get_client, insert_item, update_media_path
from bot.intake import parse_update
from bot.media import DropboxRefreshClient, download_from_telegram, upload_with_fallback

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

config = Config.from_env()
supabase = get_client(config.supabase_url, config.supabase_service_key)
dropbox_factory = DropboxRefreshClient(
    config.dropbox_refresh_token,
    config.dropbox_app_key,
    config.dropbox_app_secret,
)

app = FastAPI()


@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.post("/webhook/{secret}")
async def webhook(secret: str, request: Request, background_tasks: BackgroundTasks):
    if secret != config.webhook_secret:
        raise HTTPException(status_code=403, detail="invalid webhook secret")

    update = await request.json()

    msg = update.get("message") or update.get("channel_post") or {}
    sender_id = (msg.get("from") or {}).get("id")
    if sender_id != config.my_telegram_id:
        logger.info("dropping update from unauthorized sender id=%s", sender_id)
        return {"ok": True}

    try:
        intake = parse_update(update)
    except ValueError as exc:
        logger.warning("could not parse update: %s", exc)
        return {"ok": True}

    chat_id = msg["chat"]["id"]
    message_id = msg["message_id"]

    item = insert_item(
        supabase,
        source_message_id=str(message_id),
        raw_text=intake["raw_text"],
        media_type=intake["media_type"],
        media_telegram_file_id=intake["media_telegram_file_id"],
    )

    if intake["media_telegram_file_id"]:
        background_tasks.add_task(
            handle_media,
            item_id=item["id"],
            file_id=intake["media_telegram_file_id"],
            ext=ext_for_media_type(intake["media_type"]),
        )

    background_tasks.add_task(send_ack, chat_id=chat_id, reply_to=message_id)
    return {"ok": True}


async def handle_media(item_id: str, file_id: str, ext: str) -> None:
    try:
        content = await download_from_telegram(config.bot_token, file_id)
        date_path = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        dropbox_path = f"/personal-os-inbox/{date_path}/{item_id}{ext}"
        result_path = upload_with_fallback(
            dropbox_factory.get_client,
            dropbox_path=dropbox_path,
            content=content,
        )
        update_media_path(supabase, item_id, result_path)
    except Exception:
        logger.exception("media handling failed for item %s", item_id)


async def send_ack(chat_id: int, reply_to: int) -> None:
    try:
        async with httpx.AsyncClient(timeout=10.0) as http:
            await http.post(
                f"https://api.telegram.org/bot{config.bot_token}/sendMessage",
                json={
                    "chat_id": chat_id,
                    "text": "👍",
                    "reply_to_message_id": reply_to,
                },
            )
    except Exception:
        logger.exception("ack failed for chat %s", chat_id)


def ext_for_media_type(media_type: str) -> str:
    return {
        "image": ".jpg",
        "video": ".mp4",
        "voice": ".ogg",
        "document": ".bin",
    }.get(media_type, ".bin")
```

- [ ] **Step 4: Run tests, confirm pass**

Run: `pytest tests/test_webhook.py -v`
Expected: 5 passed.

- [ ] **Step 5: Run full test suite**

Run: `pytest -v`
Expected: all tests pass (~26 tests across 5 files).

- [ ] **Step 6: Commit**

```bash
git add bot/main.py tests/test_webhook.py
git commit -m "feat(webhook): FastAPI route with background media handling"
```

---

## Task 8: Local manual smoke test (no droplet, just runs locally against real Supabase + Dropbox)

This validates the full happy path against the real Supabase project and Dropbox app, before we touch the droplet.

- [ ] **Step 1: Fill in `.env`** at the repo root with real values from prerequisites P1, P2, P5, plus `DROPBOX_REFRESH_TOKEN` (do that first via Task 11 OR do this task after Task 11). For now we'll defer Step 1 of this task until Task 11 is done.

- [ ] **Step 2: Run uvicorn locally**

```bash
.venv\Scripts\activate
uvicorn bot.main:app --host 127.0.0.1 --port 8000 --reload
```

Expected: server starts, no errors. `curl http://127.0.0.1:8000/healthz` returns `{"ok":true}`.

- [ ] **Step 3: Send a fake Telegram update via curl**

In another terminal, with the server running:

```bash
curl -X POST "http://127.0.0.1:8000/webhook/<your-real-WEBHOOK_SECRET>" `
    -H "Content-Type: application/json" `
    -d '{\"update_id\":99999,\"message\":{\"message_id\":99999,\"from\":{\"id\":<your-real-MY_TELEGRAM_ID>,\"is_bot\":false,\"first_name\":\"Ryan\"},\"chat\":{\"id\":<your-real-MY_TELEGRAM_ID>,\"type\":\"private\"},\"date\":0,\"text\":\"local smoke test - text\"}}'
```

Expected: 200 response.

- [ ] **Step 4: Verify Supabase row**

Use the MCP tool:
```
mcp__claude_ai_Supabase__execute_sql(
    project_id="<roscoe-project-id>",
    query="select id, status, media_type, raw_text from items order by created_at desc limit 1"
)
```

Expected: one row with `status=pending`, `media_type=text`, `raw_text="local smoke test - text"`.

- [ ] **Step 5: Send a real Telegram message (loop the public bot in)**

This step requires the production bot, so it's the live deploy smoke test — do as part of Task 12.

- [ ] **Step 6: Note** — no commit here since this task only involves verification, not code.

---

## Task 9: One-time Dropbox OAuth setup script

Refresh-token flow requires a one-time interactive auth dance. This script prints a URL, the user authorizes, pastes back the auth code, and the script exchanges it for a refresh token.

**Files:**
- Create: `scripts/setup_dropbox_oauth.py`

- [ ] **Step 1: Write the script**

`scripts/setup_dropbox_oauth.py`:

```python
"""One-time setup: get a Dropbox refresh token.

Usage:
    python scripts/setup_dropbox_oauth.py <APP_KEY> <APP_SECRET>

Steps:
    1. Script prints a Dropbox auth URL — open it in a browser.
    2. Authorize the app for your Dropbox account.
    3. Dropbox shows you an authorization code — paste it back into the
       terminal.
    4. Script exchanges the code for a long-lived refresh token and prints
       it. Copy that into .env as DROPBOX_REFRESH_TOKEN.
"""

import sys

from dropbox import DropboxOAuth2FlowNoRedirect


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__)
        return 1

    app_key, app_secret = sys.argv[1], sys.argv[2]

    flow = DropboxOAuth2FlowNoRedirect(
        consumer_key=app_key,
        consumer_secret=app_secret,
        token_access_type="offline",  # required for refresh tokens
    )

    auth_url = flow.start()
    print()
    print("1. Open this URL in your browser:")
    print(f"   {auth_url}")
    print()
    print("2. Click 'Allow' to grant the app access to your Dropbox.")
    print("3. Dropbox will display an authorization code. Copy it.")
    print()
    auth_code = input("Paste the authorization code here: ").strip()

    try:
        result = flow.finish(auth_code)
    except Exception as exc:
        print(f"\nError exchanging code: {exc}", file=sys.stderr)
        return 2

    print()
    print("Success. Save this refresh token to your .env as DROPBOX_REFRESH_TOKEN:")
    print()
    print(f"DROPBOX_REFRESH_TOKEN={result.refresh_token}")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: Run the script with real values from prerequisite P2**

```bash
python scripts/setup_dropbox_oauth.py <DROPBOX_APP_KEY> <DROPBOX_APP_SECRET>
```

Follow the prompts. Copy the printed `DROPBOX_REFRESH_TOKEN=...` line into the `.env` file at the repo root.

- [ ] **Step 3: Verify** the `.env` file at the repo root now has all 8 values populated.

- [ ] **Step 4: Commit the script (without .env)**

```bash
git add scripts/setup_dropbox_oauth.py
git commit -m "feat(scripts): one-time Dropbox refresh-token setup"
```

---

## Task 10: Local manual smoke test (revisit Task 8 with real Dropbox)

Now that `.env` is fully populated, repeat Task 8 with a photo to confirm Dropbox upload works end-to-end.

- [ ] **Step 1: Restart uvicorn locally** (it picked up the new `.env` automatically since `python-dotenv` is loaded at import; if not, restart):

```bash
uvicorn bot.main:app --host 127.0.0.1 --port 8000 --reload
```

- [ ] **Step 2: Send a synthetic photo update via curl**

This requires a real Telegram `file_id`, which only Telegram can issue. So this step actually requires going through the real bot. **Skip to Task 12** for the photo path; the curl test from Task 8 (text only) is sufficient validation here.

- [ ] **Step 3: Confirm** that the curl text-only test from Task 8 still works with the real Supabase, by checking a row appears.

No commit — verification only.

---

## Task 11: Deployment artifacts

**Files:**
- Create: `deploy/deploy.sh`, `deploy/personal-os-v2.service`

These artifacts let the operator deploy v2 to the droplet at `/opt/personal-os-v2/`, alongside (not replacing) v1 at `/opt/personal-os/`. Cutover happens by swapping the systemd service.

- [ ] **Step 1: Write `deploy/deploy.sh`**

```bash
#!/usr/bin/env bash
# Deploy roscoe-robot v2 to the droplet.
#
# Usage:  ./deploy/deploy.sh
#
# Run from the repo root on your local machine. Requires SSH key access to
# root@64.23.170.115. Pushes the bot/ folder, requirements.txt, and .env to
# /opt/personal-os-v2/, installs deps in a venv there, and reloads systemd.
#
# This does NOT start the v2 service or change the active webhook. After
# this script succeeds, do the cutover steps in Task 12.

set -euo pipefail

DROPLET_HOST="root@64.23.170.115"
REMOTE_DIR="/opt/personal-os-v2"

echo ">> Ensuring remote dir exists..."
ssh "$DROPLET_HOST" "mkdir -p $REMOTE_DIR"

echo ">> Rsyncing code (excludes .venv, .git, __pycache__, tests)..."
rsync -avz \
    --exclude '.venv' \
    --exclude '.git' \
    --exclude '__pycache__' \
    --exclude '.pytest_cache' \
    --exclude 'tests' \
    --exclude 'docs' \
    --exclude 'scripts' \
    --exclude 'migrations' \
    --exclude 'deploy' \
    --exclude '.env' \
    --exclude '.env.example' \
    --exclude 'spec.md' \
    --exclude 'pyproject.toml' \
    bot/ requirements.txt \
    "$DROPLET_HOST:$REMOTE_DIR/"

echo ">> Copying .env (you'll be prompted)..."
scp .env "$DROPLET_HOST:$REMOTE_DIR/.env"
ssh "$DROPLET_HOST" "chmod 600 $REMOTE_DIR/.env"

echo ">> Creating venv and installing deps on droplet..."
ssh "$DROPLET_HOST" "
    set -e
    cd $REMOTE_DIR
    if [ ! -d venv ]; then python3 -m venv venv; fi
    venv/bin/pip install --upgrade pip
    venv/bin/pip install -r requirements.txt
"

echo ">> Copying systemd unit (will overwrite)..."
scp deploy/personal-os-v2.service "$DROPLET_HOST:/etc/systemd/system/personal-os-v2.service"
ssh "$DROPLET_HOST" "systemctl daemon-reload"

echo ">> Done. Cutover steps:"
echo "   ssh $DROPLET_HOST 'systemctl stop personal-os && systemctl start personal-os-v2'"
echo "   then re-register the Telegram webhook to point at the v2 endpoint (same URL,"
echo "   probably a different port — confirm before flipping)."
```

- [ ] **Step 2: Make it executable**

```bash
chmod +x deploy/deploy.sh
```

(On Windows, `chmod` is a no-op; the file is executable on the droplet after rsync.)

- [ ] **Step 3: Write `deploy/personal-os-v2.service`**

```ini
[Unit]
Description=Personal OS bot v2 (capture pipe → Supabase + Dropbox)
After=network.target
Wants=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/personal-os-v2
EnvironmentFile=/opt/personal-os-v2/.env
ExecStart=/opt/personal-os-v2/venv/bin/uvicorn bot.main:app \
    --host 0.0.0.0 \
    --port 8443 \
    --ssl-keyfile /opt/personal-os/ssl.key \
    --ssl-certfile /opt/personal-os/ssl.crt
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Note: v2 uses port **8443** (not 443) so v1 stays on 443 during cutover. After v2 is verified, update the Telegram webhook to the v2 port and stop v1 — see Task 12.

- [ ] **Step 4: Commit**

```bash
git add deploy/deploy.sh deploy/personal-os-v2.service
git commit -m "feat(deploy): deploy script + systemd unit for v2"
```

---

## Task 12: Production deployment + live smoke test

These are operator steps run from the local repo root + on the droplet.

- [ ] **Step 1: Open firewall for v2 port**

```bash
ssh root@64.23.170.115 "ufw allow 8443/tcp && ufw status"
```

Expected: 8443/tcp listed.

- [ ] **Step 2: Run deploy script from local repo root**

```bash
./deploy/deploy.sh
```

Expected: rsync output, deps installed, systemd reloaded, instructions printed.

- [ ] **Step 3: Start v2 service**

```bash
ssh root@64.23.170.115 "systemctl enable personal-os-v2 && systemctl start personal-os-v2"
```

- [ ] **Step 4: Verify v2 is running**

```bash
ssh root@64.23.170.115 "systemctl status personal-os-v2 --no-pager | head -15"
ssh root@64.23.170.115 "journalctl -u personal-os-v2 -n 20 --no-pager"
```

Expected: active (running), recent log lines including FastAPI startup.

- [ ] **Step 5: Verify v2 healthz**

```bash
ssh root@64.23.170.115 "curl -k https://localhost:8443/healthz"
```

Expected: `{"ok":true}`.

- [ ] **Step 6: Re-register Telegram webhook to v2 endpoint**

On the droplet, with the v1 secret value substituted:

```bash
ssh root@64.23.170.115 "
    source /opt/personal-os-v2/.env && \
    curl -F url=https://64.23.170.115:8443/webhook/\${WEBHOOK_SECRET} \
         -F certificate=@/opt/personal-os/ssl.crt \
         -F drop_pending_updates=true \
         https://api.telegram.org/bot\${BOT_TOKEN}/setWebhook
"
```

Expected: `{"ok":true,"result":true,"description":"Webhook was set"}`.

- [ ] **Step 7: Stop v1**

```bash
ssh root@64.23.170.115 "systemctl stop personal-os && systemctl disable personal-os"
```

V1's bot.py and inbox.log stay at /opt/personal-os/ as a fallback for instant rollback.

- [ ] **Step 8: Live smoke test — text**

In Telegram, message Roscoe: `live test 1 - text`.

Expected: bot replies with 👍 within ~2 seconds.

Verify in Supabase:
```
mcp__claude_ai_Supabase__execute_sql(
    project_id="<roscoe-project-id>",
    query="select id, status, media_type, raw_text, created_at from items order by created_at desc limit 1"
)
```
Row exists, `media_type=text`, `status=pending`, `raw_text="live test 1 - text"`.

- [ ] **Step 9: Live smoke test — photo with caption**

Send a photo with caption "live test 2 - photo".

Expected: 👍 within ~2 seconds. Eventually (within ~5-15 seconds) the row's `media_dropbox_path` updates to a `/personal-os-inbox/2026-MM-DD/<uuid>.jpg`.

Verify:
```
select media_type, raw_text, media_dropbox_path, media_telegram_file_id
from items where source_message_id = '<message_id>';
```

Confirm the file actually exists in Dropbox at `Apps/roscoe-robot/personal-os-inbox/2026-MM-DD/<uuid>.jpg`.

- [ ] **Step 10: Live smoke test — photo without caption**

Send a photo with no caption.

Expected: same as Step 9, but `raw_text` is null.

- [ ] **Step 11: Live smoke test — video**

Send a short video.

Expected: 👍, row inserted with `media_type=video`, dropbox path populated.

- [ ] **Step 12: Live smoke test — voice memo**

Hold the mic in Telegram, say "test voice memo," release.

Expected: 👍, row inserted with `media_type=voice`, dropbox path populated with `.ogg`.

- [ ] **Step 13: Live smoke test — link**

Send a URL-only message: `https://example.com/article-test`.

Expected: 👍, row inserted with `media_type=link`, no dropbox path.

- [ ] **Step 14: Live smoke test — forwarded message**

Forward any message from another chat to Roscoe.

Expected: 👍, row inserted with `media_type=forward`, dropbox path null (forwards just preserve text in v1; we add forward-media handling later if needed).

- [ ] **Step 15: Live smoke test — unauthorized sender**

Have a friend (or a second Telegram account) message the bot.

Expected: no reply, no Supabase row inserted, droplet log shows "dropping update from unauthorized sender."

Verify:
```bash
ssh root@64.23.170.115 "journalctl -u personal-os-v2 --since '2 minutes ago' | grep unauthorized"
```

- [ ] **Step 16: Verify Supabase row counts after the smoke run**

```
mcp__claude_ai_Supabase__execute_sql(
    project_id="<roscoe-project-id>",
    query="select media_type, count(*) from items group by media_type order by media_type"
)
```

Expected: counts roughly matching what you sent — at least 1 of each type tested.

- [ ] **Step 17: Reboot test (optional but recommended)**

```bash
ssh root@64.23.170.115 "reboot"
```

Wait ~60 seconds, then:
```bash
ssh root@64.23.170.115 "systemctl status personal-os-v2 --no-pager | head -5"
```

Expected: active. Send another Telegram message → 👍 → row appears. Confirms auto-restart on reboot works.

---

## Verification gate (Session 2 done-ness)

Session 2 is complete when ALL of the following are true:

- [ ] All unit tests pass (`pytest` at the repo root).
- [ ] All seven Task 12 live smoke tests pass: text, photo with caption, photo without caption, video, voice, link, forward.
- [ ] Unauthorized sender is silently dropped.
- [ ] All Supabase rows from the smoke tests have `status=pending`, `source=telegram`, the right `media_type`, and (for media types) a non-null `media_dropbox_path` after a few seconds.
- [ ] Photo, video, voice files are actually present in Dropbox at the paths the rows record.
- [ ] Bot replies with 👍 within ~2 seconds for every message type.
- [ ] V1 service is stopped/disabled; v2 is enabled and survives a reboot.
- [ ] All code is committed to `main` and pushed to GitHub.

After this passes, **let the system run live for at least one full week** before starting Session 3. The point is to validate intake reliability under real use — surface any media types or edge cases that the synthetic tests didn't catch (Telegram updates have a long tail of unusual fields).

## Out of scope (defer to later sessions)

- No classification or Claude API calls (Session 3).
- No Obsidian writes (Session 3).
- No Todoist creation (Session 3).
- No daily summary or triage flow (Session 4).
- No cron / autonomy (Session 5).
- No `/process` command (Session 3).
- No migration of existing inbox.log entries (skip — start fresh).

## Open items resolved during this session

- **Dropbox auth.** Refresh-token OAuth flow via the one-time `setup_dropbox_oauth.py` script. The refresh token is stored in `.env` and used to mint short-lived access tokens at request time.
- **Telegram URL expiration.** Mitigated by downloading at intake (within seconds of receiving the webhook) via FastAPI background tasks.
- **Existing v1 bot.** Stays on the droplet at `/opt/personal-os/` as a frozen fallback. V2 lives at `/opt/personal-os-v2/` and runs on a different port; cutover is a webhook-URL swap + systemd swap. Roll back by reversing those two steps.
- **Polymorphic vs typed schema.** One `items` table with discriminator columns (`media_type`, `type`) — confirmed in the design.
