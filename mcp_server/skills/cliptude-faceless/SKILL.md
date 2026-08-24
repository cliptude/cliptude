---
name: cliptude-faceless
description: Author Faceless Explainer storyboard and frame HTML against a Cliptude coding kit, then submit a zip. Use when the user wants Claude Code / Cursor / Codex to code Faceless visuals (faceless_coding=external).
---

# Cliptude Faceless bring-your-own coding

You plan **storyboard.json** and one HTML file per frame. Cliptude owns script (if needed), TTS, word rail, music, `hyperframes check` (no Claude repair), and render.

Do **not** start storyboard or frames until `status=awaiting_project` and `coding_kit` is present. Cue times must come from that kit’s `word_rail.json` for that specific `voice.mp3`. Do not invent timings.

Docs: https://cliptude.com/docs/api/#faceless-byo-coding

## Auth

```bash
npm i -g @cliptude/cli
cliptude auth login
```

## Flow

1. Template must be **Faceless Explainer** (wizard: voice, style, duration; Save as template).
2. Create the job (returns in seconds, `queued`). Nothing is voiced yet.

```bash
cliptude generate --template "My Faceless" --title "…" --prompt "…" --faceless-coding external
# or --script / --script-file
```

3. Poll `cliptude status <job-id>` until `awaiting_project` / `coding_kit` (Phase A: script if prompt-only, then TTS, word rail, kit zip). Current step may show `Writing script...` then `Generating voiceover...`.
4. Download the kit. **Wait for this kit.** Do not author HTML before it exists.

```bash
cliptude kit <job-id> --out ./kit.zip
```

5. Unzip. Write:
   - `storyboard.json` (frames/cues/transitions timed to `word_rail.json`)
   - `compositions/frames/{n:02d}-{id}.html` for every storyboard frame
   - Do **not** upload `index.html` (Cliptude rebuilds it)
6. Local check (same as Cliptude; not `--strict`):

```bash
npx hyperframes check --snapshots --json
```

7. Zip those files and submit. Returns immediately (202). Do **not** block until the MP4.

```bash
cliptude submit-project <job-id> ./project.zip
cliptude status <job-id>
```

8. Phase C is 10–40 minutes: check (no Claude repair), story music, HyperFrames render. Poll until `completed` and `output.url` / `output.path`.

If `get_job.qa` is present and the job `failed`, fix frames locally and submit again. Cliptude will not rewrite your HTML.

MCP: `create_job` with `faceless_coding=external`, `download_coding_kit` (URL/path only — never zip bytes), `submit_faceless_project(job_id, zip_path)` reading a **local path**. Never `wait_for_job` until MP4.
