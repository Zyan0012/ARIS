# Claude Code + Codex Review Bridge

Run ARIS in Claude Code with:

- Claude Code as the main executor
- a local `codex-review` MCP bridge for reviewer calls
- Codex CLI as the reviewer backend
- async polling for long paper and project reviews

This path is additive. It does not replace the default `codex` MCP server if you still want direct Codex access for other tasks.

## Architecture

- Base skill set: `skills/*`
- Reviewer override layer: `skills/skills-claude-codex-review/*`
- Reviewer bridge: `mcp-servers/codex-review/`

Install order:

1. install base `skills/*`
2. install `skills/skills-claude-codex-review/*`
3. register `codex-review` MCP

## Install

```bash
git clone https://github.com/wanshuiyin/Auto-claude-code-research-in-sleep.git
cd Auto-claude-code-research-in-sleep

mkdir -p ~/.claude/skills
cp -a skills/* ~/.claude/skills/
cp -a skills/skills-claude-codex-review/* ~/.claude/skills/

mkdir -p ~/.claude/mcp-servers/codex-review
cp mcp-servers/codex-review/server.py ~/.claude/mcp-servers/codex-review/server.py
cp mcp-servers/codex-review/README.md ~/.claude/mcp-servers/codex-review/README.md
claude mcp add codex-review -s user -- python3 ~/.claude/mcp-servers/codex-review/server.py
```

## What gets overridden

This overlay currently rewrites the core long-review Claude Code skills:

- `research-review`
- `novelty-check`
- `research-refine`
- `auto-review-loop`
- `paper-plan`
- `paper-figure`
- `paper-write`
- `auto-paper-improvement-loop`

These are the skills where long synchronous Codex review prompts are most likely to hit tool-host timeouts.

## Sync vs async

The bridge exposes both sync and async review tools, but the overlayed skills are written to prefer the async pattern:

- `review_start`
- `review_reply_start`
- `review_status`

This avoids waiting for a long reviewer response inside a single MCP tool call.

In practice this means you should not need to ask for async review explicitly each time. Once the override skills are installed, the covered review-heavy Claude Code skills route through `codex-review` by default. Prompt-level instructions are only needed if you want to override that default behavior.

## Provider caveat

The bridge reuses the local Codex CLI configuration as-is. Before using this path, verify that a short direct Codex command succeeds:

```bash
codex exec --skip-git-repo-check "Reply with exactly OK."
```

If that fails, the bridge will surface the same underlying error. In that case:

- set `CODEX_REVIEW_MODEL` to a provider-supported model, or
- fix the Codex CLI provider configuration first

For WSL setups that also have a Windows Codex install, the bridge subprocess now pins `CODEX_HOME` to `$HOME/.codex` by default. This avoids cross-loading Windows-side skills into the WSL reviewer process.

## Maintenance

The overlay files are generated from the base Claude Code skills by:

```bash
python tools/generate_claude_codex_review_overrides.py
```

Regenerate the overlay whenever the source `skills/*` files change.
