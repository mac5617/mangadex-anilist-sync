-- Notes on series that aren't on your AniList list (those on it keep their notes on the AniList entry).
CREATE TABLE series_note(key TEXT PRIMARY KEY,         -- 'al:<id>' or 'md:<uuid>'
  notes TEXT NOT NULL, updated_at TEXT NOT NULL);
