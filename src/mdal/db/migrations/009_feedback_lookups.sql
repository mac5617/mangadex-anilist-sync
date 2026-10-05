-- Your verdicts on recommended series, cached lookups for series pages (AniList, MyAnimeList, MangaUpdates),
-- MangaUpdates ids from MangaDex links, and what an Ask message changed.
CREATE TABLE rec_feedback(key TEXT PRIMARY KEY,        -- 'al:<AniList id>' or 'md:<MangaDex uuid>'
  verdict TEXT NOT NULL CHECK(verdict IN ('loved','liked','disliked','not_interested','read')),
  title TEXT NOT NULL,
  source TEXT NOT NULL CHECK(source IN ('page','ask','hide')),
  -- What the series is, kept with the verdict (a new release can drop out of later scans):
  genres TEXT NOT NULL DEFAULT '[]', tags TEXT NOT NULL DEFAULT '[]',   -- JSON lists of names
  staff TEXT NOT NULL DEFAULT '[]',                                     -- JSON [{id, name}] (AniList creators)
  description TEXT,
  created_at TEXT NOT NULL);
-- kind: 'anilist' (readers' recommendations), 'mal' (readers' recommendations), 'mu' (MangaUpdates series).
-- data is JSON; NULL means looked up and nothing found.
CREATE TABLE lookup_cache(key TEXT NOT NULL, kind TEXT NOT NULL, data TEXT, fetched_at TEXT NOT NULL,
  PRIMARY KEY(key, kind));
ALTER TABLE md_new ADD COLUMN mu_id TEXT;              -- MangaUpdates id (base 36) from the MangaDex links
ALTER TABLE chat_message ADD COLUMN notes TEXT;        -- JSON [text], e.g. a rating the message saved
