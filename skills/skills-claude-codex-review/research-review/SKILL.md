---
name: research-review
description: "Get a deep critical review of research from GPT via codex-review MCP. Use when user says \\\"review my research\\\", \\\"help me review\\\", \\\"get external review\\\", or wants critical feedback on research ideas, papers, or experimental results."
argument-hint: [topic-or-scope]
allowed-tools: Bash(*), Read, Grep, Glob, Write, Edit, Agent, mcp__codex-review__review, mcp__codex-review__review_reply, mcp__codex-review__review_start, mcp__codex-review__review_reply_start, mcp__codex-review__review_status
---

> Override for Claude Code users who want long Codex reviews to run through the local `codex-review` MCP bridge with async polling instead of direct synchronous `codex` MCP calls. Install this package **after** `skills/*`.

# Research Review via `codex-review` MCP (xhigh reasoning)

Get a multi-round critical review of research work from an external LLM with maximum reasoning depth.

## Constants

- REVIEWER_MODEL = `gpt-5.4` — Model used via `codex-review` MCP. Must be an OpenAI model (e.g., `gpt-5.4`, `o3`, `gpt-4o`)

## Context: $ARGUMENTS

## Prerequisites

- Install the base Claude Code skills first: copy `skills/*` into `~/.claude/skills/`.
- Then install this overlay package: copy `skills/skills-claude-codex-review/*` into `~/.claude/skills/` and allow it to overwrite the same skill names.
- Register the local reviewer bridge:
  ```bash
  mkdir -p ~/.claude/mcp-servers/codex-review
  cp mcp-servers/codex-review/server.py ~/.claude/mcp-servers/codex-review/server.py
  claude mcp add codex-review -s user -- python3 ~/.claude/mcp-servers/codex-review/server.py
  ```
- This gives Claude Code access to `mcp__codex-review__review`, `mcp__codex-review__review_reply`, `mcp__codex-review__review_start`, `mcp__codex-review__review_reply_start`, and `mcp__codex-review__review_status`.


## Workflow

### Step 1: Gather Research Context
Before calling the external reviewer, compile a comprehensive briefing:
1. Read project narrative documents (e.g., STORY.md, README.md, paper drafts)
2. Read any memory/notes files for key findings and experiment history
3. Identify: core claims, methodology, key results, known weaknesses

### Step 2: Initial Review (Round 1)
Send a detailed prompt with xhigh reasoning:

```
mcp__codex-review__review_start:
  prompt: |
    [Full research context + specific questions]
    Please act as a senior ML reviewer (NeurIPS/ICML level). Identify:
    1. Logical gaps or unjustified claims
    2. Missing experiments that would strengthen the story
    3. Narrative weaknesses
    4. Whether the contribution is sufficient for a top venue
    Please be brutally honest.
```

After this start call, immediately save the returned `jobId` and poll `mcp__codex-review__review_status` with a bounded `waitSeconds` until `done=true`. Treat the completed status payload's `response` as the reviewer output, and save the completed `threadId` for any follow-up round.

### Step 3: Iterative Dialogue (Rounds 2-N)
Use `mcp__codex-review__review_reply_start` with the saved completed `threadId`, then poll `mcp__codex-review__review_status` with the returned `jobId` until `done=true` to continue the conversation:

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

## Key Rules

- Always ask the Codex reviewer for strict, high-rigor feedback. Override `CODEX_REVIEW_MODEL` when your provider requires a specific supported model.
- Send comprehensive context in Round 1 — the external model cannot read your files
- Be honest about weaknesses — hiding them leads to worse feedback
- Push back on criticisms you disagree with, but accept valid ones
- Focus on ACTIONABLE feedback — "what experiment would fix this?"
- Document the threadId for potential future resumption
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
