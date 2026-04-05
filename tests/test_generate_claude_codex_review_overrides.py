#!/usr/bin/env python3
"""Tests for Claude Code codex-review overlay generation helpers."""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path


GENERATOR_PATH = (
    Path(__file__).resolve().parents[1]
    / "tools"
    / "generate_claude_codex_review_overrides.py"
)


def load_generator_module():
    spec = importlib.util.spec_from_file_location("codex_review_overlay_generator", GENERATOR_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestGenerateClaudeCodexReviewOverrides(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.generator = load_generator_module()

    def test_target_skills_cover_new_review_and_judgment_entries(self) -> None:
        expected = {
            "idea-creator",
            "grant-proposal",
            "paper-slides",
            "paper-poster",
            "ablation-planner",
            "result-to-claim",
            "experiment-bridge",
        }
        self.assertTrue(expected.issubset(set(self.generator.TARGET_SKILLS)))

    def test_transform_body_injects_prerequisites_when_missing(self) -> None:
        body = """## Context: $ARGUMENTS

## Workflow

```
mcp__codex__codex:
  config: {"model_reasoning_effort": "xhigh"}
  prompt: |
    Review this.
```
"""
        output = self.generator.transform_body(body)
        self.assertIn("## Prerequisites", output)
        self.assertIn("mcp__codex-review__review_start:", output)
        self.assertIn("mcp__codex-review__review_status", output)
        self.assertNotIn('config: {"model_reasoning_effort": "xhigh"}', output)

    def test_transform_body_replaces_existing_prerequisites_block(self) -> None:
        body = """## Context: $ARGUMENTS

## Prerequisites

- old prereq

## Workflow

```
mcp__codex__codex-reply:
  config: {"model_reasoning_effort": "xhigh"}
  prompt: |
    Continue the review.
```
"""
        output = self.generator.transform_body(body)
        self.assertIn("Register the local reviewer bridge", output)
        self.assertIn("mcp__codex-review__review_reply_start:", output)
        self.assertIn("mcp__codex-review__review_status", output)
        self.assertNotIn("old prereq", output)


if __name__ == "__main__":
    unittest.main()
