# Codex Review MCP

Bridge Claude Code ARIS workflows to the local Codex CLI with a resumable async review contract.

## What it does

- Keeps Claude Code as the executor
- Uses the local Codex CLI as the reviewer transport
- Exposes synchronous MCP tools: `review`, `review_reply`
- Exposes asynchronous MCP tools for long reviewer prompts: `review_start`, `review_reply_start`, `review_status`

The synchronous tools return a JSON string containing `threadId` and `response`.
The asynchronous start tools return a JSON string containing `jobId` and `status`, and `review_status` later returns the final `threadId` and `response`.

## Install into Claude Code

```bash
mkdir -p ~/.claude/mcp-servers/codex-review
cp mcp-servers/codex-review/server.py ~/.claude/mcp-servers/codex-review/server.py
cp mcp-servers/codex-review/README.md ~/.claude/mcp-servers/codex-review/README.md
claude mcp add codex-review -s user -- python3 ~/.claude/mcp-servers/codex-review/server.py
```

## Environment Variables

- `CODEX_BIN`: Codex CLI path, defaults to `codex`
- `CODEX_REVIEW_MODEL`: optional reviewer model override
- `CODEX_REVIEW_SYSTEM`: optional default reviewer system instructions
- `CODEX_REVIEW_TIMEOUT_SEC`: subprocess timeout, defaults to `600`
- `CODEX_REVIEW_REASONING_EFFORT`: Codex reasoning effort override, defaults to `xhigh`
- `CODEX_REVIEW_CODEX_HOME`: optional explicit Codex home; defaults to `$HOME/.codex` for the subprocess
- `CODEX_REVIEW_DISABLE_FAST_MODE`: set `0` to keep Codex fast mode enabled; default disables it
- `CODEX_REVIEW_SKIP_GIT_REPO_CHECK`: set `0` to keep the repo check enabled; default skips it
- `CODEX_REVIEW_EXTRA_ARGS`: additional raw Codex CLI arguments appended to every review call
- `CODEX_REVIEW_STATE_DIR`: bridge state directory, defaults to `~/.claude/state/codex-review`
- `CODEX_REVIEW_DEBUG_LOG`: debug log path, defaults to `~/.claude/state/codex-review/codex-review-debug.log`

## Notes

- The bridge runs Codex via `codex exec` and `codex exec resume`.
- The `system` argument is accepted for compatibility and is prepended to the prompt as text.
- The `tools` argument is accepted for compatibility but ignored.
- `threadId` is the native Codex session id emitted by `codex exec`.
- `jobId` is a bridge-local background task id stored on disk under `~/.claude/state/codex-review/jobs/`.

## Provider caveat

This bridge reuses whatever Codex CLI authentication and provider config already exists in the local environment.

If your Codex CLI uses a custom API endpoint or model mapping, validate that a short direct command works first:

```bash
codex exec --skip-git-repo-check "Reply with exactly OK."
```

If that fails, the bridge will surface the same underlying provider error. In that case, set `CODEX_REVIEW_MODEL` to a provider-supported model or fix the Codex CLI provider configuration first.

If you run Claude Code from WSL and also have a Windows-side Codex install, this bridge pins the subprocess `CODEX_HOME` to `$HOME/.codex` by default so review jobs stay on the WSL Codex config instead of inheriting a mixed Windows skill directory.

## When to use sync vs async

- Use `review` / `review_reply` for short prompts that comfortably finish within the host MCP tool timeout.
- Use `review_start` / `review_reply_start` + `review_status` for long paper or project reviews.
