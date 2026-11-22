-- A Change's list entry, progress and earlier status are the site's own, on AniList and MyAnimeList runs alike.
ALTER TABLE sync_item RENAME COLUMN al_entry_id TO site_entry_id;
ALTER TABLE sync_item RENAME COLUMN al_progress TO site_progress;
ALTER TABLE sync_item RENAME COLUMN al_status_before TO site_status_before;
