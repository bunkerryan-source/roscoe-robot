-- 002_source_posts_and_corrections_extension.sql
-- Session 4: add normalized source_posts table for scraped X posts,
-- link items via source_post_id (multi-image fan-out), and extend the
-- existing corrections table with correction_type + note plus nullable values.

-- 1. source_posts: deduped by (source, source_url)
create table public.source_posts (
    id                       uuid primary key default uuid_generate_v4(),
    created_at               timestamptz not null default now(),
    source                   text not null,                  -- 'x' for now; future: 'youtube', 'instagram'
    source_url               text not null,
    post_text                text,                           -- scraped body, NEVER from user input
    author_handle            text,                           -- e.g. '@naval'
    author_name              text,                           -- display name
    posted_at                timestamptz,                    -- when the tweet was posted
    image_urls               text[] not null default '{}',   -- ordered list as returned by scraper
    midjourney_params        jsonb,                          -- {sref, ar, style, v, niji, chaos, stylize, weird}
    raw_scraper_response     jsonb,                          -- full Apify response, for debugging
    unique (source, source_url)
);

create index source_posts_source_url_idx on public.source_posts (source_url);

-- 2. items.source_post_id: N items rows can share one source_posts row (multi-image fan-out)
alter table public.items
    add column source_post_id uuid references public.source_posts(id) on delete set null;

create index items_source_post_id_idx on public.items (source_post_id);

-- 3. corrections extensions: generic value columns, add correction_type + note.
--    Existing rows are 0 (Session 4 is what starts populating this); safe to backfill with 'project'.
alter table public.corrections rename column original_class to original_value;
alter table public.corrections rename column corrected_class to corrected_value;
alter table public.corrections alter column original_value drop not null;
alter table public.corrections alter column corrected_value drop not null;

alter table public.corrections add column correction_type text;
update public.corrections set correction_type = 'project' where correction_type is null;
alter table public.corrections alter column correction_type set not null;

alter table public.corrections add column note text;

create index corrections_item_id_idx on public.corrections (item_id);
-- corrections_created_at_idx already exists from 001; no need to recreate.
