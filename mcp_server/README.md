# Cliptude CLI + MCP

Generate videos from a saved template. Same account and credits as cliptude.com.

## Set up

1. Install the CLI: run `npm i -g @cliptude/cli`.
2. Authenticate: run `cliptude auth login` and complete the sign-in in the browser it opens.
3. Install the companion skills: run `npx skills add cliptude/cliptude --path mcp_server/skills`.

From this repo without npm:

```bash
pip install -e .
cliptude auth login
```

## Commands

```bash
cliptude templates
cliptude generate --template "Finance vector weekly" --title "Why index funds win" --prompt "…"
cliptude generate --template "Finance vector weekly" --title "Why index funds win" --script-file script.txt
cliptude status <job-id>
```

`--title` is required. `generate` returns immediately (`queued`). Poll `status`.

Faceless bring-your-own coding:

```bash
cliptude generate --template "My Faceless" --title "…" --prompt "…" --faceless-coding external
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
