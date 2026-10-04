-- MyAnimeList as a second sync target.
-- A run syncs MangaDex to one site. For target 'mal', sync_item reuses the AniList-named columns for the
-- MyAnimeList list entry: al_entry_id = MAL manga id when the series is on the MAL list (NULL = add),
-- al_progress / al_status_before = MAL chapters read / status. al_media_id still names the AniList
-- match (titles, covers), and mal_id is always the MAL manga id.
ALTER TABLE sync_run ADD COLUMN target TEXT NOT NULL DEFAULT 'anilist';
ALTER TABLE sync_run ADD COLUMN req_mal INTEGER NOT NULL DEFAULT 0;
ALTER TABLE sync_item ADD COLUMN mal_id INTEGER;
-- The user's MAL manga list, replaced on every read. Statuses use AniList's words (CURRENT, COMPLETED,
-- PAUSED, DROPPED, PLANNING, REPEATING) so the rules and labels are shared; the writer maps them back.
CREATE TABLE mal_entry(mal_id INTEGER PRIMARY KEY, status TEXT NOT NULL, progress INTEGER NOT NULL,
  volumes INTEGER, score INTEGER, title TEXT, chapters INTEGER,   -- chapters NULL = MAL does not know
  media_status TEXT,                                               -- FINISHED, RELEASING, ... (AniList words)
  picture TEXT, updated_at TEXT, fetched_at TEXT NOT NULL);
