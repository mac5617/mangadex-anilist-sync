-- Allow action 'add': a matched series not on the AniList list, created by the writer after approval.
-- SQLite cannot alter a CHECK constraint, so the table is rebuilt with identical columns.
CREATE TABLE sync_item_new(run_id INTEGER NOT NULL REFERENCES sync_run(run_id), md_id TEXT NOT NULL,
  al_media_id INTEGER, al_entry_id INTEGER, al_progress INTEGER, md_progress INTEGER,
  al_status_before TEXT,
  set_status TEXT CHECK(set_status IS NULL OR set_status = 'COMPLETED'),
  status_source TEXT,
  status_approved INTEGER NOT NULL DEFAULT 0,
  action TEXT NOT NULL CHECK(action IN ('write','add','skip','flag')),
  flag_kind TEXT CHECK(flag_kind IS NULL OR flag_kind IN ('exceeds_total','implausible')),
  reason TEXT, hint TEXT, unresolved_reads INTEGER NOT NULL DEFAULT 0,
  approved INTEGER NOT NULL DEFAULT 0,
  write_state TEXT NOT NULL DEFAULT 'none' CHECK(write_state IN ('none','pending','done','failed','dropped')),
  written_at TEXT, verify_note TEXT,
  PRIMARY KEY(run_id, md_id));
INSERT INTO sync_item_new SELECT run_id, md_id, al_media_id, al_entry_id, al_progress, md_progress, al_status_before,
  set_status, status_source, status_approved, action, flag_kind, reason, hint, unresolved_reads, approved,
  write_state, written_at, verify_note FROM sync_item;
DROP TABLE sync_item;
ALTER TABLE sync_item_new RENAME TO sync_item;
