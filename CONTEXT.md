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
