-- Tags come with the list request; staff is backfilled in small batches. NULL = not fetched yet.
ALTER TABLE al_media ADD COLUMN tags TEXT;          -- JSON [{name, rank, category}] (spoiler tags dropped)
ALTER TABLE al_media ADD COLUMN staff_roles TEXT;   -- JSON [{id, name, role}] (story/art creators)
