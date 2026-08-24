# Cliptude CLI + MCP

Generate videos from a saved template. Same account and credits as cliptude.com.

## Set up

1. Install the CLI: run `npm i -g @cliptude/cli`.
2. Authenticate: run `cliptude auth login` and complete the sign-in in the browser it opens.
3. Install the companion skills: run `npx skills add cliptude/cliptude --path mcp_server/skills` (includes **cliptude**, **cliptude-script**, and **cliptude-faceless**).

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

Faceless bring-your-own coding:

```bash
cliptude generate --template "My Faceless" --title "Why index funds win" --script-file script.txt --faceless-coding external
cliptude kit <job-id> --out ./kit.zip
cliptude submit-project <job-id> ./project.zip
```

## MCP

Remote Streamable HTTP: `https://mcp.cliptude.com/mcp` (OAuth or Bearer).

```bash
cliptude-mcp
```

Unauthenticated `POST /mcp` returns 401 with `WWW-Authenticate` pointing at RFC 9728 metadata.
Stdio never writes to stdout except JSON-RPC.
