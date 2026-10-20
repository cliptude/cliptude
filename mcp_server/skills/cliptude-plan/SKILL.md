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
   The template's `generation_model` decides whether footage can move: Cinder (`high`) sources still pictures only, so `prefer: "video"` becomes a still there; Caldera (`pro`) sources video clips. `check_plan` warns `video_needs_caldera`.
2. **Start.** `create_plan_job` / `cliptude plan-job` with exactly one of: `script` (your narration, voiced as is),
   `prompt` (a brief; Cliptude writes the narration — never the bare title), or `voiceover_path` (audio you already
   have). Needs the full website price in credits; charges only the narration now.
3. **Wait.** Poll `get_job` until `status` is `plan_ready` (`narration_url` is set). Plans expire after 14 days.
4. **Read.** `get_narration`: `sentences[]` (`id`, `start`, `end`, `text`, `words[]`), `narration_digest`,
   `suggested_chapters`, `template` (theme, switched-off graphics, and `look`: the template's style pack,
   colours and music now), `pricing`.
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
   also shows), `text card` (a last-resort plate) or `kept` (a file you put back with `media.keep`); both endpoints
   list them in `source_labels`. Each used file also carries `keep`, a 12-character id (`picture_keep` on a graphic).
10. **Revise on the same job** if anything is wrong: `check_plan` and `lock_plan` a revised plan (same narration,
   `get_narration` still works), then `continue_job` again. Cliptude rebuilds from the new plan, reuses every
   picture or clip whose request did not change, and charges nothing more. Repeat 9–10 as needed.
11. **Render once.** `open_editor_project` for the job, then `render_project` for the final MP4.

CLI: `cliptude plan-job`, `narration`, `catalog --compact`, `catalog --types a,b`, `plan-check <job> plan.json`,
`plan-lock <job> plan.json`, `continue <job>`, `plan-get`, `plan-review`, `plan-report`, `upload-media <job> <path>`, `editor <job>`,
`render <project>`.

## Render-only (you bring everything)

When the user already has the narration and you can find every picture and clip yourself, create the plan job with
`render_only: true` (CLI `plan-job --render-only --voiceover narration.mp3`, or `--voiceover-url https://…`). Cliptude then searches, writes, voices
and generates nothing: it aligns your voiceover, builds the plan you lock and renders it, for a flat
**15 credits per minute** (`estimate_credits(render_only=true, output="plan", voiceover_seconds=…)` quotes it).

- Needs `output: "plan"` and your own voiceover. `check_plan` then refuses any searched media: every beat and every
  graphic picture slot is your `url` or `keep` (`render_only_needs_media`), with no `fallback_query`; a plate is
  `{"beat": true}` or your `url`; and there are no `presenter_windows` (no avatar render; `render_only_no_avatar`).
- **Getting your files to Cliptude.** On a remote host (claude.ai, ChatGPT or any agent on `mcp.cliptude.com`) you
  cannot send a local file: give the voiceover as `voiceover_url` and every picture and clip as a `url` (a public
  https link Cliptude downloads once and keeps). With the CLI or a local MCP server you can also upload: the voiceover
  as `voiceover_path`, pictures and clips with `upload_plan_media(job, path)` / `cliptude upload-media`, then use the
  `keep` ids. Trim long clips with `start_s` / `end_s`.
- A board's picture slot draws one image: a clip given there is shown as one frame of it.
- If a file cannot be loaded at build time the job fails and says which (`RenderOnlyMediaMissing`), instead of
  quietly searching. Boards left without a picture hold the footage under them; nothing is generated.
- Graphics, overlays, transitions, sound effects and library music work as in any plan; they cost nothing extra.
- Flow: `create_plan_job(render_only=true, voiceover_url=…)` (or `voiceover_path=…` locally) → `get_narration` →
  find and link (or upload) your media → write the plan → `check_plan` → `lock_plan` → `continue_job` → review → render.

### Finding your own media (render-only)
Cliptude searches for nothing on these jobs, so the pictures and clips come from you, and you must be allowed to use
each one in a published video: Cliptude stores and renders what you send. Good sources, most usable without asking:
- **Free stock with an API key you create:** Pexels (`api.pexels.com`, photos and video) and Pixabay
  (`pixabay.com/api`, photos and video). Both allow commercial use without credit; read their license for identifiable
  people, brands and logos (no implied endorsement, no reselling an unaltered file).
- **Stock the user subscribes to:** Storyblocks, Vecteezy, Shutterstock, Getty Images / iStock, Adobe Stock, Envato
  Elements, Pond5, Unsplash+ and Artgrid. Use their API or downloads with the user's own key or account, within that
  subscription's license (some files are editorial-only, and some plans exclude broadcast or ads).
- **Public domain and open archives:** NASA, US federal government works (most are public domain), the Library of
  Congress, the US National Archives, Wikimedia Commons and the Internet Archive. On Commons and the Archive, check each
  file's own license: CC BY needs a credit line, and "no known restrictions" is not the same as public domain.
- **Short excerpts under fair use** from YouTube and other video sites: a few seconds the narration is actually about
  (commenting on it, reporting on it, analysing it), credited on screen or in the description. Never a whole video,
  long stretches of one creator's work, or a clip used only as background decoration; avoid music videos, films and
  sports broadcasts beyond a brief quoted moment. Fair use is judged case by case, and downloading is subject to each
  platform's terms: the user decides and is responsible.
- **The user's own material:** their footage, photos, screen recordings and documents.
- **Pictures you generate** with an image or video model the user has a key for.
If a key is needed, ask the user to add it to your environment; `sourcing.env.example` next to this skill lists the
ones the sources above use. Never put a key in a plan.

## Pricing

The total for the job equals the website price for the same video. Render-only jobs: 15 credits per minute flat. Re-rendering a finished video costs 10% of the job's credits, and never less than 6 credits per minute of timeline. The narration (charged per minute by voice — 18 credits/min with
ElevenLabs, 4 with Voicetory — plus the script at cost, or `max(2, minutes)` credits to align an uploaded voiceover) is charged when it is ready and is kept if you abandon the
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
  beats. A `url` can be any public http(s) picture or clip; Cliptude downloads and keeps a copy.
- **Rule: fill every picture slot.** For each graphic, read its `media_slots` and give each slot as many pictures
  as its `min` (a two-picture comparison takes two). `check_plan` refuses a plan that gives fewer
  (`missing_media`). A board with picture slots but no `media` (and no `plate`) shows whatever picture is under
  its sentence, which is rarely the one it needs: `check_plan` warns (`borrowed_picture`, naming that beat).
  Give it its own picture unless that beat's picture is what you want there. After the build, `get_plan_review`
  shows `pictures: "n of m"` on each such graphic; anything below m is dropped from the video, never drawn empty.

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
- When full-frame boards cover more than ~65% of the runtime, `check_plan` warns (`board_share_high`) and names the
  boards that can sit over a picture; plate them, shorten them or turn some into overlays.

### Premium boards
- Catalog entries with `premium: true` (about 190, mostly full-frame boards: maps, data charts, comparisons, lists,
  timelines, document and article boards) are the newest designs, ported from a premium motion library.
  `get_kind_catalog(premium=true)` / `cliptude catalog --premium` lists only them. Place one with
  `"animation_type": "<type>"`; check its `prop_schema` and `media_slots` like any other type.
- Each premium board carries its own timed sound cues (several hits matched to its motion). Leave its `sound` unset
  (or `"auto"`) to keep them, even when the plan lists `sfx`. Setting `sound` replaces them with your one sound and
  `"none"` silences it. Listing any `sfx` mutes the sounds Cliptude would add on its own, not your graphics' cues.
- A premium board's `prop_schema` lists the fields its component actually draws (e.g. `multi_pie_chart`:
  `pie1Value`/`pie1Label`…, `comparison_bar_chart`: `titleText`, `firstBarLabel`, `firstBarValue`, `firstBarText`…,
  `five_step_timeline`: `firstStepText`…`fifthStepText`). Fill those data fields from the narration, not just a
  `headline`: most premium boards never draw `headline`, and any required field left empty renders the component's
  sample data. `check_plan` refuses a premium board missing a required field (`missing_prop`, with the real fields in
  the hint). Each field's `examples` shows the sample it replaces; ranges and `enum`s are in the schema.
- Pictures are never props: ask for them in `media_slots` (step images, portraits, cut-outs fill in order).

### A board for every fact (how the best documentaries are planned)
The strongest faceless documentaries put **one premium board on each sentence that states a fact**, and let footage
carry the sentences between. A 9.6-minute reference video ran 50 boards (about 5 a minute, one every ~10 s, each 4-8 s,
median 6 s) over ~56% of its runtime, with almost no text overlays. Plan the same way:
- **Pick the board from the kind of fact the sentence states**, then vary the design within that kind:

  | The sentence states… | Board (premium) |
  |---|---|
  | a percentage or share | `circle_chart_overlay`, `percentage_bar_chart`, `text_and_image_progress_overlay` |
  | a money amount or big count | `number_count_overlay`, `large_number_text_overlay` |
  | two values or two sides | `comparison_bar_chart`, `vertical_bar_chart_comparison`, `double_image_comparison`, `splitscreen_comparison` |
  | a trend or market share over time | `comparison_line_chart_overlay`, `line_chart_callout`, `chart_race_comparison` |
  | a country, city or region | `country_map_icon_popup`, `country_map`, `country_border_highlight`, `multi_country_highlight_text` |
  | a named person | `callout_overlay` (NAME + role), `portrait_card`, `character_introduction_split` |
  | a company or brand | `logo_reveal_overlay`, `logo` |
  | a date or year | `date_overlay`, `day_month_year_text_overlay`, `year_text_overlay` |
  | a list of things | `bullet_point_overlay`, `icon_and3_bulletpoints`, `text_list` |
  | a sequence of events | `five_step_timeline`, `timeline_of_events` |
  | a headline, report or document | `image_article`, `headline_article_slide_up` |
  | a new chapter | `chapter_title_scene`, `chapter_title` (1-3 s) |
- **Give every board its own picture.** Most premium boards take an optional `images` picture behind or beside the data;
  fill it with a search for *this sentence's* subject, never the beat's footage by default. Write the query as subject +
  era + medium: `"Morris Chang Texas Instruments 1980s archival portrait photograph"`, `"TSMC Fab 1 Hsinchu 1987
  archival photo"`, `"Intel 1987 annual report cover scan"`. A board with no picture of its own is the most common
  reason a premium board looks empty (`borrowed_picture`, `empty_board`).
- **Board text is a label, not a sentence:** 2-4 words, the way a title card reads (`"ADVANCED CHIPS"`, callout
  `"MORRIS CHANG"` with role `"TI EXEC"`, versus `"CHIP DESIGN"` / `"OWN A FAB"`). The narration says the rest.
- **Two facts in one sentence can take two boards back to back** (a person card, then that company's logo board).
- **Open on footage.** The hook (first ~15-20 s) is fast cuts, 1.5-4 s a picture; the first board lands on the first
  hard fact. Keep a short overlay title there if you want text earlier.
- **Join chapters and big turns with a real transition** (`film-burn`, `glitch`, `zoom`, `sliding-pan` or a
  template-allowed one), about one every 20 s, and leave the other cuts on `auto`. Silence about half of those joins
  (`"transition_sound": "none"`) so the whooshes do not repeat on every cut.
- **Let the boards make the sound.** The reference video had no separate effects track: its sound was each board's own
  cues and the joins, over one music bed. Leave premium boards' `sound` unset and set their `volume` to 30-50 (0 on a
  quiet, serious line); add `sfx` hits only for the two or three biggest moments (a number landing, a verdict).
- Text overlays are only for a quote over the footage, at most one graphic in ten. Names, dates, years and
  money the narration states go on boards (name card, date or year board, number board), not on tags or
  highlights: they count for the coverage targets too. `check_plan` warns `too_many_overlays` when overlays are
  more than 10% of graphics, and `few_board_kinds` under about 16 different board designs per 10 minutes.

### Speaker boards (Avatar Video and Talking Head)
- Catalog entries with `speaker: true` (`type: "speaker"`, about 60, named `talking_head_*` plus
  `dual_metric_talking_head` and `framework_mind_map_talking_head`) keep the presenter on screen and animate a graphic
  with them: a bar chart, numbered list, receipt, calendar, mind map, chapter title, card collage and more. The
  catalog lists them only for these two formats; elsewhere `check_plan` refuses one (`wrong_format`).
- Put each one **inside a full-screen presenter window** (start and end within it); Cliptude plays that window's
  presenter clip in the board. Outside one, or in a split window, `check_plan` refuses it (`speaker_outside_presenter`).
  They are the exception to "full-screen windows may not cover graphics".
- Fill the fields in its `prop_schema`, not a `headline`: `talking_head_bar_chart` draws `title` and `bars`
  (`[{"value": 0-100, "icon": "chart"}]`), `talking_head_numbered_list` draws `items`, `talking_head_receipt_breakdown`
  draws `items` as `[{"label", "price"}]`. Lists are JSON arrays of the item shape the schema shows.
- Use them to keep a presenter window from being one long talking head: about one per presenter window, on the
  sentence that states the number, list or step it shows. A card collage, browser window or feed board also takes
  pictures through its `media_slots`; the presenter clip is never one of them.

### Beat media
One of: `query` (+ `alts`, `negatives`, `query_kind` such as object, place or person, `prefer`: video|image), `person`,
`url` (+ `fallback_query`; public http(s) images ≤15 MB or videos ≤300 MB), `auto: true` (Cliptude writes the
query), or `keep` (+ `fallback_query`): the `keep` id `get_plan_review` or `get_plan_report` shows for a file an
earlier build of this job used. A kept file is never searched again, so change a beat's request to try for something
better and, if the new pick is worse, put the old one back with `{"keep": "<id>"}`. Revising keeps an unchanged
request's file only from the last build; `keep` reaches any build of the job. Picture slots of graphics take `keep`
wherever they take `url`.
- **Your own files.** A picture or clip you found as a page (not a direct file link) or have on disk: download it, then
  `upload_plan_media(job_id, path)` (CLI `cliptude upload-media <job> <path>`; JPG/PNG/WEBP/GIF up to 15 MB, MP4/MOV/
  WEBM/M4V up to 300 MB). It goes straight to Cliptude's storage on signed URLs (no storage keys needed), is checked to
  be a real picture or video, and returns `{"keep": "<id>"}` to use as above. Only send media you may use.
- **Part of a clip.** On a beat whose media is a `url` or `keep` clip, `start_s` and `end_s` (seconds into the clip)
  play only that part: `{"keep": "3f9a1c0d2b7e", "start_s": 72, "end_s": 78}`. `check_plan` refuses them on a searched
  beat (`invalid_trim`); the report shows the cut, or `failed` if the whole clip played instead. Your query is searched verbatim first, then your `alts`; if nothing usable is found Cliptude uses its normal
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
Leave out any field you are happy with: `get_narration`'s `template.look` shows what the template has now, and a
`look` field you set replaces it for this video.

### Format-specific
- `ranked_items` (Top 10 only): one title per entry, unique ranks, titles may not overlap each other or a board.
- `presenter_windows` (Avatar Video only): `full_screen`, `split_left` or `split_right`; no overlaps; total seconds
  within the template's cap (intro-only templates: one opening window). Full-screen windows may not cover graphics,
  except speaker boards, which must sit inside one.
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
Graphics/min ≈ 4.9 (3.6–6.7); ~55% of runtime covered (50–60%); ≥80% of graphics starting on a sentence start; first graphic
≤ 6 s; ≥ 9 visuals in the first minute; longest gap ≲ 30 s; no kind more than twice in a row; ≥ 16 distinct kinds per
10 minutes; ~70% of graphics are premium boards, covering ~55% of the runtime (`board_share_high` over 65%); every spoken date, ~50% of years and ~60% of money figures on screen. Warnings also flag
numbers on cards that are not spoken nearby and sample text left in props.
Pictures: about one beat per sentence, each on screen 4–6 s on average (`avg_picture_s`); `slow_pictures` warns
above 6 s on average or for any picture held over 12 s, naming the beats to split. `few_graphics` warns below 3.6
graphics a minute, `few_boards` when boards are under 40% of graphics, `boards_too_short` when boards hold under 4 s
on the median, `low_board_share` when boards cover under 45% of the runtime, and `few_joins` under one planned join
per 40 s (videos over 3 minutes).

## What Cliptude still decides
Joins on cuts you leave at `"auto"`, the direction and timing details inside a reveal (a locked plan gets no
collages or reveals you did not ask for), where a searched picture or clip comes from (you choose video vs still), the
plain camera move on a searched beat without a `camera`, the
voice, caption styling, and fallbacks when your media cannot be found. The report (`get_plan_report`) lists every
graphic, transition, reveal and free sound effect you planned against what was rendered, with the reason for anything
dropped.
