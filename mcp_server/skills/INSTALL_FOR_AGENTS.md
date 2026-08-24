# Install Cliptude skills (for agents)

The user asked to set up Cliptude so they can generate videos from this session.

1. Install the CLI: run `npm i -g @cliptude/cli`.
2. Authenticate: ask the user to run `cliptude auth login` and complete the sign-in in the browser it opens.
3. Install the companion skills: run `npx skills add cliptude/cliptude --path mcp_server/skills`.

If npm global install is not available in this repo checkout:

```bash
pip install -e mcp_server
cliptude auth login
```

Verify with `cliptude auth status` and `cliptude templates`.

Then generate with a saved template name:

```bash
cliptude generate --template "Finance vector weekly" --title "Why index funds win" --prompt "…"
```
