-- The chapter total the rules used for a Change, saved when it is built so the row shows what its decision was
-- based on. Completions already stored (a discarded run can be restored) get the total they were shown with.
ALTER TABLE sync_item ADD COLUMN total INTEGER;
UPDATE sync_item SET total = COALESCE(
  (SELECT CASE WHEN r.target='mal' THEN COALESCE(me.chapters, CASE WHEN me.mal_id IS NULL THEN a.chapters END)
               ELSE a.chapters END
   FROM sync_run r
   LEFT JOIN al_media a ON a.media_id = sync_item.al_media_id
   LEFT JOIN mal_entry me ON me.mal_id = sync_item.mal_id
   WHERE r.run_id = sync_item.run_id),
  (SELECT CAST(m.last_chapter AS INTEGER) FROM md_manga m
   WHERE m.md_id = sync_item.md_id AND m.pub_status = 'completed'
     AND m.last_chapter GLOB '[0-9]*' AND m.last_chapter NOT GLOB '*[^0-9.]*'))
WHERE set_status = 'COMPLETED';
