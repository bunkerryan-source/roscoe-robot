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
