-- Your notes on list entries (AniList "notes", mirrored to MyAnimeList "comments" on request).
ALTER TABLE al_entry ADD COLUMN notes TEXT;            -- NULL = not read yet; '' = no notes
-- list_edit also records notes now, and on which site an edit was made.
CREATE TABLE list_edit_new(id INTEGER PRIMARY KEY,
  media_id INTEGER NOT NULL, entry_id INTEGER NOT NULL,  -- for MyAnimeList: the MAL id
  site TEXT NOT NULL DEFAULT 'anilist' CHECK(site IN ('anilist','mal')),
  field TEXT NOT NULL CHECK(field IN ('score','status','notes')),
  old TEXT, new TEXT NOT NULL,
  state TEXT NOT NULL CHECK(state IN ('done','failed')), error TEXT,
  at TEXT NOT NULL);
INSERT INTO list_edit_new(id, media_id, entry_id, field, old, new, state, error, at)
  SELECT id, media_id, entry_id, field, old, new, state, error, at FROM list_edit;
DROP TABLE list_edit;
ALTER TABLE list_edit_new RENAME TO list_edit;
