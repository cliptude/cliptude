---
name: cliptude
description: Generate Cliptude videos from a saved template using the cliptude CLI. Use when the user wants to create a video from a topic or a custom script via Cliptude.
---

# Cliptude video generation

Style lives in a **saved template** (voice, model, format). The user must finish the Cliptude wizard once and click **Save as template**.

## Auth

```bash
npm i -g @cliptude/cli
cliptude auth login
```

Or `export CLIPTUDE_API_KEY='clp_…'` from /developers/. Never commit keys.

## Commands

```bash
cliptude templates
cliptude generate --template "Finance vector weekly" --title "Why index funds win" --prompt "topic…"
cliptude generate --template "Finance vector weekly" --title "Why index funds win" --script-file script.txt
cliptude status <job-id>
cliptude jobs
cliptude account
```

`generate` returns immediately (`queued`). Poll `status` until `completed` or `failed`. Jobs take 10–40 minutes. Do not block waiting for the MP4.

If `templates` is empty, tell the user to finish the wizard in Cliptude and Save as template.

If the user pastes a full narration, use `--script` / `--script-file`, not `--prompt`. Formatting: spoken words only (no Scene 1, no [Music plays]). See https://cliptude.com/docs/custom-script/

402 = not enough credits (nothing queued). 403 = need a paid plan (SaaS).
