-- The hourly check: MangaDex's newest chapter per series, to tell what changed since the last look.
ALTER TABLE md_new ADD COLUMN latest_chapter_id TEXT;   -- attributes.latestUploadedChapter; NULL = not known yet
