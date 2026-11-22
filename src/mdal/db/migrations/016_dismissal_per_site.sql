-- A Dismissal covers one site and one kind of Flag. Older ones don't say which site they were made on and already
-- applied to both, so each is kept for both.
CREATE TABLE dismissed_flag_new(md_id TEXT NOT NULL, site TEXT NOT NULL CHECK(site IN ('anilist','mal')),
  md_progress INTEGER NOT NULL, flag_kind TEXT NOT NULL, reason TEXT, dismissed_at TEXT NOT NULL,
  PRIMARY KEY(md_id, site));
INSERT INTO dismissed_flag_new
  SELECT d.md_id, s.site, d.md_progress, d.flag_kind, d.reason, d.dismissed_at
  FROM dismissed_flag d CROSS JOIN (SELECT 'anilist' AS site UNION ALL SELECT 'mal') s;
DROP TABLE dismissed_flag;
ALTER TABLE dismissed_flag_new RENAME TO dismissed_flag;
