---
name: cliptude
description: Generate Cliptude videos from a saved template using the cliptude CLI. Use when the user wants to create a video from a topic or a custom script via Cliptude. If they only gave a topic or title, follow cliptude-script first — never pass the title as --prompt.
---

# Cliptude video generation

Style lives in a **saved template** (voice, model, format). The user must finish the Cliptude wizard once and click **Save as template**.

## Before generate — script vs prompt

**Never pass the video title as `--prompt` / `prompt`.** Production researches the topic, then writes narration. Copying the title into prompt skips that and yields a thin video.

| User gave you… | Do this |
|---|---|
| Topic or title only (“make a video about X”) | Follow **cliptude-script**: web-search, write spoken narration, then `--script-file` |
| Full narration they want voiced | `--script` / `--script-file` as-is (spoken words only) |
| Explicit “let Cliptude write the script” | `--prompt` = a **brief** (audience, angle, must-include facts) — not the title |

Read `../cliptude-script/SKILL.md` before `generate` / `create_job` when the user did not paste a script.

## Auth

```bash
npm i -g @cliptude/cli
cliptude auth login
```

Or `export CLIPTUDE_API_KEY='clp_…'` from /developers/. Never commit keys.

## Commands

```bash
cliptude templates
cliptude generate --template "Finance vector weekly" --title "Why index funds win" --script-file script.txt --duration 3-4
cliptude generate --template "Finance vector weekly" --title "Why index funds win" --prompt "why most retail traders underperform the S&P over 10 years, keep it for beginners" --duration 3-4
cliptude status <job-id>
cliptude jobs
cliptude account
```

`generate` returns immediately (`queued`). Poll `status` until `completed` or `failed`. Jobs take 10–40 minutes. Do not block waiting for the MP4.

If `templates` is empty, tell the user to finish the wizard in Cliptude and Save as template.

Custom script formatting: spoken words only (no Scene 1, no [Music plays]). See https://cliptude.com/docs/custom-script/

Optional `--duration` (`3-4`, `6-8`, `10-12`, …) overrides the template. Without it, prompt jobs use the template duration and script jobs estimate from word count.

402 = not enough credits (nothing queued). 403 = need a paid plan (SaaS).
