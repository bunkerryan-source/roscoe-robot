-- Session 4 — triage flow adds a 'discarded' status for items the user
-- trashes via the inline-keyboard 🗑 button. The original items_status_check
-- constraint from 001_initial_schema.sql only allowed pending / processed /
-- needs_review / failed, so any UPDATE setting status='discarded' was
-- rejected with constraint violation 23514. Recreate the check with
-- 'discarded' added.

alter table items drop constraint items_status_check;

alter table items add constraint items_status_check
  check (status in ('pending', 'processed', 'needs_review', 'failed', 'discarded'));
