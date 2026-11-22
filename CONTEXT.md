# Shiori

Shiori keeps your MangaDex reading in step with your lists on AniList and MyAnimeList, and helps you score, rank and find series.

## Language

### Lists and sites

**Site**:
A list service Shiori writes to: AniList or MyAnimeList. MangaDex is the reading source, not a site.
_Avoid_: target (except for what a Sync covers), service, tracker

**Main site**:
AniList. A series is on **your list** when it has an AniList entry; MyAnimeList follows AniList.
_Avoid_: primary, source of truth

**Your list**:
Your entries across the sites, treated as one list headed by the main site.
_Avoid_: library (that is MangaDex), collection

**Entry**:
One series on one site's list, with its status, progress, score and notes.
_Avoid_: list item, row

**Score**:
Your rating of a series, out of 10 with one decimal. Each site stores it on its own scale; that is the site's concern, not yours.
_Avoid_: points, rating (a rating is MangaDex's 1–10 vote)

**List edit**:
A change you make to your list outside a Sync: an add, a status, a score or notes.
_Avoid_: write (that is a Sync write), update

**Mirror**:
Repeating a list edit on MyAnimeList after AniList took it. Whether to mirror is one setting.
_Avoid_: fan-out, copy, sync (for list edits)

**MyAnimeList link**:
Which MyAnimeList entry a series is: AniList's link, else MangaDex's; when the two disagree there is none.
_Avoid_: mal id (in prose), mal target

### Sync

**Sync**:
Reading MangaDex, comparing with each site Sync covers, and writing the changes you approve.
_Avoid_: run (a run is one Sync's record for one site)

**Busy**:
A Sync is in progress for any site, including the gap between finishing one site and starting the next. No list edit is made while Shiori is busy.
_Avoid_: locked, running (for the two-site gap)

**Change**:
One series' proposed update for one site in a Sync.
_Avoid_: diff row, sync item, run item

**Flag**:
A Change held back for you to look at, because it looks wrong or can't be written.
_Avoid_: warning, error

**Dismissal**:
Your decision to set one kind of Flag aside on one site, until your MangaDex progress for that series changes.
_Avoid_: ignore, hide

**Selection**:
The Changes, and the parts of them, you ticked for approval.
_Avoid_: choices, ticks

**Write plan**:
What a Selection will actually send to a site. The estimate is the cost of the Write plan.
_Avoid_: approved items, payload

**Run**:
One Sync's record for one site.
_Avoid_: job, sync (for the record)

**Phase**:
The read or the write part of a Run.
_Avoid_: step, stage

**Resume**:
Continuing the unfinished writes of the Runs that stopped partway.
_Avoid_: retry, restart

**Superseded**:
A Run closed because a newer one for the same site replaced it.
_Avoid_: stale, overwritten
