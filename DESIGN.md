---
name: Shiori
description: A local, reviewed sync from MangaDex to AniList and MyAnimeList, with a reader's home around it.
colors:
  paper: "#f6f7f8"
  sheet: "#ffffff"
  sheet-shade: "#eef0f2"
  ink: "#1a1d21"
  ink-muted: "#5d6670"
  ink-faint: "#656d76"
  hairline: "#e1e4e8"
  hairline-strong: "#c9ced4"
  ribbon-teal: "#0e6b6b"
  ribbon-teal-deep: "#0a5656"
  ribbon-teal-wash: "#e3f1f0"
  on-ribbon: "#ffffff"
  ok: "#1b7a43"
  ok-wash: "#e5f3ea"
  err: "#b42318"
  err-wash: "#fbeae8"
  warn: "#8a5a00"
  warn-wash: "#fdf2dc"
  info: "#2a5f9e"
  info-wash: "#e7eef8"
  viz-blue: "#2a78d6"
  viz-orange: "#eb6834"
  viz-green: "#1baf7a"
  viz-amber: "#eda100"
  night-paper: "#111418"
  night-sheet: "#171b20"
  night-sheet-shade: "#1f242b"
  night-ink: "#e4e7eb"
  night-hairline: "#2a3038"
  night-ink-faint: "#8a939d"
  night-ribbon-teal: "#4fb8b4"
typography:
  display:
    fontFamily: "system-ui, -apple-system, Segoe UI, Roboto, Helvetica Neue, sans-serif"
    fontSize: "2rem"
    fontWeight: 650
    lineHeight: 1.2
  headline:
    fontFamily: "system-ui, -apple-system, Segoe UI, Roboto, Helvetica Neue, sans-serif"
    fontSize: "1.45rem"
    fontWeight: 650
    lineHeight: 1.25
    letterSpacing: "-0.01em"
  title:
    fontFamily: "system-ui, -apple-system, Segoe UI, Roboto, Helvetica Neue, sans-serif"
    fontSize: "1.02rem"
    fontWeight: 650
    lineHeight: 1.25
  body:
    fontFamily: "system-ui, -apple-system, Segoe UI, Roboto, Helvetica Neue, sans-serif"
    fontSize: "15px"
    fontWeight: 400
    lineHeight: 1.55
  label:
    fontFamily: "system-ui, -apple-system, Segoe UI, Roboto, Helvetica Neue, sans-serif"
    fontSize: "0.76rem"
    fontWeight: 600
    lineHeight: 1.3
  lead:
    fontFamily: "system-ui, -apple-system, Segoe UI, Roboto, Helvetica Neue, sans-serif"
    fontSize: "1.25rem"
    fontWeight: 400
    lineHeight: 1.45
  figure:
    fontFamily: "system-ui, -apple-system, Segoe UI, Roboto, Helvetica Neue, sans-serif"
    fontSize: "1.4rem"
    fontWeight: 650
    lineHeight: 1.2
    fontFeature: "tnum"
  mono:
    fontFamily: "ui-monospace, Cascadia Code, Consolas, monospace"
    fontSize: "0.86em"
  small:
    fontFamily: "system-ui, -apple-system, Segoe UI, Roboto, Helvetica Neue, sans-serif"
    fontSize: "0.85rem"
  aside:
    fontFamily: "system-ui, -apple-system, Segoe UI, Roboto, Helvetica Neue, sans-serif"
    fontSize: "0.9rem"
  axis:
    fontFamily: "system-ui, -apple-system, Segoe UI, Roboto, Helvetica Neue, sans-serif"
    fontSize: "0.72rem"
    fontFeature: "tnum"
  placeholder:
    fontFamily: "system-ui, -apple-system, Segoe UI, Roboto, Helvetica Neue, sans-serif"
    fontSize: "0.6rem"
  card-title:
    fontFamily: "system-ui, -apple-system, Segoe UI, Roboto, Helvetica Neue, sans-serif"
    fontSize: "1.15rem"
    fontWeight: 600
  prompt:
    fontFamily: "system-ui, -apple-system, Segoe UI, Roboto, Helvetica Neue, sans-serif"
    fontSize: "1.5rem"
    fontWeight: 650
  rank-prompt:
    fontFamily: "system-ui, -apple-system, Segoe UI, Roboto, Helvetica Neue, sans-serif"
    fontSize: "1.75rem"
    fontWeight: 650
    letterSpacing: "-0.01em"
  graph-label:
    fontFamily: "system-ui, -apple-system, Segoe UI, Roboto, Helvetica Neue, sans-serif"
    fontSize: "11px"
  graph-pill:
    fontFamily: "system-ui, -apple-system, Segoe UI, Roboto, Helvetica Neue, sans-serif"
    fontSize: "11.5px"
    fontWeight: 600
  graph-focus:
    fontFamily: "system-ui, -apple-system, Segoe UI, Roboto, Helvetica Neue, sans-serif"
    fontSize: "14px"
    fontWeight: 700
rounded:
  meter: "2px"
  tag: "3px"
  cover-sm: "4px"
  swatch: "0.45rem"
  base: "6px"
  cover: "8px"
  tier: "10px"
  composer: "12px"
  bubble: "14px"
  pill: "999px"
spacing:
  xs: "0.25rem"
  sm: "0.5rem"
  md: "0.75rem"
  lg: "1rem"
  xl: "1.5rem"
  page-max: "76rem"
components:
  button-primary:
    backgroundColor: "{colors.ribbon-teal}"
    textColor: "{colors.on-ribbon}"
    rounded: "{rounded.base}"
    padding: "0.42rem 0.85rem"
  button-primary-hover:
    backgroundColor: "{colors.ribbon-teal-deep}"
  button-secondary:
    backgroundColor: "{colors.sheet}"
    textColor: "{colors.ink}"
    rounded: "{rounded.base}"
    padding: "0.42rem 0.85rem"
  button-secondary-hover:
    backgroundColor: "{colors.sheet-shade}"
  button-danger:
    backgroundColor: "{colors.sheet}"
    textColor: "{colors.err}"
    rounded: "{rounded.base}"
  input:
    backgroundColor: "{colors.sheet}"
    textColor: "{colors.ink}"
    rounded: "{rounded.base}"
    padding: "0.38rem 0.55rem"
  badge:
    backgroundColor: "{colors.sheet-shade}"
    textColor: "{colors.ink-muted}"
    rounded: "{rounded.tag}"
    typography: "{typography.label}"
  panel:
    backgroundColor: "{colors.sheet}"
    rounded: "{rounded.base}"
    padding: "1rem 1.1rem"
  table-header:
    backgroundColor: "{colors.sheet-shade}"
    textColor: "{colors.ink-muted}"
    typography: "{typography.label}"
    padding: "0.5rem 0.7rem"
  chat-bubble-user:
    backgroundColor: "{colors.ribbon-teal-wash}"
    textColor: "{colors.ink}"
    rounded: "{rounded.bubble}"
    padding: "0.6rem 0.95rem"
  prompt-chip:
    backgroundColor: "{colors.sheet}"
    textColor: "{colors.ink}"
    rounded: "{rounded.pill}"
    padding: "0.45rem 0.9rem"
---

# Design System: Shiori

## Overview

**Creative North Star: "The Reader's Ledger"**

Shiori is a careful record book kept by one reader. Every page is a sheet of accounts: what was read, what
will change, what was written, what is worth reading next. Tables, figures and diffs are set plainly on cool
paper in dark ink, with hairlines doing the work that boxes and shadows do elsewhere. Color appears only
where it carries meaning: the one ribbon-teal accent for the action and the place you're at, and a small set
of status washes that say whether a row is safe, flagged or failed.

The ledger is quiet but warm. The working screens (Sync, Matches, Stats, Settings) are plain and exact,
because the user approves real writes from them. Where the app turns to reading for pleasure (cover grids,
series pages, the Ask chat, ranking), the same system is allowed softer corners, larger covers and a gentler
rhythm, so the covers carry the warmth instead of decoration. The ribbon bookmark in the header (the "shiori")
is the one emblem.

Density is moderate: a single reading column up to 76rem, sections separated by space and hairlines, with
Discover allowed the full window. Light and dark themes are equal citizens; dark redefines tokens only.

**Key Characteristics:**
- Cool paper-and-ink neutrals with a slight blue bias; one teal accent.
- Flat: depth comes from hairlines and a shaded sheet, never shadows.
- System font stack, compact sizes, tabular numbers for every figure.
- Covers are the imagery; nothing decorative is added around them.
- State is shown with small washes and badges, not large colored areas.

## Colors

Cool paper and dark ink, a single Ribbon Teal for action and place, and status washes kept small.

### Primary
- **Ribbon Teal** (`ribbon-teal`; `night-ribbon-teal` in dark): the primary button, the active tab and nav
  underline, focus rings, links, checkbox fills, the brand ribbon, and the "chosen" state of a verdict.
  **Ribbon Teal Deep** is its hover. **Ribbon Teal Wash** is the soft fill behind counts, genre tags, the
  user's chat bubble and focus halos.

### Neutral
- **Paper** (`paper` / `night-paper`): the page background behind everything.
- **Sheet** (`sheet` / `night-sheet`): tables, panels, cards, the header, inputs and secondary buttons.
- **Sheet Shade** (`sheet-shade` / `night-sheet-shade`): table header rows, row hover, badges, tag pills,
  cover placeholders, inline code.
- **Ink** (`ink` / `night-ink`): body text and titles. **Ink Muted** for secondary text, labels and inactive
  nav; **Ink Faint** (`ink-faint` / `night-ink-faint`) for counts, axis labels, placeholders and the least
  important metadata. Faint still reaches 4.5:1 on Paper, Sheet and Sheet Shade in both themes.
- **Hairline** (`hairline` / `night-hairline`): borders of tables, panels and figure strips, and row dividers.
  **Hairline Strong** outlines inputs, secondary buttons and chips.

### Status
- **Ok / Err / Warn / Info**, each with a matching wash: badges and notices for write, flagged, implausible,
  add and in-progress states; the tone of a figure that needs attention; the ranking tiers (liked, fine,
  disliked). Text sits in the status color on its own wash.

### Data
- **Viz Blue, Orange, Green, Amber** for series in charts and the network graphs (themes, creators, your
  series), plus a five-step blue heat ramp (`--heat-1`…`--heat-5`) for heat grids and tag tone. Validated
  for contrast on both themes; chart colors never stand in for status.
- On Stats and Year in review each single-series chart takes its own hue through a `hue-1`…`hue-4` class on
  its panel (the `--bar` property), so a page of charts reads as several things, not one blue wall. The hue
  marks which chart you're looking at, never a category inside it.

### Named Rules
**The One Ribbon Rule.** Ribbon Teal is the only accent. It marks the action to take and where you are, and
nothing else; never use it for decoration, large fills or headings.

**The Small Wash Rule.** Status color lives in badges, notices, row tints and thin borders. A whole panel or
page is never painted in a status color.

**The Tokens Only Rule.** Dark mode redefines the same custom properties; components never branch on theme.

**The Two Registers Rule.** The working screens (Sync, Matches, Settings, History) stay plain ledger. Stats,
Year in review, Discover and Map are the reader's own pages and may be livelier: a hue per chart, a headline
sentence, large covers, a cover shelf. Ribbon Teal stays the only accent in both.

## Typography

**Body Font:** system-ui (with -apple-system, Segoe UI, Roboto, Helvetica Neue, sans-serif)
**Mono Font:** ui-monospace (with Cascadia Code, Consolas, monospace), for codes, keys and env names.

**Character:** One native family at compact sizes, so the app reads like a well-kept tool on the user's own
machine. Hierarchy comes from weight (600–700) and size steps, not from a second typeface.

### Hierarchy
- **Display** (650, 2rem, 1.2): series page titles only; balanced wrapping.
- **Headline** (650, 1.45rem, 1.25, -0.01em): the page `h1`.
- **Title** (650, 1.02rem): section and panel headings; `h3` at 600, 0.95rem.
- **Body** (400, 15px, 1.55): everything else. Long text (descriptions, model reasons, chat answers) is held
  to 70–110ch.
- **Lead** (400, 1.25rem, 1.45): one headline sentence that sums up a stats page; balanced, at most 60ch.
- **Figure** (650, 1.4rem, tabular): the summary numbers that lead a page.
- **Label** (600, 0.76rem): table headers and badges.
- **Small** (0.85rem): the `.small` class, the theme toggle, link buttons and most secondary lines. Other
  small text runs 0.78–0.88rem.
- **Aside** (0.9rem): muted text beside a heading, breadcrumbs and button text.
- **Axis** (0.72rem, tabular): chart axis and scale labels, heat-map corners and nav counts.
- **Placeholder** (0.6rem): the title in a cover thumbnail that has no image.
- **Card Title** (600, 1.15rem): recommendation and rank card titles, ranking tiers and the all-clear line.
- **Prompt** (650, 1.5rem): the Ask empty-state heading, the score badge and series titles on phones.
- **Rank Prompt** (650, 1.75rem, -0.01em): the question at the top of a rank or compare step.
- **Graph text** (11px labels, 11.5px pills at 600, 14px focus pill at 700): text inside the network SVGs,
  set in px because the graph scales as one drawing.

### Named Rules
**The Tabular Figures Rule.** Every count, chapter number, score and date uses tabular numerals and doesn't
wrap.

**The No Shouting Rule.** No uppercase labels, no letter-spaced caps, no display sizes outside series titles,
the Ask empty state and the rank prompt.

## Layout

One centred reading column (`page-max`, 76rem) with 1rem side gutters and 1.5rem between sections; the page
head pairs the title and a short muted lede with actions on the right. Discover pages and their header use
the full window width with clamped gutters (1–2.5rem); their recommendation cards form a shelf of as many
40rem columns as fit, so wide screens fill instead of leaving half of each card empty. Sections are separated by space and hairlines rather
than wrapped in cards; panels are used only when a block is a separate object.

Grids use `auto-fit` / `auto-fill` with sensible minimums (two-up at 22rem, three-up at 17rem, review
cards at 34rem, review candidates at 19rem, search results at 22rem, cover digests at 8.5rem, best-of covers
at 11rem, the Year shelf at 4.75rem) so they fill the width at any size. A page never leaves content in the
left half of a wide column: widen it, put it in columns, or add a ruled row beside it. The series page and Rank
use a sticky left column (cover and your rating) beside the content. Ask is a one-screen chat: the page
never scrolls, the conversation does, above a pinned composer.

Below 48rem the header wraps with a scrolling nav row, side columns stack, covers shrink, and sub-navigation
scrolls on its own instead of widening the page; scrolling tab rows hide their scrollbar and fade at the
right edge so the hidden tabs read as "more this way". Figures strips go two to a row, an odd last figure
spanning both. Spacing steps are 0.25, 0.5, 0.75, 1 and 1.5rem.

## Elevation & Depth

The system is flat. Depth is conveyed by tone and line: Paper behind, Sheet for objects, Sheet Shade for
headers and hover, and hairline borders around anything that is its own object. There are no drop shadows.
The only box-shadows are functional rings: the composer's focus halo (3px Ribbon Teal Wash) and the outline
that marks the matching cell in a heat grid. Sticky elements (header, table headers, the diff's action bar,
side columns) stay flat and rely on their Sheet background and a border.

### Named Rules
**The Hairline Rule.** If something needs separating, use a 1px hairline or a shade step, never a shadow.

## Shapes

Corners are gently rounded and grow with the warmth of the surface: 2px for meter bars; 3px for tags, badges
and thumbnails; 4px for cover tiles and chart bars; 0.45rem for the network legend's focus swatch; 6px for buttons, inputs, panels, tables and cards; 8px for large covers; 10px for ranking tiers; 12px
for the chat composer; 14px for chat bubbles (with a 4px tail corner); full pills only for Ask's prompt
chips. Covers keep a 2:3 aspect ratio everywhere. Chart bars round only their outer end (4px) and are at most 32px thick (26px when paired). A bar taller than
the axis (a one-off import year, a huge first sync) is drawn to the top with a small break across it, so the
rest of the chart stays readable. Missing covers show the shiori ribbon, faint, centred in a Sheet Shade box.

## Components

### Buttons
Quiet tools; the main action is the only solid fill on a screen.
- **Shape:** gently rounded (6px), 0.42rem × 0.85rem, 600 weight at 0.9rem.
- **Primary:** Ribbon Teal fill and border, white text; hover darkens to Ribbon Teal Deep.
- **Secondary:** Sheet fill, Ink text, Hairline Strong border; hover to Sheet Shade.
- **Danger:** Sheet fill with Err text; hover to the Err wash. Destructive actions never use a red fill.
- **Link button:** text-only Ribbon Teal at 0.85rem for small in-row actions.
- **Disabled:** 45% opacity. **Focus:** 2px Ribbon Teal outline, 2px offset, on every control.
- **Choice buttons** (verdicts): outlined; the chosen one takes the Ribbon Teal Wash with a teal border,
  never a solid fill. Repeated per-row actions (Unlisted's Add) are secondary.
- **Pickers** (year, sort): the underline tab pattern, never filled pills.

### Badges, tags and chips
- **Badges:** 3px radius, 0.78rem at 600, Sheet Shade by default; status badges use the status color on its
  wash (write/done = ok, flagged/failed = err, implausible/pending = warn, add/in progress = info).
- **Tag pills:** Sheet Shade with muted text; genres get the Ribbon Teal Wash.
- **Site tag:** outlined (Hairline Strong) label naming AniList or MyAnimeList.
- **Filter chips:** outlined, turning Err on hover because clicking removes the filter.

### Panels, figures and tables
- **Panel / card:** Sheet, 1px Hairline, 6px radius, about 1rem padding; a finished review card turns to the
  ok wash.
- **Notices:** a status wash with a full 1px border in the status color at low strength. Never a stripe down
  one side.
- **Figures strip:** one bordered row of summary numbers divided by hairlines, not a card per number.
- **Data tables:** wrapped in a bordered Sheet; sticky Sheet Shade header in label type; 0.5rem × 0.7rem
  cells; hairline row dividers; soft hover tint. Series cells pair a 38×54 cover with the MangaDex title and
  the matched title beneath in muted type.

### Inputs / Fields
- **Style:** Sheet fill, Hairline Strong border, 6px radius; checkboxes are filled with Ribbon Teal.
- **Focus:** the global 2px Ribbon Teal outline; the Ask composer instead shifts its border to Ribbon Teal
  with a 3px wash halo.

### Navigation
- **Header:** sticky Sheet bar with a hairline below; the ribbon mark and "Shiori" at 700; section links in
  muted 500 weight with a 2px Ribbon Teal underline for the current page and a Ribbon Teal Wash count.
- **Sub-navigation and tabs:** the same underline pattern on a hairline; counts in Ink Faint.

### Diff action bar
The sticky bar at the bottom of a sync's Changes: a Sheet strip with a stronger border, holding the request
estimate and the write button, so approval is always in reach while scrolling the rows.

### Recommendation rows
One series per row: a large cover (132×188, 160×228 on very wide screens) beside the title, meta line in
tabular figures, tag pills, the reason and a folded description, with links at the foot. Inside Ask answers
the same row is compact with an 84×120 cover.

### Ask chat
The user's message is a Ribbon Teal Wash bubble aligned right (14px radius, 4px tail); answers are unboxed
text at 1.02rem with recommendation rows beneath. Thinking is three small Ribbon Teal dots that pulse
(static under reduced motion).

### Ranking tiers
Three large choices side by side across the column beside a 19rem cover (10px radius, 1.15rem, 600, label at
the foot) whose border takes the tier's status color on hover and when suggested, with a keyboard hint at the
top right.

### Year shelf
Year in review opens with every cover finished that year, packed tight (4.75rem columns, 0.4rem gaps) like
spines on a shelf, before any numbers; "Your best this year" follows as 11rem covers.

## Do's and Don'ts

### Do:
- **Do** keep Ribbon Teal for the primary action, the current place and focus; one solid button per screen
  area.
- **Do** separate sections with space and hairlines; use a bordered Sheet only for real objects (tables,
  charts, cards).
- **Do** use tabular numerals for every number, and show status with a badge or wash.
- **Do** let covers be the imagery, at 2:3 with small rounded corners and a Sheet Shade placeholder.
- **Do** make grids and charts fill the width (`auto-fit` columns, equal-width chart slots).
- **Do** define every color as a token on `:root` and redefine it for dark mode; respect reduced motion.

### Don't:
- **Don't** add drop shadows, gradients or glows; the system is flat.
- **Don't** introduce a second accent, a second typeface, or uppercase letter-spaced labels.
- **Don't** use filled pills for pickers, or a colored stripe down one side of a box.
- **Don't** leave a wide column's right half empty.
- **Don't** fill panels, headers or buttons with a status color; a destructive button stays outlined.
- **Don't** wrap every number in its own card; use the figures strip.
- **Don't** add decorative illustration, icons for their own sake, or ornament around covers.
