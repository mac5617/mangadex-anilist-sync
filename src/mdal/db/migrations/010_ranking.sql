-- Your ranking (compare two series at a time, like Beli): an ordered list per tier; a series' score
-- follows from where it sits. Edits Shiori made to your AniList list outside syncs. Friends' lists.
CREATE TABLE ranking(media_id INTEGER PRIMARY KEY,
  tier TEXT NOT NULL CHECK(tier IN ('liked','fine','disliked')),
  position REAL NOT NULL,                       -- order within the tier: lower is better (fractions insert between)
  ranked_at TEXT NOT NULL);
CREATE TABLE list_edit(id INTEGER PRIMARY KEY,
  media_id INTEGER NOT NULL, entry_id INTEGER NOT NULL,
  field TEXT NOT NULL CHECK(field IN ('score','status')),
  old TEXT, new TEXT NOT NULL,
  state TEXT NOT NULL CHECK(state IN ('done','failed')), error TEXT,
  at TEXT NOT NULL);
CREATE TABLE friend(name TEXT PRIMARY KEY COLLATE NOCASE, user_id INTEGER,
  entries TEXT NOT NULL,                        -- JSON [{media_id, status, score, progress}]
  fetched_at TEXT NOT NULL);
