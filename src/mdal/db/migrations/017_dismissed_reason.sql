-- A dismissed Change is a skip that still has its flag_kind. Its reason goes back to the Flag's own words;
-- "dismissed by you" is how the changes page shows it.
UPDATE sync_item SET reason = substr(reason, length('dismissed by you: ') + 1)
WHERE action = 'skip' AND flag_kind IS NOT NULL AND reason LIKE 'dismissed by you: %';
