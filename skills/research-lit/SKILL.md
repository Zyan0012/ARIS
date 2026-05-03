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
- optional Semantic Scholar API search for venue-only papers
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

Overrides:
- `/research-lit "topic" - paper library: ~/my_papers/` -> custom local PDF path
- `/research-lit "topic" - sources: zotero, local` -> only search Zotero plus local PDFs
- `/research-lit "topic" - sources: zotero` -> only search Zotero
- `/research-lit "topic" - sources: web` -> only search the web
- `/research-lit "topic" - sources: web, semantic` -> web plus local Semantic Scholar skill
- `/research-lit "topic" - sources: web, semantic-scholar` -> web plus Semantic Scholar API
- `/research-lit "topic" - arxiv download: true` -> download top relevant arXiv PDFs
- `/research-lit "topic" - arxiv download: true, max download: 10` -> download up to 10 PDFs

Parse `$ARGUMENTS` for an optional `sources:` directive.

Valid values:
- `zotero`
- `obsidian`
- `local`
- `semantic`
- `semantic-scholar`
- `deepxiv`
- `exa`
- `gemini`
- `openalex`
- `web`
- `all`

If no directive is provided, use `all`.

Interpretation:
- `all` means search the default sources: Zotero, Obsidian, local PDFs, local Semantic Scholar skill, arXiv, and web.
- `semantic-scholar`, `deepxiv`, `exa`, `gemini`, and `openalex` are opt-in expansion sources. Add them explicitly, for example `sources: all, gemini`.
- `semantic` means the local Semantic Scholar skill/script, while `semantic-scholar` means the repo's `semantic_scholar_fetch.py` API path for venue-only papers beyond arXiv.

Examples:

```text
/research-lit "diffusion models"
/research-lit "diffusion models - sources: semantic, web"
/research-lit "offline RL - sources: local, semantic"
/research-lit "multimodal misinformation - sources: web, semantic-scholar"
/research-lit "representation learning - sources: all, semantic-scholar"
/research-lit "topic" - sources: deepxiv
/research-lit "topic" - sources: all, deepxiv
/research-lit "topic" - sources: exa
/research-lit "topic" - sources: all, exa
/research-lit "topic" - sources: gemini
/research-lit "topic" - sources: all, gemini
/research-lit "topic" - sources: openalex
/research-lit "topic" - sources: semantic-scholar, openalex
```

## Source Priority

| Priority | Source | ID | How to detect | What it provides |
|----------|--------|----|---------------|-----------------|
| 1 | Zotero (via MCP) | `zotero` | Try calling any `mcp__zotero__*` tool; if unavailable, skip | Collections, tags, annotations, PDF highlights, BibTeX, semantic search |
| 2 | Obsidian (via MCP) | `obsidian` | Try calling any `mcp__obsidian-vault__*` tool; if unavailable, skip | Research notes, paper summaries, tagged references, wikilinks |
| 3 | Local PDFs | `local` | `Glob: papers/**/*.pdf, literature/**/*.pdf` | Raw PDF content (first 3 pages) |
| 4 | Local Semantic Scholar skill | `semantic` | `~/.codex/skills/semantic-scholar-search/scripts/search.py` or Windows-mounted fallback exists | Direct Semantic Scholar retrieval tuned for the local workflow |
| 5 | arXiv API | implicit | `arxiv_fetch.py` exists | Structured preprint search and optional PDF download |
| 6 | Web search | `web` | Always available | arXiv, project pages, Google Scholar snippets, venue pages |
| 7 | Semantic Scholar API | `semantic-scholar` | `tools/semantic_scholar_fetch.py` exists | Published venue papers with citation counts, venue metadata, and TLDR; runs only when explicitly requested |
| 8 | DeepXiv CLI | `deepxiv` | `tools/deepxiv_fetch.py` and installed `deepxiv` CLI | Progressive paper retrieval: search, brief, head, section, trending, web search; runs only when explicitly requested |
| 9 | Exa Search | `exa` | `tools/exa_search.py` and installed `exa-py` SDK | AI-powered broad web search with content extraction; runs only when explicitly requested |
| 10 | Gemini (MCP / CLI) | `gemini` | `mcp__gemini-cli__ask-gemini` tool available, or `gemini` CLI installed | AI-powered broad literature discovery; runs only when explicitly requested |
| 11 | OpenAlex | `openalex` | `tools/openalex_fetch.py` exists | Open citation graph with institutional affiliations, funding data, and broad metadata; runs only when explicitly requested |

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

### Important Rule

Do not send the raw user topic directly into keyword extraction when the request is rich or multilingual.

Instead:
1. Read the user's request.
2. Rewrite it into a compact English search brief.
3. Produce 2-4 precise English query strings.
4. Run `search.py direct` for each query.
5. Merge and deduplicate results by `paperId`.

### Search Brief Template

Always write a short internal brief with:
- topic
- must-have concepts
- optional concepts
- exclusions
- time range
- 2-4 English query strings

### Query Rules

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

### Example Command

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

### Step 0c: Scan Local PDFs

- inspect `papers/**/*.pdf` and `literature/**/*.pdf`
- de-duplicate against Zotero results
- read the first 3 pages of the most relevant PDFs
- record title, year, core contribution, and why it matters

### Step 1: Structured External Search

#### Step 1a: Semantic Scholar Local Skill

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

#### Step 1c: Semantic Scholar API Search

If `sources` explicitly includes `semantic-scholar`, search the repo's Semantic Scholar API path for published venue papers beyond arXiv:

```bash
S2_SCRIPT=$(find tools/ -name "semantic_scholar_fetch.py" 2>/dev/null | head -1)
[ -z "$S2_SCRIPT" ] && S2_SCRIPT=$(find ~/.claude/skills/semantic-scholar/ -name "semantic_scholar_fetch.py" 2>/dev/null | head -1)

python3 "$S2_SCRIPT" search "QUERY" --max 10 \
  --fields-of-study "Computer Science,Engineering" \
  --publication-types "JournalArticle,Conference"
```

If `semantic_scholar_fetch.py` is not found, skip silently.

Use this source when you specifically want:
- IEEE / ACM / Springer papers not mirrored on arXiv
- citation counts and venue metadata
- DOI and publication venue disambiguation

De-duplicate against arXiv by `externalIds.ArXiv` when present:
- if the S2 result has a real publication venue, prefer its venue metadata
- if it is only a mirrored preprint, keep the arXiv version as primary
- S2 entries without arXiv IDs are usually the unique venue-only hits

#### Step 1d: Web Search Fallback

Use WebSearch and WebFetch to cover:
- project pages
- Google Scholar snippets
- venue pages
- papers missed by Semantic Scholar and arXiv

Do not rely on generic web search alone if the local Semantic Scholar skill is available.

#### Step 1e: DeepXiv Search

```bash
python3 tools/deepxiv_fetch.py search "QUERY" --max 10
```

Then deepen only for the most relevant papers:

```bash
python3 tools/deepxiv_fetch.py paper-brief ARXIV_ID
python3 tools/deepxiv_fetch.py paper-head ARXIV_ID
python3 tools/deepxiv_fetch.py paper-section ARXIV_ID "Experiments"
```

If `tools/deepxiv_fetch.py` or the `deepxiv` CLI is unavailable, skip this source gracefully and continue with the remaining requested sources.

**Why use DeepXiv?** It is useful when a broad search should be followed by staged reading rather than immediate full-paper loading. This reduces unnecessary context while still surfacing structure, TLDRs, and the most relevant sections.

**De-duplication against arXiv and S2**:
- Match by arXiv ID first, DOI second, normalized title third
- If DeepXiv and arXiv refer to the same preprint, keep one canonical paper row and record `deepxiv` as an additional source
- If DeepXiv overlaps with S2 on a published paper, prefer S2 venue/citation metadata in the final table, but keep DeepXiv-derived section notes when they add value

**Exa search** (only when `exa` is in sources):

When the user explicitly requests `— sources: exa` (or includes `exa` in a combined source list), use the Exa tool for broad AI-powered web search with content extraction:

```bash
EXA_SCRIPT=$(find tools/ -name "exa_search.py" 2>/dev/null | head -1)

# Search for research papers with highlights
python3 "$EXA_SCRIPT" search "QUERY" --max 10 --category "research paper" --content highlights

# Search for broader web content (blogs, docs, news)
python3 "$EXA_SCRIPT" search "QUERY" --max 10 --content highlights
```

If `tools/exa_search.py` or the `exa-py` SDK is unavailable, skip this source gracefully and continue with the remaining requested sources.

**Why use Exa?** Exa provides AI-powered search across the broader web (blogs, documentation, news, company pages) with built-in content extraction. It fills a gap between academic databases (arXiv, S2) and generic WebSearch by returning richer content with each result.

**De-duplication against arXiv, S2, and DeepXiv**:
- Match by URL first, then normalized title
- If Exa returns an arXiv paper already found by arXiv/S2, prefer the structured metadata from those sources
- Exa results from non-academic domains (blogs, docs, news) are unique value not covered by other sources

**Gemini search** (only when `gemini` is in sources):

When the user explicitly requests `— sources: gemini` (or includes `gemini` in a combined source list), use Gemini for AI-powered broad literature discovery.

**Priority 1 — Gemini MCP** (preferred): Call `mcp__gemini-cli__ask-gemini` with the search prompt:

```
mcp__gemini-cli__ask-gemini({
  prompt: 'You are a research literature scout. Search comprehensively for papers on: "QUERY"

IMPORTANT CONSTRAINTS:
1. Search from MULTIPLE angles — decompose the topic into sub-problems, aliases, neighboring tasks, and common benchmark/settings variants.
2. Prefer papers that are genuinely relevant, not merely keyword-adjacent.
3. Include top venues, journals, surveys, recent preprints, and papers with code when available.
4. Focus on papers from 2022 onward unless older foundational work is necessary.

For EACH paper found, provide ALL of the following:
- Title: [exact title]
- Authors: [full author list]
- Year: [publication year]
- Venue: [exact conference/journal name + year, or "arXiv preprint"]
- arXiv ID: [format 2401.12345, or "N/A"]
- DOI: [if available, or "N/A"]
- Code URL: [GitHub/GitLab link if available, or "No code"]
- Summary: [one-sentence core contribution]

Find at least 15 papers.',
  model: 'gemini-2.5-pro'
})
```

**Priority 2 — Gemini CLI fallback** (if MCP unavailable): Use `gemini -p "...same prompt..." 2>/dev/null` via Bash (timeout: 120s).

If both MCP and CLI are unavailable, skip this source gracefully and continue with the remaining requested sources.

**Why use Gemini?** Gemini provides AI-driven discovery that goes beyond keyword matching — it decomposes topics, explores naming variants, and surfaces papers that traditional API-based searches (arXiv, S2) may miss. It fills a different retrieval niche from structured database queries.

**De-duplication against arXiv, S2, DeepXiv, and Exa**:
- Match by arXiv ID first, DOI second, normalized title third
- If Gemini returns a paper already found by S2, prefer S2's citation count and venue metadata
- If Gemini returns a paper already found by arXiv, prefer arXiv's structured metadata
- Gemini's unique value is discovering papers that other keyword-based indexes did not surface
- **Do not use Gemini-reported citation counts** — they may be inaccurate. Use S2 for authoritative citation data.

**OpenAlex search** (only when `openalex` is in sources):

When the user explicitly requests `— sources: openalex` (or includes `openalex` in a combined source list), use OpenAlex API for comprehensive academic metadata:

```bash
OA_SCRIPT=$(find tools/ -name "openalex_fetch.py" 2>/dev/null | head -1)

# Preflight: skip OpenAlex silently if either openalex_fetch.py or the
# `requests` Python package is unavailable. Both checks must pass before
# the script is invoked, so users without `requests` installed never see
# a stack trace from a default `/research-lit` run.
if [ -z "$OA_SCRIPT" ] || ! python3 -c "import requests" >/dev/null 2>&1; then
  echo "OpenAlex source not available (missing tools/openalex_fetch.py or 'requests' module); skipping." >&2
else
  # Search for papers with comprehensive metadata
  python3 "$OA_SCRIPT" search "QUERY" --max 10 \
    --year "2022-" \
    --type article \
    --sort relevance
fi
```

If `openalex_fetch.py` is not found or `requests` module is missing, skip this source gracefully and continue with the remaining requested sources.

**Why use OpenAlex?** Fully open citation graph (no API key required), institutional affiliations, funding data (NSF, NIH), comprehensive topic/keyword metadata, and coverage across all disciplines (not just CS).

**De-duplication against arXiv, S2, DeepXiv, Exa, and Gemini**:
- Match by DOI first (OpenAlex has DOI for most works), then arXiv ID, then normalized title
- If OpenAlex and S2 both have the same paper:
  - Prefer S2 for citation counts (more up-to-date)
  - Prefer S2 for venue metadata (more accurate for CS/AI papers)
  - Use OpenAlex for institutional affiliations and funding data (unique value)
  - Merge both into a richer record
- If OpenAlex and arXiv overlap, prefer arXiv's PDF link and metadata, but keep OpenAlex's citation/institution data
- OpenAlex's unique value: institutional affiliations, funding sources, comprehensive topic classification, and cross-discipline coverage

**Optional PDF download** (only when `ARXIV_DOWNLOAD = true`):

After all sources are searched and papers are ranked by relevance:
```bash
# Download top N most relevant arXiv papers
python3 "$SCRIPT" download ARXIV_ID --dir papers/
```
- Only download papers ranked in the top ARXIV_MAX_DOWNLOAD by relevance
- Skip papers already in the local library
- 1-second delay between downloads (rate limiting)
- Verify each PDF > 10 KB

### Step 2: Analyze Each Paper

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

### Step 5: Save if Requested

Optionally:
- save downloaded PDFs into `papers/` or `literature/`
- write notes into project memory
- create or update an Obsidian literature note when available

### Step 6: Update Research Wiki

**Required when `research-wiki/` exists.** Skip entirely (no action, no
error) if the directory is absent. Per
[`shared-references/integration-contract.md`](../shared-references/integration-contract.md),
this step follows the canonical ingest contract — business logic lives
in `tools/research_wiki.py`, not in this prose.

```
📋 Research Wiki ingest (runs once, at end of research-lit):
   [ ] 1. Predicate: `research-wiki/` exists? If no, skip this step.
   [ ] 2. For each of the top 8–12 relevant papers (arxiv IDs collected above):
          python3 tools/research_wiki.py ingest_paper research-wiki/ \
              --arxiv-id <id> [--thesis "<one-line>"] [--tags <t1>,<t2>]
   [ ] 3. For each explicit relationship to an existing wiki entity,
          add an edge:
          python3 tools/research_wiki.py add_edge research-wiki/ \
              --from "paper:<slug>" --to "<target_node_id>" \
              --type <extends|contradicts|addresses_gap|inspired_by|...> \
              --evidence "<one-sentence quote or reasoning>"
   [ ] 4. Confirm papers/<slug>.md files were created (helper prints
          "Paper ingested: ..."); if any failed with a network error,
          retry or fall back to the --title/--authors/--year manual form.
```

`ingest_paper` handles slug generation, arXiv metadata fetch, dedup
(skips an existing paper by arXiv id), page rendering, `index.md`
rebuild, `query_pack.md` rebuild, and log append in a single call —
**do not manually write `papers/<slug>.md`**. If the helper is
unavailable (e.g., offline on a non-ARIS machine), log the gap and let
`/research-wiki sync --arxiv-ids …` backfill later.

For non-arXiv sources (Semantic Scholar only, IEEE/ACM journals without
arXiv mirrors, blog posts), pass manual metadata instead:

```
python3 tools/research_wiki.py ingest_paper research-wiki/ \
    --title "<full title>" --authors "A, B, C" --year <yyyy> \
    --venue "<venue>" [--external-id-doi "<doi>"] [--thesis "..."]
```

## Key Rules

- Always include authors, year, and venue when available.
- Distinguish peer-reviewed papers from preprints.
- De-duplicate aggressively across all sources.
- Prefer precise structured search over broad keyword stuffing.
- When the local Semantic Scholar skill is available, use `direct` mode first.
- Use raw keyword extraction only as fallback or debugging.
- Never fail because a source is missing; continue with the remaining sources.
