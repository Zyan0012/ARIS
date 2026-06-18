from __future__ import annotations

import importlib.util
import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "tools" / "meta_opt" / "meta_optimize_state.py"


def load_state():
    spec = importlib.util.spec_from_file_location("meta_optimize_state", SCRIPT)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def append_jsonl(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(record) + "\n")


def test_summary_counts_all_events_without_marker(tmp_path: Path) -> None:
    state = load_state()
    event_log = tmp_path / ".aris" / "meta" / "events.jsonl"
    for idx in range(5):
        append_jsonl(
            event_log,
            {
                "ts": f"2026-05-03T00:00:0{idx}Z",
                "session": "s1",
                "event": "skill_invoke",
                "skill": "research-lit",
            },
        )

    summary = state.build_summary(tmp_path, event_log, tmp_path / ".aris" / "meta" / "last_meta_optimize.json")

    assert summary["events_total"] == 5
    assert summary["skill_invocations_since_last_optimize"] == 5
    assert summary["ready"] is True
    assert summary["optimized_before"] is False


def test_mark_complete_creates_marker_and_counts_incrementally(tmp_path: Path) -> None:
    state = load_state()
    event_log = tmp_path / ".aris" / "meta" / "events.jsonl"
    marker = tmp_path / ".aris" / "meta" / "last_meta_optimize.json"
    for idx in range(5):
        append_jsonl(
            event_log,
            {
                "ts": f"2026-05-03T00:00:0{idx}Z",
                "session": "s1",
                "event": "skill_invoke",
                "skill": "research-lit",
            },
        )

    result = state.mark_complete(tmp_path, event_log, marker, "all", ".aris/meta/reports/r.md", "test")
    marker_payload = json.loads(marker.read_text(encoding="utf-8"))

    assert result["status"] == "marked_complete"
    assert marker_payload["events_total"] == 5
    assert marker_payload["skill_invocations_total"] == 5

    for idx in range(3):
        append_jsonl(
            event_log,
            {
                "ts": f"2026-05-03T00:01:0{idx}Z",
                "session": "s2",
                "event": "skill_invoke",
                "skill": "auto-review-loop",
            },
        )

    summary = state.build_summary(tmp_path, event_log, marker, ready_threshold=5)

    assert summary["optimized_before"] is True
    assert summary["events_total"] == 8
    assert summary["skill_invocations_total"] == 8
    assert summary["events_since_last_optimize"] == 3
    assert summary["skill_invocations_since_last_optimize"] == 3
    assert summary["sessions_since_last_optimize"] == 1
    assert summary["ready"] is False
