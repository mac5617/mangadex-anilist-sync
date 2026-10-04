-- Fields for the stats pages. All come with the one MediaListCollection request a sync already makes.
ALTER TABLE al_media ADD COLUMN genres TEXT NOT NULL DEFAULT '[]';   -- JSON list
ALTER TABLE al_entry ADD COLUMN score REAL;                          -- POINT_100; NULL = unscored
ALTER TABLE al_entry ADD COLUMN progress_volumes INTEGER;
ALTER TABLE al_entry ADD COLUMN started_at TEXT;                     -- YYYY[-MM[-DD]] as AniList has it
ALTER TABLE al_entry ADD COLUMN completed_at TEXT;
ALTER TABLE al_entry ADD COLUMN updated_at INTEGER;                  -- Unix time of the entry's last change
