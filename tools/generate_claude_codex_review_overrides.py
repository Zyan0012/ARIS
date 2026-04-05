#!/usr/bin/env python3
"""Generate Claude Code skill overrides that route Codex review through codex-review MCP."""

from __future__ import annotations

import ast
import re
import shutil
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "skills"
DEST_ROOT = REPO_ROOT / "skills" / "skills-claude-codex-review"

TARGET_SKILLS = [
    "ablation-planner",
    "research-review",
    "novelty-check",
    "research-refine",
    "auto-review-loop",
    "idea-creator",
    "grant-proposal",
    "paper-plan",
    "paper-figure",
    "paper-write",
    "paper-slides",
    "paper-poster",
    "auto-paper-improvement-loop",
    "result-to-claim",
    "experiment-bridge",
]

FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n?", re.DOTALL)
DIRECT_BLOCK_RE = re.compile(r"```(?:yaml|text)?\nmcp__codex__codex:\n([\s\S]*?)```")
REPLY_BLOCK_RE = re.compile(r"```(?:yaml|text)?\nmcp__codex__codex-reply:\n([\s\S]*?)```")

OVERRIDE_NOTE = (
    "> Override for Claude Code users who want long Codex reviews to run through "
    "the local `codex-review` MCP bridge with async polling instead of direct "
    "synchronous `codex` MCP calls. Install this package **after** `skills/*`."
)

PREREQ_BLOCK = """## Prerequisites

- Install the base Claude Code skills first: copy `skills/*` into `~/.claude/skills/`.
- Then install this overlay package: copy `skills/skills-claude-codex-review/*` into `~/.claude/skills/` and allow it to overwrite the same skill names.
- Register the local reviewer bridge:
  ```bash
  mkdir -p ~/.claude/mcp-servers/codex-review
  cp mcp-servers/codex-review/server.py ~/.claude/mcp-servers/codex-review/server.py
  claude mcp add codex-review -s user -- python3 ~/.claude/mcp-servers/codex-review/server.py
  ```
- This gives Claude Code access to `mcp__codex-review__review`, `mcp__codex-review__review_reply`, `mcp__codex-review__review_start`, `mcp__codex-review__review_reply_start`, and `mcp__codex-review__review_status`.
""".strip()

ASYNC_NOTE = (
    "After this start call, immediately save the returned `jobId` and poll "
    "`mcp__codex-review__review_status` with a bounded `waitSeconds` until "
    "`done=true`. Treat the completed status payload's `response` as the reviewer "
    "output, and save the completed `threadId` for any follow-up round."
)


def extract_field(frontmatter: str, field: str) -> str:
    pattern = re.compile(rf"^{re.escape(field)}:\s*(.+)$", re.MULTILINE)
    match = pattern.search(frontmatter)
    if not match:
        return ""
    value = match.group(1).strip()
    if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
        try:
            value = ast.literal_eval(value)
        except (SyntaxError, ValueError):
            value = value[1:-1]
    return value


def build_frontmatter(raw_frontmatter: str, description: str) -> str:
    lines = raw_frontmatter.splitlines()
    updated: list[str] = []
    reviewer_tools = (
        "mcp__codex-review__review, mcp__codex-review__review_reply, "
        "mcp__codex-review__review_start, mcp__codex-review__review_reply_start, "
        "mcp__codex-review__review_status"
    )
    for line in lines:
        if line.startswith("description:"):
            updated.append(f'description: "{description.replace(chr(34), r"\\\"")}"')
            continue
        if line.startswith("allowed-tools:"):
            prefix, raw_tools = line.split(":", 1)
            tool_items = [item.strip() for item in raw_tools.split(",") if item.strip()]
            expanded: list[str] = []
            for item in tool_items:
                if item in {"mcp__codex__codex", "mcp__codex__codex-reply"}:
                    expanded.extend([tool.strip() for tool in reviewer_tools.split(",")])
                else:
                    expanded.append(item)
            deduped: list[str] = []
            for item in expanded:
                if item not in deduped:
                    deduped.append(item)
            updated.append(f"{prefix}: " + ", ".join(deduped))
            continue
        updated.append(line)
    return "---\n" + "\n".join(updated) + "\n---\n\n"


def normalize_description(text: str) -> str:
    text = text or "Codex review override for a Claude Code ARIS skill."
    text = text.replace("Codex MCP", "`codex-review` MCP")
    text = text.replace("via Codex MCP", "via codex-review MCP")
    text = text.replace("using a secondary Codex agent", "using Codex through the local codex-review MCP bridge")
    return text


def rewrite_direct_block(match: re.Match[str]) -> str:
    lines = match.group(1).splitlines()
    out = ["```", "mcp__codex-review__review_start:"]
    for line in lines:
        stripped = line.strip()
        if not stripped:
            out.append(line)
            continue
        if stripped.startswith("config:"):
            continue
        out.append(line)
    out.append("```")
    return "\n".join(out)


def rewrite_reply_block(match: re.Match[str]) -> str:
    lines = match.group(1).splitlines()
    out = ["```", "mcp__codex-review__review_reply_start:"]
    for line in lines:
        stripped = line.strip()
        if not stripped:
            out.append(line)
            continue
        if stripped.startswith("config:"):
            continue
        out.append(line)
    out.append("```")
    return "\n".join(out)


def append_async_notes(text: str) -> str:
    def repl(match: re.Match[str]) -> str:
        block = match.group(0)
        if ASYNC_NOTE in block:
            return block
        return f"{block}\n\n{ASYNC_NOTE}"

    return re.sub(
        r"```(?:yaml|text)?\n(?:mcp__codex-review__review_start:|mcp__codex-review__review_reply_start:)[\s\S]*?```",
        repl,
        text,
    )


def ensure_prerequisites_block(text: str) -> str:
    if "## Prerequisites" in text:
        return re.sub(
            r"## Prerequisites\n\n[\s\S]*?(?=\n## )",
            PREREQ_BLOCK + "\n\n",
            text,
            count=1,
        )

    context_heading = re.search(r"^## Context[^\n]*\n", text, re.MULTILINE)
    if context_heading:
        insert_at = context_heading.end()
        return text[:insert_at] + "\n" + PREREQ_BLOCK + "\n" + text[insert_at:]

    first_h2 = re.search(r"^## ", text, re.MULTILINE)
    if first_h2:
        insert_at = first_h2.start()
        return text[:insert_at] + PREREQ_BLOCK + "\n\n" + text[insert_at:]

    return PREREQ_BLOCK + "\n\n" + text


def transform_body(text: str) -> str:
    text = ensure_prerequisites_block(text)
    text = DIRECT_BLOCK_RE.sub(rewrite_direct_block, text)
    text = REPLY_BLOCK_RE.sub(rewrite_reply_block, text)
    text = text.replace("Codex MCP", "`codex-review` MCP")
    text = text.replace("mcp__codex__codex-reply", "mcp__codex-review__review_reply_start")
    text = text.replace("mcp__codex__codex", "mcp__codex-review__review_start")
    text = text.replace(
        "Use `mcp__codex-review__review_reply_start` with the returned `threadId` to continue the conversation:",
        "Use `mcp__codex-review__review_reply_start` with the saved completed `threadId`, then poll `mcp__codex-review__review_status` with the returned `jobId` until `done=true` to continue the conversation:",
    )
    text = text.replace(
        "If this is round 2+, use `mcp__codex-review__review_reply_start` with the saved threadId to maintain conversation context.",
        "If this is round 2+, use `mcp__codex-review__review_reply_start` with the saved completed `threadId`, then poll `mcp__codex-review__review_status` with the returned `jobId` until `done=true` to maintain continuity.",
    )
    text = text.replace(
        "This gives Claude Code access to `mcp__codex-review__review_start` and `mcp__codex-review__review_reply_start` tools",
        "This gives Claude Code access to `mcp__codex-review__review`, `mcp__codex-review__review_reply`, `mcp__codex-review__review_start`, `mcp__codex-review__review_reply_start`, and `mcp__codex-review__review_status` tools",
    )
    text = text.replace(
        "ALWAYS use `config: {\"model_reasoning_effort\": \"xhigh\"}` for reviews",
        "Always ask the Codex reviewer for strict, high-rigor feedback. Override `CODEX_REVIEW_MODEL` when your provider requires a specific supported model.",
    )
    text = text.replace(
        "ALWAYS use `config: {\"model_reasoning_effort\": \"xhigh\"}` for maximum reasoning depth",
        "Always ask the Codex reviewer for strict, high-rigor feedback. Override `CODEX_REVIEW_MODEL` when your provider requires a specific supported model.",
    )
    return append_async_notes(text)


def generate_one(skill_name: str) -> None:
    skill_path = SRC_ROOT / skill_name / "SKILL.md"
    content = skill_path.read_text(encoding="utf-8")
    match = FRONTMATTER_RE.match(content)
    if not match:
        raise ValueError(f"Missing frontmatter: {skill_path}")

    raw_frontmatter = match.group(1)
    body = content[match.end() :].lstrip("\n")
    description = normalize_description(extract_field(raw_frontmatter, "description"))

    output = build_frontmatter(raw_frontmatter, description)
    output += OVERRIDE_NOTE + "\n\n"
    output += transform_body(body).rstrip() + "\n"

    target_dir = DEST_ROOT / skill_name
    if target_dir.exists():
        shutil.rmtree(target_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    (target_dir / "SKILL.md").write_text(output, encoding="utf-8")


def main() -> None:
    DEST_ROOT.mkdir(parents=True, exist_ok=True)
    for skill_name in TARGET_SKILLS:
        generate_one(skill_name)


if __name__ == "__main__":
    main()
