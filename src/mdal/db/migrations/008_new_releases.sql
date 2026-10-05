-- New releases: recently updated MangaDex series you haven't read, scored against your list by tags,
-- creators and descriptions; and the Ask chat.
ALTER TABLE al_media ADD COLUMN description TEXT;   -- plain text from the list request; NULL = not fetched
CREATE TABLE md_new(md_id TEXT PRIMARY KEY,
  title TEXT NOT NULL, alt_titles TEXT NOT NULL,     -- JSON list of strings
  description TEXT,                                  -- plain text, English when there is one
  tags TEXT NOT NULL,                                -- JSON [{name, group}]
  authors TEXT NOT NULL,                             -- JSON list of names
  original_language TEXT, year INTEGER, pub_status TEXT, demographic TEXT, content_rating TEXT,
  al_id INTEGER, mal_id INTEGER, cover_file TEXT, last_chapter TEXT,
  source TEXT NOT NULL,                              -- 'updated' (latest chapters) | 'added' (new series)
  similarity REAL,                                   -- description match with your list; NULL = not computed
  similar_to TEXT,                                   -- JSON [title] of the closest series on your list
  fetched_at TEXT NOT NULL);
CREATE TABLE md_new_hidden(md_id TEXT PRIMARY KEY, hidden_at TEXT NOT NULL);
CREATE TABLE md_follow(md_id TEXT PRIMARY KEY);     -- your MangaDex follows, replaced each scan
-- Embedding vectors (float32 bytes) from the local model, reused while the text is unchanged.
CREATE TABLE embedding(ref TEXT NOT NULL, model TEXT NOT NULL, text_hash TEXT NOT NULL, vector BLOB NOT NULL,
  PRIMARY KEY(ref, model));
CREATE TABLE chat_message(id INTEGER PRIMARY KEY,
  role TEXT NOT NULL CHECK(role IN ('user','assistant')),
  content TEXT NOT NULL,
  picks TEXT,                                        -- JSON [{key, reason}] for assistant messages
  created_at TEXT NOT NULL);
