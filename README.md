# Cliptude CLI + MCP

Generate videos from a saved template. Same account and credits as cliptude.com.

## Set up

1. Install the CLI: run `npm i -g @cliptude/cli`.
2. Authenticate: run `cliptude auth login` and complete the sign-in in the browser it opens.
3. Install the companion skills: run `npx skills add cliptude/cliptude --path mcp_server/skills` (includes **cliptude**, **cliptude-script**, **cliptude-faceless** and **cliptude-plan**).

From this repo without npm:

```bash
pip install -e .
cliptude auth login
```

## Commands

```bash
cliptude templates
cliptude generate --template "Finance vector weekly" --title "Why index funds win" --prompt "why most retail traders underperform the S&P over 10 years, keep it for beginners" --duration 3-4
cliptude generate --template "Finance vector weekly" --title "Why index funds win" --script-file script.txt
cliptude status <job-id>
```

`--title` is required. Never copy the title into `--prompt`. If you only have a topic, follow **cliptude-script** (web search → spoken narration → `--script-file`). Optional `--duration` is a wizard bucket (`3-4`, `6-8`, `10-12`, …). Omit it to use the template duration, or the word-count estimate when you pass `--script`. `generate` returns immediately (`queued`). Poll `status`.

Agent-planned videos (Faceless Documentary, Faceless Top 10, Avatar Video): your agent plans every shot, graphic,
cut and sound, and Cliptude voices, sources and renders it. See **cliptude-plan**.

```bash
cliptude plan-job --template "TSMC doc" --title "The island" --script-file script.txt
cliptude narration <job-id> > narration.json
cliptude catalog --format faceless_documentary --compact
cliptude plan-check <job-id> plan.json            # repeat until valid
cliptude plan-lock <job-id> plan.json && cliptude continue <job-id>
cliptude plan-review <job-id>                     # review the build, then revise on the same job or render
cliptude editor <job-id> && cliptude render <project-id> --wait
```

## MCP

Remote Streamable HTTP: `https://mcp.cliptude.com/mcp` (OAuth or Bearer).

```bash
cliptude-mcp
```

Unauthenticated `POST /mcp` returns 401 with `WWW-Authenticate` pointing at RFC 9728 metadata.
Stdio never writes to stdout except JSON-RPC.
