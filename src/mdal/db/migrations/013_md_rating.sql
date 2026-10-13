-- Your MangaDex rating fills in a list score the sites don't have yet.
ALTER TABLE md_manga ADD COLUMN rating INTEGER;                 -- yours on MangaDex, 1-10; NULL = unrated
ALTER TABLE sync_item ADD COLUMN set_score INTEGER;             -- the MangaDex rating to send (1-10); NULL = none
ALTER TABLE sync_item ADD COLUMN score_approved INTEGER NOT NULL DEFAULT 0;
