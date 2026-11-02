# Cliptude plugin for Claude

This plugin bundles the Cliptude connector (`https://mcp.cliptude.com/mcp`) with three skills: **cliptude** (make a video from a saved template), **cliptude-script** (research a topic and write the spoken narration) and **cliptude-plan** (direct every visual of the video yourself). Install it from the Claude directory, connect Cliptude on the plugin's Connectors tab and sign in with your Cliptude account. Videos use that account's credits; new jobs show their settings and credit price for you to confirm before they start.

**Data the plugin sends.** The skills run in Claude and send nothing themselves. The connector sends Cliptude what you ask it to work with: video titles, scripts or prompts, template settings, product page URLs, and any audio, video or images you upload. Cliptude stores these with your account to produce and keep your videos. Sign-in uses OAuth on cliptude.com; the plugin holds no API keys. See the [privacy policy](https://cliptude.com/privacy/) and [terms](https://cliptude.com/terms/).

The CLI and the self-hosted MCP server live in the [repository root](https://github.com/cliptude/cliptude).
