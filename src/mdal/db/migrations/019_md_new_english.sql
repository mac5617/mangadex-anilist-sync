-- New releases come from new English chapters: the highest one in the last week, and when the newest went up.
ALTER TABLE md_new ADD COLUMN en_chapter TEXT;      -- chapter number as MangaDex writes it; NULL for a oneshot
ALTER TABLE md_new ADD COLUMN en_chapter_at TEXT;   -- readableAt of the newest; NULL = from a scan before this one
