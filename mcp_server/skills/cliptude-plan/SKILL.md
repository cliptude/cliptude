---
name: cliptude-plan
description: Plan every visual of a Cliptude video yourself — the look (theme, colours, style, music), every motion graphic and its text and timing, the search query, person or URL behind every shot, the transition on every cut, two-image reveals and every sound effect — and have Cliptude only voice, source and render it. Use for Faceless Documentary, Faceless Top 10 and Avatar Video templates when the user wants the agent to direct the video (create_plan_job / cliptude plan-job), or asks to review or edit the plan before production.
---

# Agent-planned videos

You direct; Cliptude executes. Cliptude writes the narration (or voices your script, or aligns your audio), gives you
every sentence with word timings, renders **exactly the plan you lock**, sources the media you asked for, and tells
you afterwards what it used and where it had to fall back. Its own planner does not run: it adds, moves and drops
nothing.

Formats: `faceless_documentary`, `faceless_top_10`, `avatar_video`. Not available in the desktop app yet.

## Flow

1. **Template.** `list_templates`; or `get_template_options` + `list_voices` + `create_template`. The template fixes the
   **voice**, language, aspect ratio and duration bucket. (Theme, colours, style and music can be set in the plan.)
2. **Start.** `create_plan_job` / `cliptude plan-job` with exactly one of: `script` (your narration, voiced as is),
   `prompt` (a brief; Cliptude writes the narration — never the bare title), or `voiceover_path` (audio you already
   have). Needs the full website price in credits; charges only the narration now.
3. **Wait.** Poll `get_job` until `status` is `plan_ready` (`narration_url` is set). Plans expire after 14 days.
4. **Read.** `get_narration`: `sentences[]` (`id`, `start`, `end`, `text`, `words[]`), `narration_digest`,
   `suggested_chapters`, `template` (theme, switched-off graphics), `pricing`.
5. **Choose graphics.** `get_kind_catalog(format, compact=true)` for one line per graphic, then
   `get_kind_catalog(types=[...])` for the full entries you will use: description (when to use / not for),
   `prop_schema` (required props), `media_slots`, durations, cap, and a preview video URL.
6. **Write the plan** (schema below), then `check_plan` and fix every error until `valid` is true. Checks are free,
   use no LLM, 60/minute. Read the warnings and the scorecard; they are advice, not blockers.
7. **Lock.** `lock_plan` stores it and applies its `look`. You can lock again to replace it until you continue,
   and again after production to revise it (step 10).
8. **Produce.** `continue_job`, then poll `get_job` until `status` is `ready_for_edit`. Cliptude has voiced,
   sourced and built the video; nothing is rendered yet. The job is charged here, once.
9. **Review the build, not a render.** `get_plan_review` returns every picture or clip in order (beat, time, the
   sentence under it, what you asked for, what was used, a link to the file) and every graphic (time on screen and
   the props it will be drawn with, or why it is missing). `get_plan_report` adds every media request's fallback.
   Look at the media links: a search can return a wrong picture that still counts as found. `used.source` is one of
   `your URL`, `stock footage`, `stock photo`, `web video`, `web image`, `AI still`, `reused` (a file another beat
   also shows) or `text card` (a last-resort plate); both endpoints list them in `source_labels`.
10. **Revise on the same job** if anything is wrong: `check_plan` and `lock_plan` a revised plan (same narration,
   `get_narration` still works), then `continue_job` again. Cliptude rebuilds from the new plan, reuses every
   picture or clip whose request did not change, and charges nothing more. Repeat 9–10 as needed.
11. **Render once.** `open_editor_project` for the job, then `render_project` for the final MP4.

CLI: `cliptude plan-job`, `narration`, `catalog --compact`, `catalog --types a,b`, `plan-check <job> plan.json`,
`plan-lock <job> plan.json`, `continue <job>`, `plan-get`, `plan-review`, `plan-report`, `editor <job>`,
`render <project>`.

## Pricing

The total for the job equals the website price for the same video. The narration (script + TTS at cost, or
`max(2, minutes)` credits to align an uploaded voiceover) is charged when it is ready and is kept if you abandon the
job. The rest is charged when the video completes. `check_plan` returns `cost.due_at_continue`. Avatar Video prices
the presenter seconds you plan.

## Plan schema (`cliptude.plan/v1`)

```json
{
  "version": "cliptude.plan/v1",
  "narration_digest": "sha1:… (copy from get_narration)",
  "look": {
    "visual_theme": "history",
    "faceless_documentary_style": "investigative",
    "faceless_documentary_primary": "#F5F0E6",
    "faceless_documentary_secondary": "#C81828",
    "background_music_mode": "library",
    "background_music_library_file": "<a filename from get_template_options>",
    "background_music_volume": 0.3,
    "enable_sound_effects": true,
    "enable_transitions": true,
    "excluded_transition_kinds": []
  },
  "sense": {"topic": "TSMC and Taiwan", "must_include": ["TSMC"], "must_avoid": ["TSMC stock ticker"],
            "people": ["Morris Chang"], "places": ["Hsinchu"]},
  "chapters": [{"title": "The bet", "from_sentence": 0, "to_sentence": 21}],
  "beats": [
    {"id": "b0", "sentence_id": 0, "media": {"query": "Hsinchu Science Park aerial", "alts": ["TSMC fab exterior"],
     "negatives": ["stock chart"], "prefer": "video", "query_kind": "place",
     "intent": "Aerial view of Hsinchu Science Park with TSMC fab buildings, Taiwan, 1990s", "era": "1990s"},
     "camera": "zoom_in", "treatment": "grain"},
    {"id": "b1", "sentence_id": 3, "phrase": "Morris Chang", "media": {"person": "Morris Chang", "prefer": "image"},
     "transition_in": "film-burn", "transition_sound": "el_join_film_burn",
     "reveal_next": "crash_zoom", "reveal_sound": "cue_deep_impact"},
    {"id": "b2", "sentence_id": 5, "media": {"url": "https://example.org/fab.jpg", "fallback_query": "TSMC cleanroom"}},
    {"id": "b3", "sentence_id": 9, "media": {"auto": true}, "transition_in": "none"}
  ],
  "graphics": [
    {"id": "g1", "sentence_id": 0, "phrase": "1987,", "kind": "giant_centered_year", "props": {"text": "1987"}},
    {"id": "g2", "sentence_id": 3, "phrase": "Morris Chang", "animation_type": "person_cutout_headline", "dur": 5.6,
     "props": {"personName": "Morris Chang", "personTitle": "Founder, TSMC", "accentColor": "#C81828"},
     "media": [{"slot": "personImageQuery", "person": "Morris Chang"}], "sound": "el_gap_cutout_rise", "volume": 40,
     "intent": "introduce the founder"},
    {"id": "g3", "sentence_id": 12, "kind": "checklist_sticky_card_right",
     "props": {"text": "Why nobody copies it", "listItems": ["EUV machines", "Process know-how", "Customer trust"]}}
  ],
  "beat_fx": [{"sentence_id": 30, "phrase": "collapsed", "kind": "vignette", "opacity": 0.5}],
  "sfx": [{"id": "s1", "sentence_id": 30, "phrase": "collapsed", "sound": "cue_low_boom", "volume": 55, "offset_s": -0.1}],
  "ranked_items": [{"rank": 10, "label": "Intel", "sentence_id": 2, "dur": 4.0}],
  "presenter_windows": [{"from_sentence": 0, "to_sentence": 2, "layout": "full_screen"},
                        {"from_sentence": 40, "to_sentence": 41, "layout": "split_left",
                         "collage_media": [{"query": "TSMC Arizona fab"}]}]
}
```

### Timing
- Every item names a `sentence_id`. Add `phrase` (words from that sentence, punctuation and case ignored; Chinese,
  Japanese and Thai match on characters) to start exactly on that word; without it the item starts with the sentence.
- `dur` is seconds (default: the catalog's typical hold). Minimum 1 s. Cliptude only snaps to spoken words.
- **Beats** tile the narration: each beat lasts until the next one starts; the first starts at 0. Every beat ≥ 1 s,
  in narration order.

### Graphics
- Give exactly one of `kind` (a planner kind or an overlay style; Cliptude picks the concrete type) or
  `animation_type` (full control; use `"animation_type": "b_roll_text_overlay", "style": "<overlay style>"` for an
  overlay text style).
- **Layout** comes from the type: `board` replaces the picture (full frame; two boards may not overlap by 0.3 s or
  more), `overlay` sits on the footage. An overlay cannot sit on a board: a board hides the footage, so `check_plan`
  refuses an overlay that overlaps one.
- **People:** a person board needs a real portrait that passes Cliptude's visual check (a face, the right person).
  Cliptude never invents a face for a real person, so a board whose portrait is not found is dropped (the report
  gives the reason). For anyone less famous, give the slot a `url` to a portrait you checked, with a `fallback_query`.
- `props`: all on-screen text and data, per the type's `prop_schema` (write it in the narration language). Overlay
  styles take `text` (+ `secondaryText`, `listItems`, `series`/`chartKind` for the holographic chart).
- Colours per graphic: hex `accentColor`, `textColor`, `backgroundColor`, `bgColor`, `secondaryColor`,
  `highlightColor`, `primaryColor` override the template palette for that graphic only.
- `sound`: a sound id from the catalog's `sounds`, `"none"` (silent) or `"auto"` (Cliptude's sound, the default).
  `volume`: 0–100 for it.
- `media`: one entry per picture slot from the catalog's `media_slots`:
  `{"slot": "<slot>", "query": "…"}`, `{"slot": …, "person": "Full Name"}` (portrait search), or
  `{"slot": …, "url": "https://…", "fallback_query": "…"}`. A board with no picture found gets an AI still and
  the report says so. A media item may also carry `intent` (one sentence describing the picture) and `era`, as on
  beats.

### Plates under boards
- Types with `plate_capable: true` in the catalog (charts, stat cards, lists, timelines, article and social cards)
  read well as an inset card: the board shrinks to ~80% with a shadow over a dimmed, slowly pushing-in picture, so
  the viewer keeps seeing real footage or a real document. By default that picture is the beat's own footage under
  the board.
- `plate` on such a graphic picks the picture instead (exactly one of):
  `{"beat": true}` (the beat's footage, the default made explicit),
  `{"query": "TSMC 2023 annual report revenue page", "kind": "document"}` (a real primary document: report, filing,
  law, official page; `"kind": "photo"`, the default, searches for a photograph),
  `{"url": "https://…"}` (a picture or clip you checked).
  A plate that cannot be found falls back to the beat's footage (a document query first tries a photo search); the
  report's `slot: "plate"` entry says what was used. `check_plan` refuses `plate` on a type that is not
  plate-capable, or with an unknown field, more than one source, a `kind` other than document/photo, or a bad URL.
- When more than ~40% of graphics are boards, `check_plan` warns (`board_share_high`) and names the boards that can
  sit over a picture; plate them or turn others into overlays.

### Premium boards
- Catalog entries with `premium: true` (about 190, mostly full-frame boards: maps, data charts, comparisons, lists,
  timelines, document and article boards) are the newest designs, ported from a premium motion library.
  `get_kind_catalog(premium=true)` / `cliptude catalog --premium` lists only them. Place one with
  `"animation_type": "<type>"`; check its `prop_schema` and `media_slots` like any other type.
- Each premium board carries its own timed sound cues (several hits matched to its motion). Leave its `sound` unset
  (or `"auto"`) to keep them. Setting `sound` replaces them with your one sound, `"none"` silences it, and listing any
  `sfx` mutes them along with every other sound the plan does not name.
- A premium board's `prop_schema` lists the fields its component actually draws (e.g. `multi_pie_chart`:
  `pie1Value`/`pie1Label`…, `comparison_bar_chart`: `titleText`, `firstBarLabel`, `firstBarValue`, `firstBarText`…,
  `five_step_timeline`: `firstStepText`…`fifthStepText`). Fill those data fields from the narration, not just a
  `headline`: most premium boards never draw `headline`, and any required field left empty renders the component's
  sample data. `check_plan` refuses a premium board missing a required field (`missing_prop`, with the real fields in
  the hint). Each field's `examples` shows the sample it replaces; ranges and `enum`s are in the schema.
- Pictures are never props: ask for them in `media_slots` (step images, portraits, cut-outs fill in order).

### Beat media
One of: `query` (+ `alts`, `negatives`, `query_kind` such as object, place or person, `prefer`: video|image), `person`,
`url` (+ `fallback_query`; public http(s) images ≤15 MB or videos ≤300 MB), or `auto: true` (Cliptude writes the
query). Your query is searched verbatim first, then your `alts`; if nothing usable is found Cliptude uses its normal
fallbacks (an AI still or a plate), and the report shows it. The same `url` may sit on several beats: each one
plays that file.

Give every beat an `intent` too (optional, recommended; up to 300 characters): **one concrete sentence describing the
picture you want** — the subject, what is visible, the era and the place, plus the medium when it matters (news
footage, archival photo, document). Add `era` (`"1999"`, `"1980s"`, `"present-day"`) when the narration is dated.
Why: Cliptude's media library is searched by meaning, not by keywords, so a sentence that says what a camera saw
finds the right picture, and `era` keeps a 1999 line from getting a 2020 photo. Keywords still go in `query`/`alts`:
the intent is used beside them and never changes them.
- Good: `"Taiwan Chi-Chi earthquake 1999 utility crews restoring power at night, news footage"`, `"Morris Chang
  speaking at a TSMC press conference, Hsinchu, late 1980s, archival photo"`, `"Rows of ASML EUV lithography machines
  inside a TSMC fab cleanroom, present-day"`.
- Bad: `"the earthquake showed how fragile the supply chain is"` (an argument, not a picture), `"TSMC"` (a keyword),
  `"cinematic, 4k, dramatic lighting"` (style words; nothing to find).
- `auto: true` beats take an `intent` too: Cliptude writes the query and searches the library with your sentence.
`camera`: `zoom_in, zoom_out, zoom_in_left, zoom_in_right, pan_left, pan_right, tilt_left, tilt_right, slant_left,
slant_right, inset_wiggle`, or a look such as `bokeh_overlay`, `retro_film_overlay`, `smoke_fog_overlay` (an invalid
value's error hint lists them all).
`treatment`: `kenBurns, grain, flicker8mm, grayscaleIn, paper, dust, lightLeak, godRays, smoke, lensFlare,
scanLine, cctv, colorChange, footageReveal`, or `"none"`.
- A beat gets a treatment **only when it sets `treatment`**; Cliptude adds none (no grain, smoke, light leak or
  colour change of its own). The theme's colour grade still applies to every picture.
- `camera` unset: a beat that plays your `url` holds still (a finished clip, capture or screenshot is shown as
  sent); any other beat gets a plain slow move (zoom or pan, never an overlay or frame). `"camera": "none"` holds
  any beat still.

### Cuts and reveals
- `transition_in` on a beat is the join on the cut into it: a `slug` from the catalog's `transitions` (e.g. `fade`,
  `film-burn`, `glitch`, `zoom`, `whip-pan`, `wipe-left`, `iris`, `paper-tear`), `"none"` for a hard cut, or `"auto"`
  (the default: Cliptude decides, at about one join per 10–15 s). Not on the first beat. `transition_sound` (a sound
  id or `"none"`) replaces the join's own sound; it needs a `transition_in` slug.
- `reveal_next` on a beat joins it and the next beat into one moving A→B shot, styled by a `style` from the catalog's
  `reveal_styles` (`whip_pan`, `crash_zoom`, `split_wipe`, `modern_film_burn`, …; each has a preview video). Both beats
  must end up as stills, so give both `media.prefer: "image"`; a pair that sources a clip is skipped and the report
  says so. The next beat cannot also have a `transition_in` or its own `reveal_next`. `reveal_sound` replaces the
  reveal's sound. In a plan, Cliptude adds **no reveals of its own**.
- `look.enable_transitions: false` or a template that excludes a transition makes those slugs errors.

### Sound effects
- Ids come from the catalog's `sounds`: about 285 entries, each with a `category` (transition, whoosh, graphic, ui,
  click, marker, typing, shutter, impact, boom, glitch, ambient), a `description`, a `duration_s` and a `preview` link
  to listen to (the compact catalog keeps only id, category and description).
- Per graphic `sound`/`volume`, per cut `transition_sound`, per reveal `reveal_sound` (see above), and free hits in
  `sfx`: `{sound, sentence_id, phrase?, offset_s? (-2 to 5 s from the phrase), volume? (0–100, default 50)}`.
- **When `sfx` lists anything, only the sounds the plan names play.** Graphics, cuts and reveals without a sound of
  their own are silent, and Cliptude adds no cue hits. Leave `sfx` out to keep Cliptude's sound design and override it
  item by item.
- `look.enable_sound_effects: false` turns every effect off; any sound other than `"none"` is then an error.
  Music is separate (`look.background_music_*`).

### Look
Same names and values as `create_template` (see `get_template_options`): themes `standard, crime, history, modern,
minimalist, sports`; styles `default, news, yellow, maroon, investigative, dramatic`; primary = headline text colour
over footage (keep it light), secondary = accent. The **voice cannot change** here (the narration is recorded).

### Format-specific
- `ranked_items` (Top 10 only): one title per entry, unique ranks, titles may not overlap each other or a board.
- `presenter_windows` (Avatar Video only): `full_screen`, `split_left` or `split_right`; no overlaps; total seconds
  within the template's cap (intro-only templates: one opening window). Full-screen windows may not cover graphics.
- A-roll is off for agent plans.

## What `check_plan` rejects
Unknown version, stale `narration_digest` (409: fetch the narration again), unknown kind/type/style, graphics switched
off or excluded by the template, graphics the theme does not use, unknown sentence or a phrase not in its sentence
(the hint shows the sentence), missing or invalid props (including chart data the renderer needs), a calendar year
on a counter (use `giant_centered_year`), an overlay on a board, a `transition_in` on a beat whose start a board
covers (there is no cut to see), bad media (unknown slot, not exactly one source, private or non-http URL),
overlapping boards, bad beats, `radial_wash` on history/minimalist/sports, unknown camera/treatment, invalid `look`
values or colours, ranked/presenter items on the wrong format or overlapping, unknown transition slugs, reveal styles
or sound ids, a transition on the first beat or inside a reveal, a reveal chained onto a reveal, sounds while sound
effects are off, and transitions while transitions are off or excluded.

## Scorecard targets (from reference videos; advisory)
Graphics/min ≈ 4.9 (3.6–6.7); ~46% of runtime covered; ≥80% of graphics starting on a sentence start; first graphic
≤ 6 s; ≥ 9 visuals in the first minute; longest gap ≲ 30 s; no kind more than twice in a row; ≥ 16 distinct kinds per
10 minutes; ~40% boards; every spoken date, ~50% of years and ~60% of money figures on screen. Warnings also flag
numbers on cards that are not spoken nearby and sample text left in props.
Pictures: about one beat per sentence, each on screen 4–6 s on average (`avg_picture_s`); `slow_pictures` warns
above 6 s on average or for any picture held over 12 s, naming the beats to split. `few_graphics` warns below 3.6
graphics a minute, `few_boards` when boards are under 15% of graphics (videos over 3 minutes).

## What Cliptude still decides
Joins on cuts you leave at `"auto"`, the direction and timing details inside a reveal (a locked plan gets no
collages or reveals you did not ask for), where a searched picture or clip comes from (you choose video vs still), the
plain camera move on a searched beat without a `camera`, the
voice, caption styling, and fallbacks when your media cannot be found. The report (`get_plan_report`) lists every
graphic, transition, reveal and free sound effect you planned against what was rendered, with the reason for anything
dropped.
