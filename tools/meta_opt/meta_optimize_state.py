#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_READY_THRESHOLD = 5
MARKER_NAME = "last_meta_optimize.json"
OPTIMIZATIONS_LOG_NAME = "optimizations.jsonl"


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return records
    for line in lines:
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(record, dict):
            records.append(record)
    return records


def append_jsonl(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def last_ts(records: list[dict[str, Any]]) -> str:
    for record in reversed(records):
        ts = record.get("ts")
        if ts not in (None, ""):
            return str(ts)
    return ""


def count_skill_invocations(records: list[dict[str, Any]]) -> int:
    return sum(1 for record in records if record.get("event") == "skill_invoke")


def count_sessions(records: list[dict[str, Any]]) -> int:
    sessions = {str(record.get("session")) for record in records if record.get("session") not in (None, "")}
    return len(sessions)


def records_since_marker(records: list[dict[str, Any]], marker: dict[str, Any]) -> list[dict[str, Any]]:
    if not marker:
        return list(records)

    events_total = marker.get("events_total")
    if isinstance(events_total, int) and 0 <= events_total <= len(records):
        return records[events_total:]

    marker_ts = str(marker.get("last_event_ts") or marker.get("optimized_at") or "")
    if marker_ts:
        return [record for record in records if str(record.get("ts", "")) > marker_ts]

    return list(records)


def build_summary(
    project: Path,
    event_log: Path,
    marker_path: Path,
    ready_threshold: int = DEFAULT_READY_THRESHOLD,
) -> dict[str, Any]:
    records = read_jsonl(event_log)
    marker = read_json(marker_path)
    since_records = records_since_marker(records, marker)
    skill_invocations_since = count_skill_invocations(since_records)

    summary: dict[str, Any] = {
        "project": str(project),
        "event_log": str(event_log),
        "marker": str(marker_path),
        "events_total": len(records),
        "skill_invocations_total": count_skill_invocations(records),
        "sessions_total": count_sessions(records),
        "last_event_ts": last_ts(records),
        "optimized_before": bool(marker),
        "events_since_last_optimize": len(since_records),
        "skill_invocations_since_last_optimize": skill_invocations_since,
        "sessions_since_last_optimize": count_sessions(since_records),
        "ready_threshold": ready_threshold,
        "ready": skill_invocations_since >= ready_threshold,
    }
    if marker:
        summary.update(
            {
                "last_optimized_at": str(marker.get("optimized_at", "")),
                "last_optimized_target": str(marker.get("target", "")),
                "last_optimized_events_total": marker.get("events_total", 0),
                "last_optimized_skill_invocations_total": marker.get("skill_invocations_total", 0),
                "last_optimized_event_ts": str(marker.get("last_event_ts", "")),
                "last_report_path": str(marker.get("report_path", "")),
            }
        )
    return summary


def mark_complete(
    project: Path,
    event_log: Path,
    marker_path: Path,
    target: str,
    report_path: str,
    notes: str,
    ready_threshold: int = DEFAULT_READY_THRESHOLD,
) -> dict[str, Any]:
    before = build_summary(project, event_log, marker_path, ready_threshold)
    records = read_jsonl(event_log)
    optimized_at = utc_now()
    marker = {
        "version": 1,
        "optimized_at": optimized_at,
        "target": target or "all",
        "report_path": report_path,
        "notes": notes,
        "event_log": str(event_log),
        "events_total": len(records),
        "skill_invocations_total": count_skill_invocations(records),
        "sessions_total": count_sessions(records),
        "last_event_ts": last_ts(records),
    }
    write_json(marker_path, marker)
    append_jsonl(
        marker_path.parent / OPTIMIZATIONS_LOG_NAME,
        {
            "ts": optimized_at,
            "event": "meta_optimize_complete",
            "target": marker["target"],
            "report_path": report_path,
            "events_total": marker["events_total"],
            "skill_invocations_total": marker["skill_invocations_total"],
            "skill_invocations_since_previous": before["skill_invocations_since_last_optimize"],
        },
    )
    return {"status": "marked_complete", "previous_summary": before, "marker": marker}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Summarize and mark ARIS meta-optimization state.")
    parser.add_argument("--project", default=".", help="Project directory containing .aris/meta.")
    parser.add_argument("--events", default="", help="Override event log path.")
    parser.add_argument("--marker", default="", help="Override last_meta_optimize.json path.")
    parser.add_argument("--ready-threshold", type=int, default=DEFAULT_READY_THRESHOLD)
    parser.add_argument("--mark-complete", action="store_true", help="Write last_meta_optimize.json for the current event log state.")
    parser.add_argument("--target", default="all", help="Meta-optimization target skill or all.")
    parser.add_argument("--report-path", default="", help="Path to the report produced by this run.")
    parser.add_argument("--notes", default="", help="Short note to store in the completion marker.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    project = Path(args.project).expanduser().resolve()
    event_log = Path(args.events).expanduser().resolve() if args.events else project / ".aris" / "meta" / "events.jsonl"
    marker_path = Path(args.marker).expanduser().resolve() if args.marker else project / ".aris" / "meta" / MARKER_NAME

    if args.mark_complete:
        output = mark_complete(
            project,
            event_log,
            marker_path,
            args.target,
            args.report_path,
            args.notes,
            args.ready_threshold,
        )
    else:
        output = build_summary(project, event_log, marker_path, args.ready_threshold)
    print(json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
