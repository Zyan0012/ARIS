# Reviewer Routing

## Default Reviewer Contract

All reviewer-heavy Codex base skills use the same default contract:

- executor: current Codex main agent
- reviewer: second Codex reviewer
- reasoning effort: `xhigh`
- round 1: `spawn_agent`
- follow-up rounds: `send_input`

This is the base default for `skills/skills-codex/`. No effort level or unrelated parameter changes it.

## Default Pattern

Single-round review:

```text
spawn_agent:
  model: gpt-5.4
  reasoning_effort: xhigh
  message: |
    [role + task]
    Read the listed files directly.
```

Multi-round review:

```text
spawn_agent:
  model: gpt-5.4
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

Routing rule:

```text
If reviewer is omitted or reviewer=codex:
  use spawn_agent / send_input with Codex reviewer at xhigh

If reviewer=oracle-pro:
  prefer the Oracle CLI browser route with selector bypass
  call oracle --engine browser --browser-model-strategy ignore
    --browser-attachments auto --browser-max-concurrent-tabs 3
    --model gpt-5.5-pro --timeout auto --heartbeat 30 --wait
    --slug <short-review-slug> --write-output <trace-response-path>
    --prompt <review prompt> --file <primary artifacts>
  if the browser route is unavailable:
    print a clear warning
    fall back to the default Codex xhigh reviewer
```

### Oracle CLI Browser Contract

In Codex on Windows/WSL, do not invoke `oracle-pro` through a bare Oracle MCP
call unless the user explicitly asks for MCP debugging. The default Oracle MCP
browser path may try to select a model in the ChatGPT UI before sending
the prompt; on some ChatGPT layouts this fails with `Unable to locate the
ChatGPT model selector button` even when the browser is already logged in.

For normal `oracle-pro` reviews, use the CLI browser route and bypass model
selection:

```bash
oracle --engine browser \
  --browser-model-strategy ignore \
  --browser-attachments auto \
  --browser-max-concurrent-tabs 3 \
  --model gpt-5.5-pro \
  --timeout auto \
  --heartbeat 30 \
  --wait \
  --slug "<short-review-slug>" \
  --write-output ".aris/traces/<skill>/<run>/<NNN>-oracle-pro.response.md" \
  --prompt "<review prompt>" \
  --file <paper-or-project-files>
```

Operational rules:

- Do not probe the API route first when `OPENAI_API_KEY` is absent or when the
  user requested ChatGPT Pro/browser review. A missing API key is not a browser
  failure.
- `--browser-model-strategy ignore` means Oracle uses the model already active
  in the logged-in ChatGPT page. The user should keep that page on the intended
  Pro model; the `--model gpt-5.5-pro` value remains useful for session metadata
  and non-browser fallbacks.
- Keep `--browser-attachments auto` as the default. Oracle pastes small text
  bundles inline, uploads larger bundles/files, and keeps a fallback upload plan
  when inline submission fails. Use `always` only when PDFs/images must be real
  ChatGPT attachments; use `never` only for explicitly inline-only text runs.
- Oracle 0.11+ coordinates concurrent ChatGPT browser runs with a tab lease
  registry. The default soft limit is 3 tabs for one manual-login profile; keep
  `--browser-max-concurrent-tabs 3` explicit in ARIS docs so the concurrency
  expectation is visible.
- Oracle 0.11+ requires Node 24+. Verify with `node --version` and `oracle
  --version` before debugging ARIS routing.
- Run `oracle --dry-run summary ...` before the first live review after an
  Oracle upgrade; dry runs validate flags and file handling without touching the
  browser.
- Long Pro wait is normal. Browser GPT-5.5 Pro reviews can take 10-60 minutes
  and may emit no final answer for a long stretch. Do not treat silence, a live
  Chrome window, or a Codex shell timeout as failure.
- When launching Oracle from Codex tools, set the shell/tool timeout to at least
  65 minutes for live Pro reviews. If the tool call itself times out, do not
  rerun the prompt and do not fall back yet; inspect `oracle status --hours 4
  --limit 20`, then reattach with `oracle session <slug> --live --write-output
  <trace-response-path>` or harvest with `oracle session <slug> --harvest
  --write-output <trace-response-path>`.
- Only mark Oracle unavailable after Oracle records a terminal error for that
  slug and no model is still running. A session with status `running` or an
  incomplete browser response is pending, not failed.
- In WSL, first verify `oracle bridge doctor` passes. If it fails, fix the
  bridge/login/token issue before starting the review.
- Use `--force` only when Oracle's duplicate-prompt guard blocks an intentional
  rerun; do not use it as the default.
- `oracle-safe` remains available as a conservative fallback for older Oracle
  versions or a temporarily flaky browser profile, but it serializes all browser
  runs and should not be the default when throughput matters.

## Invariants

- Base skills do not use the legacy Codex MCP thread path as the default reviewer route.
- Reviewer independence still applies: pass file paths and task framing, not executor summaries.
- Overlay packages may replace only the reviewer route.
- Overlay packages do not change executor semantics.
- Browser-based Oracle review is acceptable for one-shot stress tests, not ideal for tight multi-round loops.

## Skills That Commonly Benefit From `oracle-pro`

- `research-review`
- `auto-review-loop`
- `experiment-audit`
- `proof-checker`
- `rebuttal`
- `idea-creator`
- `research-lit`
