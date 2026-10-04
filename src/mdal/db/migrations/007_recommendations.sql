-- Recommendations. Candidates come from AniList (community recommendations of your favourites, and top
-- series for your strongest tags, genres and creators); scores are computed when a page is shown.
ALTER TABLE al_media ADD COLUMN mean_score INTEGER;   -- AniList average score, 0-100
ALTER TABLE al_media ADD COLUMN popularity INTEGER;   -- number of users with it on their list
ALTER TABLE al_media ADD COLUMN is_adult INTEGER;
CREATE TABLE rec_candidate(media_id INTEGER PRIMARY KEY,
  sources TEXT NOT NULL,                              -- JSON [{kind, via, label, rating}]
  fetched_at TEXT NOT NULL);
CREATE TABLE rec_hidden(media_id INTEGER PRIMARY KEY, hidden_at TEXT NOT NULL);
