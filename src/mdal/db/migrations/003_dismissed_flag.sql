-- Flags the user dismissed. A dismissal holds while MangaDex progress stays at md_progress;
-- new reads re-flag the series so it can be looked at again.
CREATE TABLE dismissed_flag(md_id TEXT PRIMARY KEY, md_progress INTEGER NOT NULL,
  flag_kind TEXT NOT NULL, reason TEXT, dismissed_at TEXT NOT NULL);
