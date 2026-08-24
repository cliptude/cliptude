---
name: cliptude-faceless
description: Author Faceless Explainer storyboard and frame HTML against a Cliptude coding kit, then submit a zip. Use when the user wants Claude Code / Cursor / Codex to code Faceless visuals (faceless_coding=external). If they only gave a topic or title, write the narration with cliptude-script first — never pass the title as --prompt.
---

# Cliptude Faceless bring-your-own coding

You plan **storyboard.json** and one HTML file per frame. Cliptude owns TTS, word rail, music, `hyperframes check` (no Claude repair), and render.

If the user only gave a **topic or title**, write the narration first with **cliptude-script** (web search → spoken script) and pass `--script-file`. Do **not** put the title in `--prompt`. If they already pasted a full script, use that. Only use `--prompt` when they explicitly want Cliptude to write the VO — and the prompt must be a brief, not the title.

Do **not** start storyboard or frames until `status=awaiting_project` and `coding_kit` is present. Cue times must come from that kit’s `word_rail.json` for that specific `voice.mp3`. Do not invent timings.

After unzip, **read `AUTHORING.md` and `frame.md` first**. Production Claude is held to the same visual bar. `npx hyperframes check` passing is required and **not sufficient**.

Docs: https://cliptude.com/docs/api/#faceless-byo-coding

## Visual bar (same as Cliptude production)

GRAPHIC FIRST: ≥60% of each 1920×1080 frame is invented illustration / dataviz / shapes (aisles, pallets, product silhouettes, bars, maps, people, lattices). On-screen text is short kinetic labels from the word rail — never pasted VO sentences.

ANTI-TEMPLATE: Do not stamp one poster shell (left kicker + giant headline + thin sub + faint orb + cue words in an empty column) across frames. That is what makes a “basic” video. Each `blueprint_id` must change the **layout**: the signature object fills most of the canvas; labels stamp onto it.

- Instantiate the blueprint’s signature move from `blueprints.json` / `frame.md`.
- Vary `blueprint_id` across consecutive frames. Cap text-heavy recipes (`kinetic_type_beats`, `titlecard_reveal`, `ticker_takeover`, `typewriter_reveal`, `comparison_split`) to ≤ half the film.
- At least one Three.js frame (`runtime: "three"`). At least ~1/3 drawing/dataviz/3D recipes.
- `data-layout-allow-overlap` is only for designed layering, not a dodge for piled cue copy.
- Copy `_example.html` only as a **density** reference. Do not clone it, and do not write a Python/JS template that restyles the same poster for every deal.

## Auth

```bash
npm i -g @cliptude/cli
cliptude auth login
```

## Flow

1. Template must be **Faceless Explainer** (wizard: voice, style, duration; Save as template).
2. Create the job (returns in seconds, `queued`). Nothing is voiced yet.

```bash
# After cliptude-script (topic/title only) or a user-pasted script:
cliptude generate --template "My Faceless" --title "Why index funds win" --script-file script.txt --faceless-coding external
# Only if the user asked Cliptude to write the VO — brief, never the title:
cliptude generate --template "My Faceless" --title "Why index funds win" --prompt "why most retail traders underperform the S&P over 10 years, keep it for beginners" --faceless-coding external
```

3. Poll `cliptude status <job-id>` until `awaiting_project` / `coding_kit` (Phase A: script if prompt-only, then TTS, word rail, kit zip). Current step may show `Writing script...` then `Generating voiceover...`.
4. Download the kit. **Wait for this kit.** Do not author HTML before it exists.

```bash
cliptude kit <job-id> --out ./kit.zip
```

5. Unzip. Read `AUTHORING.md` + `frame.md`. Write:
   - `storyboard.json` (frames/cues/transitions timed to `word_rail.json`)
   - `compositions/frames/{n:02d}-{id}.html` for every storyboard frame
   - Do **not** upload `index.html` (Cliptude rebuilds it)
6. Local check (same as Cliptude; not `--strict`):

```bash
npx hyperframes check --snapshots --json
```

Glance at snapshots. If a frame is mostly empty field + type, rewrite that frame — do not submit.

7. Zip those files and submit. Returns immediately (202). Do **not** block until the MP4.

```bash
cliptude submit-project <job-id> ./project.zip
cliptude status <job-id>
```

8. Phase C is 10–40 minutes: check (no Claude repair), story music, HyperFrames render. Poll until `completed` and `output.url` / `output.path`.

If `get_job.qa` is present and the job `failed`, fix frames locally and submit again. Cliptude will not rewrite your HTML.

MCP: `create_job` with `faceless_coding=external`, `download_coding_kit` (URL/path only — never zip bytes), `submit_faceless_project(job_id, zip_path)` reading a **local path**. Never `wait_for_job` until MP4.
