# Cliptude CLI + MCP

Generate videos from a saved template. Same account and credits as cliptude.com.

## Set up

1. Install the CLI: run `npm i -g @cliptude/cli`.
2. Authenticate: run `cliptude auth login` and complete the sign-in in the browser it opens.
3. Install the companion skills: run `npx skills add cliptude/cliptude` (includes **cliptude**, **cliptude-script** and **cliptude-plan**).

From this repo without npm:

```bash
pip install -e .
cliptude auth login
```

## Commands

```bash
cliptude templates
cliptude template-options --format faceless_documentary
cliptude voices --provider elevenlabs
cliptude template-create --name "TSMC doc" --format faceless_documentary --set voice_id=<id> --set voice_name=George --set generation_model=pro --set faceless_documentary_style=investigative
cliptude template-create --name "TSMC doc" --format faceless_documentary --settings-file settings.json
cliptude generate --template "Finance vector weekly" --title "Why index funds win" --prompt "why most retail traders underperform the S&P over 10 years, keep it for beginners" --duration 3-4
cliptude estimate --template "Finance vector weekly" --script-file script.txt
cliptude avatars --section mine
cliptude generate --template "My avatar weekly" --title "Episode one" --script-file script.txt --avatar 42
cliptude generate --template "Finance vector weekly" --title "Why index funds win" --script-file script.txt
cliptude generate --template "Finance vector weekly" --title "Why index funds win" --voiceover narration.mp3
cliptude generate --template "My talking head" --title "Weekly recap" --talking-head clip.mp4
cliptude generate --template "My avatar weekly" --title "Grandma egg trick" --script-file script.txt --avatar-ai-footage --avatar-intro-only
cliptude script --template "Finance vector weekly" --title "Why index funds win" --prompt "why most retail traders underperform the S&P" --duration 3-4
cliptude voiceover --template "Finance vector weekly" --title "Why index funds win" --script-file script.txt
cliptude download-voiceover <job-id> --out narration.mp3
cliptude editor <job-id>                                   # editor project id, revision, timeline item ids
cliptude agent <project-id> --prompt "Replace this shot with real footage of a TSMC fab" --items clip-1 --tier smart --wait
cliptude render <project-id> --wait                        # new MP4 of the edited timeline
cliptude continue <job-id>
cliptude status <job-id>
```

No template yet? `template-create` makes one without the create wizard: pick a `voice_id` from `voices`, and see `template-options` for every other field, its choices and its default (unset fields take the wizard default). Values are validated like the wizard; uploads (custom music, avatars, characters) still need the wizard. MCP tools: `get_template_options`, `list_voices`, `create_template`. For Avatar Video, `avatars` (MCP `list_avatars`) lists your avatars; set one with `--set avatar_profile_id=<id>` on `template-create`, or override it per video with `--avatar <id>` (and `--avatar-engine iii|iv|v`) on `generate`, `plan-job` and `estimate`. The avatar's gender must match the voice.

To check the price first, `estimate` (MCP `estimate_credits`, REST `POST /api/v1/jobs/estimate`) takes the same template and prompt/script as `generate` (an upload as `--voiceover-seconds` / `--talking-head-seconds`) and returns the credits the job would need and whether the account has them. It creates and charges nothing.

To revise a finished video, `editor` opens its timeline (item ids, times, text), `agent` asks Cliptude Agent for an edit (it charges agent credits; reference item ids with `--items`), and `render` exports a new MP4. MCP tools: `open_editor_project`, `get_editor_project`, `run_agent`, `get_agent_run`, `cancel_agent_run`, `render_project`, `get_render`.

### Agent-planned videos

On Faceless Documentary, Faceless Top 10 and Avatar Video templates an agent can plan every visual and leave Cliptude
only the voicing, sourcing and rendering:

1. `cliptude plan-job` (stops at `plan_ready`), `cliptude narration <job>`.
2. `cliptude catalog --compact`, then `--types a,b` for full entries (`--premium` lists only the premium boards).
3. `cliptude plan-check <job> plan.json` until valid, `cliptude plan-lock <job> plan.json`, `cliptude continue <job>`.
   The job stops at `ready_for_edit`: built and charged, not rendered.
4. `cliptude plan-review <job>`: every picture or clip (asked vs used, link to the file) and every graphic (the props
   it will be drawn with, or why it is missing). `cliptude plan-report <job>` adds each fallback.
5. To revise, `plan-check` / `plan-lock` a new plan on the same job and `continue` again: same narration, unchanged
   media reused, nothing more charged.
6. Render once: `cliptude editor <job>`, `cliptude render <project> --wait`.

MCP tools: `create_plan_job`, `get_narration`, `get_kind_catalog`, `check_plan`, `lock_plan`, `get_plan`,
`continue_job`, `get_plan_review`, `get_plan_report`, then `open_editor_project` and `render_project`.
The plan sets the look (theme, style, colours, music), every graphic with its own data, colours and sound, the
footage under every beat, the transition on each cut, two-image reveals, and free sound effects; `get_kind_catalog`
lists the graphics, `transitions`, `reveal_styles` and `sounds` (with previews). The plan schema and rules are in
`skills/cliptude-plan/SKILL.md`. The job costs the same as the website price.

`--title` is required. Provide **one** of `--prompt`, `--script` / `--script-file`, `--voiceover`, or `--talking-head`. Never copy the title into `--prompt`. If you only have a topic, follow **cliptude-script** (web search → spoken narration → `--script-file`), or ask Cliptude to write it with `cliptude script`. After a completed `script` or `voiceover` job, `cliptude continue <job-id>` turns that same job into a full video (reuses the script; voiceover jobs reuse the TTS file). `--voiceover` is a local audio file (same as create.html); it is transcribed and skips TTS — do not combine it with prompt/script. `--talking-head` is a presenter video on a Talking Head template (MP4, MOV, WEBM, AVI); it is transcribed and skips TTS. Optional `--duration` is a wizard bucket (`3-4`, `6-8`, `10-12`, …). Omit it to use the template duration, or the word-count estimate when you pass `--script`. Uploaded audio or talking-head video sets duration from the file. Avatar Video templates can override **Avatar Intro Only** and **AI Image + AI Video** with `--avatar-intro-only` / `--no-avatar-intro-only` and `--avatar-ai-footage` / `--no-avatar-ai-footage`. Omit those flags to keep the template. `generate` / `script` / `voiceover` / `continue` return immediately (`queued`). Poll `status`.

## MCP

Remote Streamable HTTP: `https://mcp.cliptude.com/mcp` (OAuth or Bearer).

```bash
cliptude-mcp
```

Unauthenticated `POST /mcp` returns 401 with `WWW-Authenticate` pointing at RFC 9728 metadata.
Stdio never writes to stdout except JSON-RPC.
