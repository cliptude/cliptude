# Install Cliptude skills (for agents)

The user asked to set up Cliptude so they can generate videos from this session.

1. Install the CLI: run `npm i -g @cliptude/cli`.
2. Authenticate: ask the user to run `cliptude auth login` and complete the sign-in in the browser it opens.
3. Install the companion skills: run `npx skills add cliptude/cliptude --path mcp_server/skills` (includes **cliptude**, **cliptude-script** and **cliptude-plan**).

If npm global install is not available in this repo checkout:

```bash
pip install -e mcp_server
cliptude auth login
```

Verify with `cliptude auth status` and `cliptude templates`.

If the user only gave a topic or title, **do not** pass that title as `--prompt`. Follow the **cliptude-script** skill (web search → spoken narration → `--script-file`). If they want **Cliptude** to write the script and stop, use `cliptude script` / `create_script`. If they want narration audio only, use `cliptude voiceover` / `create_voiceover`. After a completed script or voiceover job, `cliptude continue` / `continue_job` turns it into a video on the same job id. If they have a voiceover audio file, use `--voiceover path/to/file.mp3` and omit prompt/script. If they have a talking-head recording, use `--talking-head path/to/clip.mp4` with a Talking Head template.

To direct every visual yourself (theme, colours, graphics, text, timing and the search query or URL behind every shot) on a Faceless Documentary, Faceless Top 10 or Avatar Video template, follow **cliptude-plan**: `cliptude plan-job` / `create_plan_job`, then check, lock and continue the plan, review the build with `cliptude plan-review` / `get_plan_review` (revise on the same job if needed), and render once; Cliptude only voices, sources and renders it.

Then generate with a saved template name (prompt, script, voiceover, or talking-head — pick one):

```bash
cliptude generate --template "Finance vector weekly" --title "Why index funds win" --script-file script.txt --duration 3-4
cliptude generate --template "Finance vector weekly" --title "Why index funds win" --voiceover narration.mp3
cliptude generate --template "My talking head" --title "Weekly recap" --talking-head clip.mp4
```
