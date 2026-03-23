---
name: research-lit
description: Search and analyze research papers, find related work, and summarize the literature landscape. Use when the user asks for papers, related work, literature review, academic search, or to understand what work already exists on a research topic.
argument-hint: [paper-topic-or-url]
allowed-tools: Bash(*), Read, Glob, Grep, WebSearch, WebFetch, Write, Agent, mcp__zotero__*, mcp__obsidian-vault__*
---

# Research Literature Review

Research topic: $ARGUMENTS

## Goal

Build a compact but decision-useful literature review by combining:
- the user's existing library and notes
- structured arXiv search
- a local Semantic Scholar skill when available
- general web search as fallback and coverage expansion

The output should help downstream skills such as `/idea-discovery`, `/research-refine`, `/experiment-plan`, and `/paper-writing`.

## Constants

- **PAPER_LIBRARY**: Check these paths in order:
  1. `papers/` in the current project
  2. `literature/` in the current project
  3. custom path from `CLAUDE.md` under `## Paper Library`
- **MAX_LOCAL_PAPERS = 20**: Maximum local PDFs to inspect.
- **ARXIV_DOWNLOAD = false**: Download arXiv PDFs only when explicitly requested.
- **ARXIV_MAX_DOWNLOAD = 5**
- **SEMANTIC_QUERY_VARIANTS = 3**: Generate 2-4 English Semantic Scholar queries, default 3.
- **SEMANTIC_LIMIT_PER_QUERY = 8**

## Source Selection

Parse `$ARGUMENTS` for an optional `sources:` directive.

Valid values:
- `zotero`
- `obsidian`
- `local`
- `semantic`
- `web`
- `all`

If no directive is provided, use `all`.

Examples:

```text
/research-lit "diffusion models"
/research-lit "diffusion models - sources: semantic, web"
/research-lit "offline RL - sources: local, semantic"
```

## Source Priority

1. Zotero
2. Obsidian
3. Local PDFs
4. Local Semantic Scholar skill
5. arXiv
6. Web search

Each source is optional. Skip missing sources silently and continue.

## Semantic Scholar Local Skill

Before generic web search, check whether a local Semantic Scholar executor exists.

Preferred lookup order:

```bash
~/.codex/skills/semantic-scholar-search/scripts/search.py
/mnt/c/Users/MX/.codex/skills/semantic-scholar-search/scripts/search.py
```

Use the WSL-local path first when it exists.

Only fall back to the Windows-mounted path when the WSL-local copy is missing.

### Important rule

Do not send the raw user topic directly into keyword extraction when the request is rich or multilingual.

Instead:
1. Read the user's request.
2. Rewrite it into a compact English search brief.
3. Produce 2-4 precise English query strings.
4. Run `search.py direct` for each query.
5. Merge and deduplicate results by `paperId`.

### Search brief template

Always write a short internal brief with:
- topic
- must-have concepts
- optional concepts
- exclusions
- time range
- 2-4 English query strings

### Query rules

- Prefer one quoted topic phrase plus 1-3 supporting tokens.
- Avoid dumping a bag of generic keywords.
- Start narrow. Broaden only if recall is weak.

Good:

```text
"multimodal fake news detection" +calibration +uncertainty
```

Bad:

```text
multimodal fake news detection model method analysis evaluation uncertainty calibration approach
```

### Example command

```bash
python3 "$SEMANTIC_SCRIPT" direct \
  --query '"multimodal fake news detection" +calibration +uncertainty' \
  --keywords 'multimodal fake news detection, calibration, uncertainty, decision-aware evaluation' \
  --year '2021-' \
  --limit 8 \
  --details 3 \
  --no-save
```

Use the printed paper metadata as structured evidence for the literature review.

## Workflow

### Step 0a: Search Zotero

If Zotero MCP is available:
- search by topic
- inspect relevant collections
- extract annotations and notes
- collect citation metadata

These annotations are high-value signals because they reflect what the user already found important.

### Step 0b: Search Obsidian

If Obsidian MCP is available:
- search notes related to the topic
- inspect tags and linked notes
- extract the user's summaries, critiques, and open questions

### Step 0c: Scan local PDFs

- inspect `papers/**/*.pdf` and `literature/**/*.pdf`
- de-duplicate against Zotero results
- read the first 3 pages of the most relevant PDFs
- record title, year, core contribution, and why it matters

### Step 1: Structured external search

#### Step 1a: Semantic Scholar local skill

If the local Semantic Scholar script exists and `sources` includes `semantic` or `all`:

1. Generate an English search brief.
2. Produce 2-4 direct query variants.
3. Run `search.py direct` for each query.
4. Merge and deduplicate by `paperId`.
5. Keep the most relevant 8-15 papers.

If direct mode returns weak recall, optionally run one `progressive` search as a second pass.

#### Step 1b: arXiv API

Try to locate `arxiv_fetch.py`:

```bash
SCRIPT=$(find tools/ -name "arxiv_fetch.py" 2>/dev/null | head -1)
[ -z "$SCRIPT" ] && SCRIPT=$(find ~/.claude/skills/arxiv/ -name "arxiv_fetch.py" 2>/dev/null | head -1)
```

If found:

```bash
python3 "$SCRIPT" search "QUERY" --max 10
```

Use arXiv for high-recall preprint discovery and recent work.

If `ARXIV_DOWNLOAD = true`, download only the top relevant papers not already in the local library.

#### Step 1c: Web search fallback

Use WebSearch and WebFetch to cover:
- project pages
- Google Scholar snippets
- venue pages
- papers missed by Semantic Scholar and arXiv

Do not rely on generic web search alone if the Semantic Scholar local skill is available.

### Step 2: Analyze each paper

For each relevant paper, extract:
- problem
- method
- key result
- relevance to our work
- source

### Step 3: Synthesize

Group papers by:
- approach
- benchmark or task
- evaluation philosophy
- major disagreement or limitation

Explicitly identify:
- what is already well covered
- what remains missing
- what claims would be hard to defend given the current literature

### Step 4: Output

Always produce a structured table:

```text
| Paper | Venue | Method | Key Result | Relevance to Us | Source |
|-------|-------|--------|------------|-----------------|--------|
```

Then add a short narrative synthesis in 3-5 paragraphs:
- landscape overview
- dominant clusters of work
- unresolved gaps
- concrete implications for the user's project

If BibTeX is available from Zotero, include a short `references.bib` snippet.

### Step 5: Save if requested

Optionally:
- save downloaded PDFs into `papers/` or `literature/`
- write notes into project memory
- create or update an Obsidian literature note when available

## Key Rules

- Always include authors, year, and venue when available.
- Distinguish peer-reviewed papers from preprints.
- De-duplicate aggressively across all sources.
- Prefer precise structured search over broad keyword stuffing.
- When Semantic Scholar local skill is available, use `direct` mode first.
- Use raw keyword extraction only as fallback or debugging.
- Never fail because a source is missing; continue with the remaining sources.
