CREATE TABLE settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);

-- MangaDex snapshot (replaced each fetch, except md_chapter which is a permanent cache)
CREATE TABLE md_manga(
  md_id TEXT PRIMARY KEY, in_library INTEGER NOT NULL, reading_status TEXT,
  title TEXT NOT NULL, alt_titles TEXT NOT NULL,          -- JSON list of strings
  original_language TEXT, year INTEGER, pub_status TEXT, last_chapter TEXT,
  links TEXT NOT NULL, links_hash TEXT NOT NULL,           -- JSON map; sha1 of sorted JSON
  authors TEXT NOT NULL, cover_file TEXT,
  chapter_numbers_reset INTEGER NOT NULL DEFAULT 0, fetched_at TEXT NOT NULL);
CREATE TABLE md_read(md_id TEXT NOT NULL, chapter_id TEXT NOT NULL, PRIMARY KEY(md_id, chapter_id));
CREATE TABLE md_chapter(chapter_id TEXT PRIMARY KEY, md_id TEXT, chapter TEXT, volume TEXT,
  missing INTEGER NOT NULL DEFAULT 0,                      -- 1 = /chapter did not return it
  fetched_at TEXT NOT NULL);

-- AniList
CREATE TABLE al_media(media_id INTEGER PRIMARY KEY, id_mal INTEGER, type TEXT, format TEXT,
  status TEXT, chapters INTEGER, country TEXT, start_year INTEGER,
  romaji TEXT, english TEXT, native TEXT, synonyms TEXT NOT NULL, staff TEXT NOT NULL,
  cover_url TEXT, site_url TEXT, fetched_at TEXT NOT NULL);
CREATE INDEX al_media_id_mal ON al_media(id_mal);
CREATE TABLE al_entry(entry_id INTEGER PRIMARY KEY, media_id INTEGER NOT NULL UNIQUE,
  status TEXT NOT NULL, progress INTEGER NOT NULL, fetched_at TEXT NOT NULL);

-- Matching
CREATE TABLE mapping(md_id TEXT PRIMARY KEY, al_media_id INTEGER,
  state TEXT NOT NULL CHECK(state IN ('confirmed','auto','review','unmatched','not_on_anilist')),
  tier INTEGER NOT NULL,            -- 1 user, 2 links.al, 3 links.mal, 4 title
  confidence REAL, reasons TEXT NOT NULL, links_hash TEXT, updated_at TEXT NOT NULL);
CREATE TABLE match_candidate(md_id TEXT NOT NULL, al_media_id INTEGER NOT NULL,
  score REAL NOT NULL, reasons TEXT NOT NULL, rank INTEGER NOT NULL,
  PRIMARY KEY(md_id, al_media_id));

-- Sync runs
CREATE TABLE sync_run(run_id INTEGER PRIMARY KEY, started_at TEXT NOT NULL,
  state TEXT NOT NULL, phase_detail TEXT, error TEXT,
  est_requests INTEGER, est_seconds INTEGER, req_anilist INTEGER NOT NULL DEFAULT 0,
  req_mangadex INTEGER NOT NULL DEFAULT 0, approved_at TEXT, finished_at TEXT);
CREATE TABLE sync_item(run_id INTEGER NOT NULL REFERENCES sync_run(run_id), md_id TEXT NOT NULL,
  al_media_id INTEGER, al_entry_id INTEGER, al_progress INTEGER, md_progress INTEGER,
  al_status_before TEXT,           -- set by the pre-write re-read; verify compares against it
  set_status TEXT CHECK(set_status IS NULL OR set_status = 'COMPLETED'),
  status_source TEXT,              -- 'AniList' | 'MangaDex' (where the total came from)
  status_approved INTEGER NOT NULL DEFAULT 0,
  action TEXT NOT NULL CHECK(action IN ('write','skip','flag')),
  flag_kind TEXT CHECK(flag_kind IS NULL OR flag_kind IN ('exceeds_total','implausible')),
  reason TEXT, hint TEXT, unresolved_reads INTEGER NOT NULL DEFAULT 0,
  approved INTEGER NOT NULL DEFAULT 0,
  write_state TEXT NOT NULL DEFAULT 'none' CHECK(write_state IN ('none','pending','done','failed','dropped')),
  written_at TEXT, verify_note TEXT,
  PRIMARY KEY(run_id, md_id));
