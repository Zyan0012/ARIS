---
name: "research-review"
description: "Get a deep critical review of research from GPT using a secondary Codex agent by default, Oracle Pro, or Claude Code/GLM when explicitly requested. Use when user says \"review my research\", \"help me review\", \"get external review\", or wants critical feedback on research ideas, papers, or experimental results."
---

# Research Review via Codex Reviewer, Oracle Pro, or Claude Code

Get a multi-round critical review of research work from an external LLM with maximum reasoning depth.

## Constants

- REVIEWER_MODEL = `gpt-5.5` — Model used via a secondary Codex agent. Must be an OpenAI model (e.g., `gpt-5.5`, `o3`, `gpt-4o`)
- **REVIEWER_BACKEND = `codex`** — Default: Codex xhigh reviewer. Use `--reviewer: oracle-pro` only when explicitly requested; route that through Oracle MCP using the strongest ChatGPT browser setting (`gpt-5.5-pro` + Pro Extended, `browserModelStrategy: select`) when the tool is exposed in the active session. Use `--reviewer: claude` only when explicitly requested; route that through `claude-review` MCP, which calls the local Claude Code CLI and therefore uses whatever model Claude Code is configured for (GLM on BigModel/Z.ai setups). If an optional reviewer is unavailable, warn and fall back to Codex xhigh. **Same-family note:** the default reviewer is a second Codex/GPT agent — valid for Type-A completeness/drive review, but not a cross-family Type-B verdict. `--reviewer: claude` can satisfy Type-B only when the local Claude Code backend is a non-GPT family model.

## Context: $ARGUMENTS

## Prerequisites

- Use `spawn_agent` and `send_input` when the user has explicitly allowed delegation or subagents.
- If delegation is not allowed, run the same review loop locally and preserve the same deliverable structure.
- For `--reviewer: oracle-pro`, first check whether `mcp__oracle__consult` is present in the active tool list. If it is missing, the current Codex session likely started before Oracle MCP was registered; print a clear warning and use the default Codex xhigh route for this run.
- For `--reviewer: claude` / `--reviewer: claude-review`, first check whether `mcp__claude-review__review_start`, `mcp__claude-review__review_reply_start`, and `mcp__claude-review__review_status` are present in the active tool list. If they are missing, the current Codex session likely started before `claude-review` MCP was registered; print a clear warning and use the default Codex xhigh route for this run.

## Reviewer Routing

Before Step 2, parse `$ARGUMENTS` for `--reviewer: oracle-pro`, `reviewer: oracle-pro`, `--reviewer: claude`, `reviewer: claude`, `--reviewer: claude-review`, or `reviewer: claude-review`.

If `oracle-pro` is explicitly requested and `mcp__oracle__consult` is available, use Oracle MCP for reviewer calls:

```text
mcp__oracle__consult:
  preset: "chatgpt-pro-heavy"
  engine: "browser"
  model: "gpt-5.5-pro"
  browserThinkingTime: "extended"
  browserModelStrategy: "select"
  files:
    - /absolute/path/to/paper-or-report
    - /absolute/path/to/key-evidence
  prompt: |
    Read the attached/listed files directly.
    Act as a senior ML reviewer and produce the requested strict review.
```

If `oracle-pro` is requested but `mcp__oracle__consult` is not available, print exactly which route is unavailable, then fall back to `spawn_agent` with `reasoning_effort: xhigh`. Do not describe that fallback as a true Oracle Pro review.

If `claude` / `claude-review` is explicitly requested and the `claude-review` MCP tools are available, use async Claude review calls:

```text
mcp__claude-review__review_start:
  prompt: |
    [same full prompt you would send to spawn_agent]

mcp__claude-review__review_status:
  jobId: [returned jobId]
  waitSeconds: 20
```

Poll `review_status` until `done=true`. Treat the completed status payload's `response` as the reviewer output and save the completed review thread id for follow-up rounds.

If `claude` / `claude-review` is requested but the MCP tools are not available, print exactly which route is unavailable, then fall back to `spawn_agent` with `reasoning_effort: xhigh`. Do not describe that fallback as a true Claude/GLM review.

## Workflow

### Step 1: Gather Research Context
Before calling the external reviewer, compile a comprehensive briefing:
1. Read project narrative documents (e.g., STORY.md, README.md, paper drafts)
2. Read any memory/notes files for key findings and experiment history
3. Identify: core claims, methodology, key results, known weaknesses

### Step 2: Initial Review (Round 1)
Send a detailed prompt with the selected reviewer route.

For `oracle-pro`, call Oracle MCP:

```text
mcp__oracle__consult:
  preset: "chatgpt-pro-heavy"
  engine: "browser"
  model: "gpt-5.5-pro"
  browserThinkingTime: "extended"
  browserModelStrategy: "select"
  files:
    - /absolute/path/to/primary-paper-or-report
    - /absolute/path/to/key-evidence
  prompt: |
    [Full research context + specific questions]
    Please act as a senior ML reviewer (NeurIPS/ICML level). Identify:
    1. Logical gaps or unjustified claims
    2. Missing experiments that would strengthen the story
    3. Narrative weaknesses
    4. Whether the contribution is sufficient for a top venue
    Please be brutally honest.
```

For `claude` / `claude-review`, call `claude-review` MCP asynchronously:

```text
mcp__claude-review__review_start:
  prompt: |
    [Full research context + specific questions]
    Please act as a senior ML reviewer (NeurIPS/ICML level). Identify:
    1. Logical gaps or unjustified claims
    2. Missing experiments that would strengthen the story
    3. Narrative weaknesses
    4. Whether the contribution is sufficient for a top venue
    Please be brutally honest.

mcp__claude-review__review_status:
  jobId: [returned jobId]
  waitSeconds: 20
```

Poll until `done=true`, save the completed review thread id, and record the actual route as `claude-review`.

For the default Codex route, use xhigh reasoning:

```
spawn_agent:
  reasoning_effort: xhigh
  message: |
    [Full research context + specific questions]
    Please act as a senior ML reviewer (NeurIPS/ICML level). Identify:
    1. Logical gaps or unjustified claims
    2. Missing experiments that would strengthen the story
    3. Narrative weaknesses
    4. Whether the contribution is sufficient for a top venue
    Please be brutally honest.
```

### Step 3: Iterative Dialogue (Rounds 2-N)
Use the selected reviewer route to continue the conversation.

For `oracle-pro`, make a fresh `mcp__oracle__consult` call for each follow-up round. Include the prior Oracle response, unresolved issues, and revised files in the prompt/files list. Do not claim same-thread continuity unless the active Oracle tool explicitly exposes it.

For `claude` / `claude-review`, continue with the saved completed review thread id:

```text
mcp__claude-review__review_reply_start:
  thread_id: [saved completed review thread id from Step 2]
  prompt: |
    Please continue the review using the revised materials below.

    Revised files:
    - /absolute/path/to/file1
    - /absolute/path/to/file2

    Focus on unresolved weaknesses and whether the revision actually fixed them.

mcp__claude-review__review_status:
  jobId: [returned jobId]
  waitSeconds: 20
```

Poll until `done=true` and use the completed status payload's `response` as the reviewer output.

For the default Codex route, use `send_input` with the returned agent id:

```text
send_input:
  target: [saved reviewer id from Step 2]
  message: |
    Please continue the review using the revised materials below.

    Revised files:
    - /absolute/path/to/file1
    - /absolute/path/to/file2

    Focus on unresolved weaknesses and whether the revision actually fixed them.
```

For each round:
1. **Respond** to criticisms with evidence/counterarguments
2. **Ask targeted follow-ups** on the most actionable points
3. **Request specific deliverables**: experiment designs, paper outlines, claims matrices

Key follow-up patterns:
- "If we reframe X as Y, does that change your assessment?"
- "What's the minimum experiment to satisfy concern Z?"
- "Please design the minimal additional experiment package (highest acceptance lift per GPU week)"
- "Please write a mock NeurIPS/ICML review with scores"
- "Give me a results-to-claims matrix for possible experimental outcomes"

### Step 4: Convergence
Stop iterating when:
- Both sides agree on the core claims and their evidence requirements
- A concrete experiment plan is established
- The narrative structure is settled

### Step 5: Document Everything
Save the full interaction and conclusions to a review document in the project root:
- Round-by-round summary of criticisms and responses
- Final consensus on claims, narrative, and experiments
- Claims matrix (what claims are allowed under each possible outcome)
- Prioritized TODO list with estimated compute costs
- Paper outline if discussed

Update project memory/notes with key review conclusions.

### Step 6: Review Tracing

Save a trace for every `spawn_agent`, `send_input`, `oracle-pro`, or `claude-review` review call following `../shared-references/review-tracing.md`. Record the reviewer route, saved agent/thread/job id, prompt summary, raw response path, decisions, and action items. This preserves the Claude mainline Review Tracing semantics while using Codex-native reviewer calls.

## Key Rules

- ALWAYS use `reasoning_effort: xhigh` for Codex reviewer calls. Oracle Pro uses the browser route `gpt-5.5-pro` + `browserThinkingTime: extended`; there is no Codex `effort` knob in the MCP call.
- Claude review uses `claude-review` MCP and does not take Codex `reasoning_effort`; its actual model and thinking behavior come from the local Claude Code configuration.
- Send comprehensive context in Round 1 — the external model cannot read your files
- Be honest about weaknesses — hiding them leads to worse feedback
- Push back on criticisms you disagree with, but accept valid ones
- Focus on ACTIONABLE feedback — "what experiment would fix this?"
- Document the Codex agent id or Claude completed review thread id for potential future resumption
- When `--reviewer: oracle-pro` was requested, record whether the actual route was `oracle-pro` or `codex-fallback`.
- When `--reviewer: claude` was requested, record whether the actual route was `claude-review` or `codex-fallback`, and record the local Claude Code backend/model if the bridge reports it.
- For true Oracle Pro, record Oracle's model selection evidence; require `strategy=select` and `verified=yes` before calling it Pro Extended.
- The review document should be self-contained (readable without the conversation)

## Prompt Templates

### For initial review:
"I'm going to present a complete ML research project for your critical review. Please act as a senior ML reviewer (NeurIPS/ICML level)..."

### For experiment design:
"Please design the minimal additional experiment package that gives the highest acceptance lift per GPU week. Our compute: [describe]. Be very specific about configurations."

### For paper structure:
"Please turn this into a concrete paper outline with section-by-section claims and figure plan."

### For claims matrix:
"Please give me a results-to-claims matrix: what claim is allowed under each possible outcome of experiments X and Y?"

### For mock review:
"Please write a mock NeurIPS review with: Summary, Strengths, Weaknesses, Questions for Authors, Score, Confidence, and What Would Move Toward Accept."
