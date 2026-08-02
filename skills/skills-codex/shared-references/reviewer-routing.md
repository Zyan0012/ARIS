# Reviewer Routing

## Default Reviewer Contract

All reviewer-heavy Codex base skills use the same default contract:

- executor: current Codex main agent
- reviewer: second Codex reviewer
- reasoning effort: `xhigh`
- round 1: `spawn_agent`
- follow-up rounds: `send_input`

This is the base default for `skills/skills-codex/`. No effort level or unrelated parameter changes it.

> ⚠️ **Same-family by default — Type-A only, NOT a cross-family verdict.** The executor here is Codex (GPT family) and this default reviewer is a *second Codex agent* — same family. That is a valid **Type-A** review (it finds omissions, ranks weaknesses, drives the fix loop), but it is **NOT** the cross-model **Type-B acquittal** ARIS's invariant requires — one model family judging itself voids the verdict (mainline `acceptance-gate.md`). For an opt-in Type-B cross-family verdict without changing defaults, pass `--reviewer: claude` and route through the local `claude-review` MCP bridge. The actual reviewer model is whatever the local Claude Code CLI is configured to use (for example GLM when `~/.claude/settings.json` points at BigModel/Z.ai). For always-on replacement installs, use the **`skills-codex-claude-review`** or **`skills-codex-gemini-review`** overlay. Note `oracle-pro` (gpt-5.x-pro) is **also GPT family**, so it does NOT cross the family boundary for a Codex executor either.

## Default Pattern

Single-round review:

```text
spawn_agent:
  model: gpt-5.5
  reasoning_effort: xhigh
  message: |
    [role + task]
    Read the listed files directly.
```

Multi-round review:

```text
spawn_agent:
  model: gpt-5.5
  reasoning_effort: xhigh
  message: |
    [initial review prompt]
```

Save the returned reviewer id, then continue with:

```text
send_input:
  target: <saved reviewer id>
  message: |
    [follow-up materials only]
```

## Oracle Pro Override

When the user explicitly passes `--reviewer: oracle-pro`, switch only the reviewer route:

- default reviewer remains Codex xhigh if no reviewer is specified
- `oracle-pro` is optional, not the base default
- Oracle route always requests the strongest ChatGPT browser setting:
  `gpt-5.5-pro` + `Pro Extended`, with model-picker strategy `select`.

Routing rule:

```text
If reviewer is omitted or reviewer=codex:
  use spawn_agent / send_input with Codex reviewer at xhigh

If reviewer=oracle-pro:
  check Oracle MCP availability
  if available:
    call mcp__oracle__consult with:
      preset: chatgpt-pro-heavy
      engine: browser
      model: gpt-5.5-pro
      browserThinkingTime: extended
      browserModelStrategy: select
  else if the oracle CLI is available:
    call oracle --engine browser --model gpt-5.5-pro
      --browser-model-strategy select --browser-thinking-time extended
  if unavailable:
    print a clear warning
    fall back to the default Codex xhigh reviewer
```

Accept the Oracle route as true `oracle-pro` only when Oracle reports model
selection evidence with `strategy=select` and `verified=yes` (for example:
`requested=Pro; resolved=Pro Extended; status=already-selected; verified=yes`).
If selection is not verified, do not describe the response as GPT-5.5 Pro
Extended; warn and use the default Codex xhigh reviewer if a fallback is needed.

## Claude Code Reviewer Override

When the user explicitly passes `--reviewer: claude` or
`--reviewer: claude-review`, switch only the reviewer route:

- default reviewer remains Codex xhigh if no reviewer is specified
- `claude` is optional, not the base default
- the transport is `claude-review` MCP, which shells out to the local
  Claude Code CLI
- the actual reviewer model follows the user's Claude Code configuration
  unless `CLAUDE_REVIEW_MODEL` is explicitly set on the MCP server
- if Claude Code is configured to BigModel/Z.ai GLM, this route is
  effectively Codex executor + GLM reviewer

Routing rule:

```text
If reviewer is omitted or reviewer=codex:
  use spawn_agent / send_input with Codex reviewer at xhigh

If reviewer=claude or reviewer=claude-review:
  check claude-review MCP availability
  if available:
    for first-round or fresh reviews:
      call mcp__claude-review__review_start with:
        prompt: [same prompt you would send to spawn_agent]
    poll mcp__claude-review__review_status with:
        jobId: [returned jobId]
        waitSeconds: 20
      until done=true
    save the completed review thread id for follow-up rounds
    for follow-up rounds:
      call mcp__claude-review__review_reply_start with:
        thread_id: [saved completed review thread id]
        prompt: [same prompt you would send to send_input]
      poll mcp__claude-review__review_status until done=true
  if unavailable:
    print a clear warning
    fall back to the default Codex xhigh reviewer
```

Do not install the `skills-codex-claude-review` overlay if the desired behavior
is only an optional `--reviewer: claude` switch. That overlay intentionally
changes covered skills' default reviewer to Claude Code; this base route keeps
Codex as the default and only switches when explicitly requested.

### Claude Review MCP Install

```bash
mkdir -p ~/.codex/mcp-servers/claude-review
cp mcp-servers/claude-review/server.py ~/.codex/mcp-servers/claude-review/server.py
codex mcp add claude-review -- python3 ~/.codex/mcp-servers/claude-review/server.py
```

On Windows, use the Windows path and `python` if that is how Python is exposed:

```powershell
New-Item -ItemType Directory -Force $env:USERPROFILE\.codex\mcp-servers\claude-review
Copy-Item .\mcp-servers\claude-review\server.py $env:USERPROFILE\.codex\mcp-servers\claude-review\server.py
codex mcp add claude-review -- python $env:USERPROFILE\.codex\mcp-servers\claude-review\server.py
```

## Invariants

- Base skills do not use the legacy Codex MCP thread path as the default reviewer route.
- Reviewer independence still applies: pass file paths and task framing, not executor summaries.
- `--reviewer: claude` is an opt-in route in the base Codex skills; it must not
  change the default Codex reviewer route.
- Overlay packages may replace the default reviewer route, but only when the
  user intentionally installs them.
- Overlay packages do not change executor semantics.
- Browser-based Oracle review is acceptable for one-shot stress tests, not ideal for tight multi-round loops.
- `claude-review` is acceptable for multi-round loops because it exposes
  `review_start`, `review_reply_start`, and `review_status` with thread state.

## Skills That Commonly Benefit From `oracle-pro`

- `research-review`
- `auto-review-loop`
- `experiment-audit`
- `proof-checker`
- `rebuttal`
- `idea-creator`
- `research-lit`

## Skills That Commonly Benefit From `--reviewer: claude`

- `research-review`
- `auto-review-loop`
- `research-refine`
- `research-refine-pipeline`
- `experiment-audit`
- `proof-checker`
- `novelty-check`
- `idea-creator`
- `rebuttal`
