# Ask: roscoe-robot — store video files so design-dashboard can render them

> **This is a cross-project ask, written from inside `design-dashboard`.** It's a request for `roscoe-robot` to make a small data-layer change. Copy / reference this file from a roscoe-robot session; do not implement it from the dashboard side.

## Why this exists

The design-dashboard is a Pinterest-style read-only viewer over `items` rows where `project='design' AND type='image'`. Ryan also captures **design videos** (mostly short X/Twitter clips and screen recordings of landing-page interactions). The dashboard would like to include those in the grid + detail flow — first frame as the tile, full video on click.

Today there's exactly one row with `project='design' AND type='video'`:

```
id:                   2e38f4d8-e426-48b7-9a60-2b68d521d2f0
type:                 video
status:               processed
summary:              "X video showcasing a landing page template design with
                       pricing and hero section inspiration."
tags:                 ["pricing-page", "hero"]
media_dropbox_path:   null   ← problem
source_post_id:       null   ← also problem
```

`roscoe-robot` clearly **processed** the video well enough to summarize it and tag it, but it never stored the file in Dropbox and never linked an X `source_posts` row. The dashboard has nothing renderable.

This file describes the smallest change that would unblock the dashboard.

## What needs to land in roscoe-robot

### 1. (Required) Populate `items.media_dropbox_path` for video captures

Whatever channel `type='video'` items arrive through (Telegram video message, X scrape, screen recording forward, voice memo containing a link, etc.), the processor should save the video file into the same Dropbox vault structure it uses for images, and write the resulting path into `items.media_dropbox_path`.

The dashboard treats `media_dropbox_path` as **the displayable asset, polymorphic by type**:
- `type='image'` → path points to a JPG/PNG/WEBP
- `type='video'` → path points to an MP4/MOV/WEBM

That's the entire contract for v1. The dashboard will:
- Mint a signed Dropbox URL the same way it does today (`/2/files/get_temporary_link` works for any file type).
- Render `<video preload="metadata" muted>` in the grid (browsers show the first frame automatically) and `<video controls>` on the detail page.

### 2. (Required, separate-ish) Either link a `source_post` or accept rows without one

The dashboard's detail panel includes a "From the source post" section, an "Open original ↗" link, and "More from this post" sibling thumbnails — all keyed off `items.source_post_id`. For X-scraped videos, that linkage matters for parity with how Ryan uses the image flow.

Either:

- **(a)** Make the X scraper attach video items to a `source_posts` row exactly like image items do — same `source_post_id`, same `image_urls` (rename eventually if it bugs anyone, but works as-is), same `midjourney_params` parsing if applicable; OR
- **(b)** Confirm and document that videos can arrive standalone (no source_post). The dashboard already handles `source_post_id = null` gracefully — sections just don't render. So no dashboard change needed, just a note that the empty-detail UX is intentional for non-X video captures.

The dashboard accepts either world; just want to know which one to expect.

### 3. (Optional, nice to have) Add `items.poster_dropbox_path` for a still frame

For a grid showing 30+ videos, asking the browser to load metadata for each `<video>` tag — even with `preload="metadata"` — is wasteful (Dropbox temp URLs are direct downloads with no `Range` support optimizations).

Future fix: roscoe extracts the first frame as a JPG during capture (ffmpeg one-liner) and stores it at e.g. `…/posters/<item_id>.jpg`, writes the path to a new `items.poster_dropbox_path` column. Dashboard then uses the poster image as the `<video poster=...>` attribute, OR renders a plain `<img>` for video items in the grid and only loads the video on the detail page.

**Defer this until v1 is shipped and the grid actually feels slow.** Could also slot into a separate "design-dashboard performance" session later, owned jointly.

### 4. (Optional, even-nicer) Reprocess the existing video row

The one existing video item (`2e38f4d8-…`) has good metadata but no file. If the original Telegram/X content is still recoverable, the cleanest end-state is a backfill script that:

- Looks up the original capture (Telegram message id, X URL, whatever roscoe persisted) for any `items` row where `project='design' AND type='video' AND media_dropbox_path IS NULL`.
- Re-downloads and stores the file.
- Updates the row in place.

If the original content isn't recoverable, just delete that row or flip its status to `discarded`. The dashboard is fine either way.

## What the dashboard will do after this lands

This part is for design-dashboard's own backlog, listed here so both sides see the full picture. **Do not implement this in roscoe-robot.**

```diff
- // lib/items.ts
- .eq('type', 'image')
+ .in('type', ['image', 'video'])
```

Plus a small component branch in `image-card.tsx` / `detail-panel.tsx` to render `<video>` instead of `<img>` for `item.type === 'video'`. Probably ~30 minutes of work once roscoe is updated.

## Schema-change checklist (per roscoe-robot conventions)

If part #3 (the `poster_dropbox_path` column) gets implemented:

- [ ] New file in `migrations/` adding the column (`ALTER TABLE items ADD COLUMN poster_dropbox_path text;`). Existing rows get NULL.
- [ ] Manual paste into Supabase SQL Editor (no migration runner in the roscoe repo).
- [ ] Update any roscoe-side type definitions / model classes.
- [ ] **Tell the dashboard project** — bump [design-dashboard/lib/types.ts](../../../lib/types.ts) `Item` type to include the new field. (This is the contract.)

If parts #1 and #2 don't require schema changes (they shouldn't — `media_dropbox_path` and `source_post_id` already exist), no migration is needed. Just code that populates them.

## Non-goals

- **No new `type` values.** `'video'` already exists in the items table and works with the existing CHECK constraint. Don't introduce `'design-video'` or similar — the project + type combination is enough.
- **No format conversion.** Whatever format the source provides (mp4, webm, mov) goes straight into Dropbox. Browsers handle all three. No ffmpeg transcoding step needed.
- **No video LLM analysis beyond what already runs.** The existing processor's video summary + tag generation is fine. This ask is purely "store the bytes so the dashboard can play them."
- **No changes to the design-dashboard's contract beyond what's listed.** If a column gets added or renamed in roscoe, propose it; don't ship and let the dashboard discover the change at runtime.

## How Ryan will verify this worked

After deploying the roscoe change, in a roscoe session or by hand:

1. Send a short design video to the Telegram bot (screen recording of a slick landing page works).
2. Run `/process`.
3. Query Supabase: a new `items` row should appear with `project='design' AND type='video' AND media_dropbox_path IS NOT NULL`.
4. Confirm the Dropbox path actually resolves (open it in Dropbox, or hit the dashboard's `/api/image/<id>` endpoint — it'll return a signed URL that downloads the file).

Then ping the design-dashboard side; the rendering work there should be a small, single-session PR.

## Filing the work in roscoe-robot

Per the roscoe-robot conventions (its CLAUDE.md):

- Plan file: `docs/superpowers/plans/2026-05-12-session-N-video-storage.md` (whatever the next session number is — currently Sessions 2–5 shipped + Session 6 drafted, so this is likely Session 7 or later).
- Commits: `feat(session-N): store video files in Dropbox for design+video items` style.
- TDD: failing test first. Tests for: video download path lands in the right Dropbox folder, `media_dropbox_path` populated on the items row, processor never raises if the download fails (per the "per-item failures must not kill the batch" invariant).
- Don't add new triggers to `runs.trigger` — this is just normal processing, no new constraint needed.
