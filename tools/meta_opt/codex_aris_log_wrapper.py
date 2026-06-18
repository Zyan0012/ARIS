#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


VALUE_OPTIONS = {
    "-c",
    "--config",
    "--enable",
    "--disable",
    "-i",
    "--image",
    "-m",
    "--model",
    "-p",
    "--profile",
    "-s",
    "--sandbox",
    "-C",
    "--cd",
    "--add-dir",
    "--output-schema",
    "--color",
    "-o",
    "--output-last-message",
    "--local-provider",
}
FLAG_OPTIONS = {
    "--oss",
    "--dangerously-bypass-approvals-and-sandbox",
    "--skip-git-repo-check",
    "--ephemeral",
    "--ignore-user-config",
    "--ignore-rules",
    "--json",
    "-h",
    "--help",
    "-V",
    "--version",
}
LONG_VALUE_OPTIONS = {opt for opt in VALUE_OPTIONS if opt.startswith("--")}
READY_THRESHOLD = 5


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def truncate(value: Any, limit: int = 200) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        text = json.dumps(value, ensure_ascii=False, sort_keys=True)
    else:
        text = str(value)
    text = " ".join(text.split())
    return text[:limit]


def strip_wrapper_separator(args: list[str]) -> list[str]:
    if args and args[0] == "--":
        return args[1:]
    return args


def strip_codex_prefix(args: list[str]) -> list[str]:
    args = strip_wrapper_separator(list(args))
    if args and Path(args[0]).name.lower() in {"codex", "codex.exe"}:
        args = args[1:]
    if args and args[0] == "exec":
        args = args[1:]
    return args


def build_codex_command(codex_bin: str, codex_args: list[str]) -> list[str]:
    args = strip_codex_prefix(codex_args)
    if "--json" in args:
        return [codex_bin, "exec", *args]
    return [codex_bin, "exec", "--json", *args]


def option_value(args: list[str], *names: str) -> str:
    normalized = strip_codex_prefix(args)
    for idx, token in enumerate(normalized):
        for name in names:
            if token == name and idx + 1 < len(normalized):
                return normalized[idx + 1]
            if name.startswith("--") and token.startswith(f"{name}="):
                return token.split("=", 1)[1]
    return ""


def infer_project_dir(explicit: str | None, codex_args: list[str]) -> Path:
    if explicit:
        return Path(explicit).expanduser().resolve()
    cd_arg = option_value(codex_args, "-C", "--cd")
    if cd_arg:
        return Path(cd_arg).expanduser().resolve()
    return Path.cwd().resolve()


def infer_model(codex_args: list[str]) -> str:
    model = option_value(codex_args, "-m", "--model")
    if model:
        return model
    normalized = strip_codex_prefix(codex_args)
    for idx, token in enumerate(normalized):
        if token == "-c" or token == "--config":
            if idx + 1 < len(normalized) and normalized[idx + 1].startswith("model="):
                return normalized[idx + 1].split("=", 1)[1].strip("\"'")
        elif token.startswith("--config=model=") or token.startswith("-c=model="):
            return token.split("model=", 1)[1].strip("\"'")
    return os.environ.get("CODEX_MODEL", "")


def codex_positionals(codex_args: list[str]) -> list[str]:
    args = strip_codex_prefix(codex_args)
    result: list[str] = []
    idx = 0
    stop_options = False
    while idx < len(args):
        token = args[idx]
        if token == "--":
            stop_options = True
            idx += 1
            continue
        if not stop_options and token in VALUE_OPTIONS:
            idx += 2
            continue
        if not stop_options and any(token.startswith(f"{opt}=") for opt in LONG_VALUE_OPTIONS):
            idx += 1
            continue
        if not stop_options and token in FLAG_OPTIONS:
            idx += 1
            continue
        if not stop_options and token.startswith("-"):
            idx += 1
            continue
        result.append(token)
        idx += 1
    return result


def infer_prompt_preview(codex_args: list[str]) -> str:
    positionals = codex_positionals(codex_args)
    if not positionals or positionals == ["-"]:
        return "(stdin prompt)"
    return truncate(" ".join(positionals), 500)


def skill_roots(repo_root: Path, extra_roots: list[str]) -> list[Path]:
    roots = [
        repo_root / "skills" / "skills-codex",
        repo_root / "skills",
    ]
    roots.extend(Path(root).expanduser() for root in extra_roots)
    return roots


def load_skill_names(roots: list[Path]) -> set[str]:
    names: set[str] = set()
    for root in roots:
        if not root.exists():
            continue
        for skill_file in root.glob("*/SKILL.md"):
            names.add(skill_file.parent.name)
    return names


def infer_skill_invocations(text: str, skill_names: set[str], strict: bool = False) -> list[tuple[str, str]]:
    if not text or text == "(stdin prompt)":
        return []
    found: list[tuple[str, str]] = []
    lower = text.lower()
    for match in re.finditer(r"/([a-z0-9][a-z0-9_-]*)\b([^\n]*)", lower):
        name = match.group(1)
        if name in skill_names:
            found.append((name, truncate(match.group(2).strip(), 200)))
    for name in sorted(skill_names):
        if strict:
            if len(name) < 6:
                continue
            cue = r"(?:use|using|run|running|invoke|calling|call|trigger|start|launch|apply|via|with|用|使用|运行|调用|执行|启用|采用)"
            pattern = rf"(?<![a-z0-9_-])(?:{cue}\s+(?:the\s+)?/?{re.escape(name.lower())}|{re.escape(name.lower())}\s+(?:skill|workflow|技能|工作流))(?![a-z0-9_-])"
        else:
            pattern = rf"(?<![a-z0-9_-]){re.escape(name.lower())}(?![a-z0-9_-])"
        if re.search(pattern, lower) and all(existing[0] != name for existing in found):
            found.append((name, ""))
    return found


def build_prompt_records(
    session_id: str,
    prompt_text: str,
    skill_names: set[str],
    ts: str | None = None,
    strict_skill_detection: bool = False,
) -> list[dict[str, Any]]:
    prompt = truncate(prompt_text, 500)
    records: list[dict[str, Any]] = []
    base = {"session": session_id}
    if ts:
        base["ts"] = ts
    if prompt.startswith("/"):
        parts = prompt.split(None, 1)
        records.append({**base, "event": "slash_command", "command": parts[0], "args": parts[1] if len(parts) > 1 else ""})
    else:
        records.append({**base, "event": "user_prompt", "prompt_preview": truncate(prompt, 100)})
    for skill, args in infer_skill_invocations(prompt, skill_names, strict=strict_skill_detection):
        records.append({**base, "event": "skill_invoke", "skill": skill, "args": args})
    return records


def append_jsonl(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")


class ArisMetaLogger:
    def __init__(self, project_dir: Path, global_log: Path | None = None, no_global: bool = False) -> None:
        self.project_dir = project_dir
        self.project_log = project_dir / ".aris" / "meta" / "events.jsonl"
        self.global_log = global_log or (Path.home() / ".aris" / "meta" / "events.jsonl")
        self.no_global = no_global

    def write(self, record: dict[str, Any]) -> dict[str, Any]:
        ts = str(record.get("ts") or utc_now())
        ordered: dict[str, Any] = {"ts": ts}
        for key, value in record.items():
            if key != "ts" and value not in (None, ""):
                ordered[key] = value
        append_jsonl(self.project_log, ordered)
        if not self.no_global:
            global_record = dict(ordered)
            global_record["project"] = self.project_dir.name or "unknown"
            append_jsonl(self.global_log, global_record)
        return ordered


def find_first(obj: Any, keys: set[str]) -> Any:
    if isinstance(obj, dict):
        for key, value in obj.items():
            if key in keys and value not in (None, ""):
                return value
        for value in obj.values():
            found = find_first(value, keys)
            if found not in (None, ""):
                return found
    elif isinstance(obj, list):
        for value in obj:
            found = find_first(value, keys)
            if found not in (None, ""):
                return found
    return None


def extract_event_type(payload: dict[str, Any]) -> str:
    value = payload.get("type") or payload.get("event") or payload.get("event_type") or payload.get("hook_event_name")
    return str(value or "")


def extract_tool_name(payload: dict[str, Any]) -> str:
    if payload.get("tool_name"):
        return str(payload["tool_name"])
    item = payload.get("item")
    if isinstance(item, dict) and item.get("type") in {"function_call", "tool_call"} and item.get("name"):
        return str(item["name"])
    if isinstance(payload.get("call"), dict) and payload["call"].get("name"):
        return str(payload["call"]["name"])
    tool = payload.get("tool")
    if isinstance(tool, str):
        return tool
    found = find_first(payload, {"tool_name", "toolName", "function_name"})
    if found:
        return str(found)
    event_type = extract_event_type(payload).lower()
    if event_type.startswith("exec_command."):
        return "shell_command"
    if event_type.startswith("apply_patch."):
        return "apply_patch"
    return ""


def extract_input_summary(payload: dict[str, Any]) -> str:
    for key in ("command", "cmd", "file_path", "path", "prompt", "arguments", "args", "input"):
        found = find_first(payload, {key})
        if found not in (None, ""):
            return truncate(found, 200)
    if payload.get("message"):
        return truncate(payload["message"], 200)
    return ""


def is_failure_event(payload: dict[str, Any]) -> bool:
    event_type = extract_event_type(payload).lower()
    status = str(find_first(payload, {"status", "outcome", "result"}) or "").lower()
    text = f"{event_type} {status}"
    return any(marker in text for marker in ("fail", "error", "exception", "cancelled", "canceled"))


def is_completed_tool_event(payload: dict[str, Any]) -> bool:
    event_type = extract_event_type(payload).lower()
    if any(marker in event_type for marker in ("completed", ".end", "done", "output", "failed", "error")):
        return True
    status = str(find_first(payload, {"status", "outcome"}) or "").lower()
    if status in {"completed", "done", "success", "succeeded", "failed", "error"}:
        return True
    return "tool" in event_type and "start" not in event_type


def normalize_tool_name(tool_name: str) -> str:
    tail = tool_name.split(".")[-1].split("__")[-1]
    if tail in {"shell", "shell_command", "exec_command", "command"}:
        return "Bash"
    return tail or tool_name


def map_codex_event(payload: dict[str, Any], session_id: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    tool_name = extract_tool_name(payload)
    if tool_name and is_completed_tool_event(payload):
        tool = normalize_tool_name(tool_name)
        event = "PostToolUse"
        if tool in {"spawn_agent", "send_input", "wait_agent"}:
            event = tool
        if is_failure_event(payload):
            event = "tool_failure"
        record = {
            "session": session_id,
            "event": event,
            "tool": tool,
            "input_summary": extract_input_summary(payload),
        }
        records.append(record)
    elif is_failure_event(payload):
        records.append(
            {
                "session": session_id,
                "event": "tool_failure",
                "tool": "codex",
                "input_summary": extract_input_summary(payload) or extract_event_type(payload),
            }
        )
    return records


def default_codex_home() -> Path:
    return Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")).expanduser()


def normalize_project_path(value: str | Path) -> str:
    text = str(value).strip().replace("\\", "/")
    match = re.match(r"^/mnt/([a-zA-Z])/(.*)$", text)
    if match:
        text = f"{match.group(1).lower()}:/{match.group(2)}"
    if re.match(r"^[A-Za-z]:/", text):
        text = text[0].lower() + text[1:]
    text = re.sub(r"/+", "/", text).rstrip("/")
    if re.match(r"^[a-z]:/", text):
        return text.lower()
    return text


def same_project_path(left: str | Path, right: str | Path) -> bool:
    return normalize_project_path(left) == normalize_project_path(right)


def is_under_source_root(path: str | Path, roots: list[str]) -> bool:
    if not roots:
        return True
    normalized_path = normalize_project_path(path)
    for root in roots:
        normalized_root = normalize_project_path(root).rstrip("/")
        if normalized_path == normalized_root or normalized_path.startswith(f"{normalized_root}/"):
            return True
    return False


def session_state_key(path: Path, codex_home: Path) -> str:
    try:
        return path.resolve().relative_to(codex_home.resolve()).as_posix()
    except ValueError:
        return str(path.resolve())


def load_import_state(path: Path) -> dict[str, Any]:
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {"version": 1, "files": {}}
    try:
        state = json.loads(raw)
    except json.JSONDecodeError:
        return {"version": 1, "files": {}}
    if not isinstance(state, dict):
        return {"version": 1, "files": {}}
    if not isinstance(state.get("files"), dict):
        state["files"] = {}
    state.setdefault("version", 1)
    return state


def save_import_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def iter_codex_session_files(codex_home: Path) -> list[Path]:
    sessions_dir = codex_home / "sessions"
    if not sessions_dir.exists():
        return []
    return sorted(sessions_dir.rglob("rollout-*.jsonl"))


def read_session_metadata(path: Path) -> dict[str, str]:
    info = {"session": "", "cwd": "", "source": "", "originator": "", "model": "", "ts": ""}
    with path.open("r", encoding="utf-8") as handle:
        for idx, line in enumerate(handle):
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            payload = obj.get("payload")
            if obj.get("type") == "session_meta" and isinstance(payload, dict):
                info["session"] = str(payload.get("id") or info["session"])
                info["cwd"] = str(payload.get("cwd") or info["cwd"])
                info["source"] = metadata_label(payload.get("source") or info["source"])
                info["originator"] = metadata_label(payload.get("originator") or info["originator"])
                info["ts"] = str(payload.get("timestamp") or obj.get("timestamp") or info["ts"])
                info["model"] = str(payload.get("model") or info["model"])
            elif obj.get("type") == "turn_context" and isinstance(payload, dict):
                info["cwd"] = str(payload.get("cwd") or info["cwd"])
                info["model"] = str(payload.get("model") or info["model"])
            if info["session"] and info["cwd"] and info["model"]:
                break
            if idx > 200 and info["session"] and info["cwd"]:
                break
    if not info["session"]:
        info["session"] = path.stem.removeprefix("rollout-")
    if not info["ts"]:
        info["ts"] = utc_now()
    return info


def metadata_label(value: Any) -> str:
    if not value:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        if "subagent" in value:
            return "subagent"
        return ",".join(str(key) for key in sorted(value.keys()))[:80]
    return str(value)


def command_summary(payload: dict[str, Any]) -> str:
    parsed_cmd = payload.get("parsed_cmd")
    if isinstance(parsed_cmd, list) and parsed_cmd:
        first = parsed_cmd[0]
        if isinstance(first, dict) and first.get("cmd"):
            return truncate(first["cmd"], 200)
    command = payload.get("command")
    if isinstance(command, list):
        if "-Command" in command:
            idx = command.index("-Command")
            if idx + 1 < len(command):
                return truncate(command[idx + 1], 200)
        return truncate(" ".join(str(part) for part in command), 200)
    return truncate(command, 200)


def session_start_record(info: dict[str, str]) -> dict[str, Any]:
    source_parts = [part for part in (info.get("originator"), info.get("source")) if part]
    return {
        "ts": info.get("ts") or utc_now(),
        "session": info.get("session", ""),
        "event": "session_start",
        "source": ":".join(source_parts) or "codex_session_import",
        "model": info.get("model", ""),
    }


def source_project_name(cwd: str) -> str:
    clean = cwd.replace("\\", "/").rstrip("/")
    return clean.rsplit("/", 1)[-1] if clean else ""


def add_source_context(record: dict[str, Any], info: dict[str, str], logger_project_dir: Path) -> dict[str, Any]:
    session_cwd = info.get("cwd", "")
    if not session_cwd:
        return record
    enriched = dict(record)
    enriched["source_cwd"] = session_cwd
    if not same_project_path(session_cwd, logger_project_dir):
        enriched["source_project"] = source_project_name(session_cwd)
    return enriched


def has_skill_signal(records: list[dict[str, Any]]) -> bool:
    return any(record.get("event") == "skill_invoke" and record.get("skill") for record in records)


def read_mapped_session_records(
    path: Path,
    info: dict[str, str],
    skill_names: set[str],
    start_after_line: int = 0,
) -> tuple[list[dict[str, Any]], int]:
    records: list[dict[str, Any]] = []
    line_count = 0
    with path.open("r", encoding="utf-8") as handle:
        for line_count, line in enumerate(handle, start=1):
            if line_count <= start_after_line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if obj.get("type") in {"session_meta", "turn_context"}:
                continue
            records.extend(map_codex_session_line(obj, info, skill_names))
    return records, line_count


def map_codex_session_line(obj: dict[str, Any], info: dict[str, str], skill_names: set[str]) -> list[dict[str, Any]]:
    ts = str(obj.get("timestamp") or utc_now())
    session_id = info.get("session", "")
    payload = obj.get("payload")
    if not isinstance(payload, dict):
        return []
    top_type = obj.get("type")
    payload_type = payload.get("type")

    if top_type == "event_msg" and payload_type == "user_message":
        return build_prompt_records(
            session_id,
            str(payload.get("message") or ""),
            skill_names,
            ts=ts,
            strict_skill_detection=True,
        )

    if top_type == "event_msg" and payload_type == "agent_message":
        records: list[dict[str, Any]] = []
        for skill, args in infer_skill_invocations(str(payload.get("message") or ""), skill_names, strict=True):
            records.append({"ts": ts, "session": session_id, "event": "skill_invoke", "skill": skill, "args": args})
        return records

    if top_type == "event_msg" and payload_type == "exec_command_end":
        exit_code = payload.get("exit_code")
        status = str(payload.get("status") or "")
        failed = exit_code not in (0, None) or status.lower() not in ("", "completed")
        return [
            {
                "ts": ts,
                "session": session_id,
                "event": "tool_failure" if failed else "PostToolUse",
                "tool": "Bash",
                "input_summary": command_summary(payload),
            }
        ]

    if top_type == "event_msg" and payload_type == "patch_apply_end":
        success = bool(payload.get("success")) or str(payload.get("status") or "").lower() == "completed"
        return [
            {
                "ts": ts,
                "session": session_id,
                "event": "PostToolUse" if success else "tool_failure",
                "tool": "apply_patch",
                "input_summary": truncate(payload.get("changes") or payload.get("stdout") or payload.get("stderr"), 200),
            }
        ]

    if top_type == "event_msg" and payload_type == "web_search_end":
        query = payload.get("query") or payload.get("action") or ""
        return [{"ts": ts, "session": session_id, "event": "PostToolUse", "tool": "web_search", "input_summary": truncate(query, 200)}]

    if top_type == "event_msg" and payload_type == "turn_aborted":
        return [{"ts": ts, "session": session_id, "event": "tool_failure", "tool": "codex", "input_summary": truncate(payload.get("reason"), 200)}]

    if top_type == "response_item" and payload_type == "function_call":
        tool = normalize_tool_name(str(payload.get("name") or ""))
        if tool in {"Bash", ""}:
            return []
        event = tool if tool in {"spawn_agent", "send_input", "wait_agent"} else "PostToolUse"
        return [{"ts": ts, "session": session_id, "event": event, "tool": tool, "input_summary": truncate(payload.get("arguments"), 200)}]

    if top_type == "response_item" and payload_type == "custom_tool_call":
        tool = normalize_tool_name(str(payload.get("name") or "custom_tool_call"))
        event = "tool_failure" if str(payload.get("status") or "").lower() == "failed" else "PostToolUse"
        return [{"ts": ts, "session": session_id, "event": event, "tool": tool, "input_summary": truncate(payload.get("input"), 200)}]

    return []


def sync_codex_sessions(
    logger: ArisMetaLogger,
    codex_home: Path,
    skill_names: set[str],
    include_other_projects: bool = False,
    aris_related_only: bool = False,
    source_roots: list[str] | None = None,
) -> tuple[int, int]:
    state_path = logger.project_log.parent / "codex_import_state.json"
    state = load_import_state(state_path)
    files_state = state.setdefault("files", {})
    imported = 0
    scanned = 0
    for path in iter_codex_session_files(codex_home):
        info = read_session_metadata(path)
        session_cwd = info.get("cwd", "")
        if not session_cwd:
            continue
        if not is_under_source_root(session_cwd, source_roots or []):
            continue
        if not include_other_projects and not same_project_path(session_cwd, logger.project_dir):
            continue
        scanned += 1
        key = session_state_key(path, codex_home)
        file_state = files_state.get(key, {})
        try:
            size = path.stat().st_size
        except FileNotFoundError:
            continue
        stored_size = int(file_state.get("size") or -1)
        if aris_related_only and not file_state.get("aris_related") and stored_size == size:
            continue
        last_line = int(file_state.get("last_line") or 0)
        if size < int(file_state.get("size") or 0):
            last_line = 0
            file_state = {}
        already_related = bool(file_state.get("aris_related"))
        start_after_line = last_line if (already_related or not aris_related_only) else 0
        records, line_count = read_mapped_session_records(path, info, skill_names, start_after_line=start_after_line)
        is_related = already_related or has_skill_signal(records)

        if aris_related_only and not is_related:
            file_state.update(
                {
                    "last_line": line_count,
                    "size": size,
                    "session": info.get("session", ""),
                    "cwd": session_cwd,
                    "aris_related": False,
                    "updated_at": utc_now(),
                }
            )
            files_state[key] = file_state
            continue

        if not file_state.get("session_start_logged"):
            logger.write(add_source_context(session_start_record(info), info, logger.project_dir))
            imported += 1
            file_state["session_start_logged"] = True

        for record in records:
            logger.write(add_source_context(record, info, logger.project_dir))
            imported += 1
        file_state.update(
            {
                "last_line": line_count,
                "size": size,
                "session": info.get("session", ""),
                "cwd": session_cwd,
                "aris_related": is_related,
                "updated_at": utc_now(),
            }
        )
        files_state[key] = file_state
    save_import_state(state_path, state)
    return imported, scanned


def parse_last_optimize(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return ""


def parse_last_meta_optimize(path: Path) -> str:
    try:
        marker = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return ""
    if not isinstance(marker, dict):
        return ""
    return str(marker.get("last_event_ts") or marker.get("optimized_at") or "")


def count_skill_invocations_since(project_log: Path, last_ts: str) -> int:
    if not project_log.exists():
        return 0
    count = 0
    with project_log.open("r", encoding="utf-8") as handle:
        for line in handle:
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if record.get("event") != "skill_invoke":
                continue
            if not last_ts or str(record.get("ts", "")) > last_ts:
                count += 1
    return count


def readiness_message(logger: ArisMetaLogger) -> str:
    meta_dir = logger.project_log.parent
    last_ts = parse_last_meta_optimize(meta_dir / "last_meta_optimize.json") or parse_last_optimize(meta_dir / ".last_optimize")
    count = count_skill_invocations_since(logger.project_log, last_ts)
    if count >= READY_THRESHOLD:
        return (
            f"ARIS meta log has {count} skill runs since last optimization. "
            "Run meta-optimize when you want to inspect improvement opportunities."
        )
    return ""


def copy_stderr(proc: subprocess.Popen[str]) -> None:
    assert proc.stderr is not None
    for line in proc.stderr:
        sys.stderr.write(line)
        sys.stderr.flush()


def stream_codex(command: list[str], logger: ArisMetaLogger, session_id: str) -> int:
    proc = subprocess.Popen(
        command,
        stdin=None,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    stderr_thread = threading.Thread(target=copy_stderr, args=(proc,), daemon=True)
    stderr_thread.start()
    assert proc.stdout is not None
    for line in proc.stdout:
        sys.stdout.write(line)
        sys.stdout.flush()
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        for record in map_codex_event(payload, session_id):
            logger.write(record)
    return_code = proc.wait()
    stderr_thread.join(timeout=2)
    return return_code


def log_session_start(logger: ArisMetaLogger, session_id: str, model: str, prompt_preview: str, skill_names: set[str]) -> None:
    logger.write({"session": session_id, "event": "session_start", "source": "codex_aris_log_wrapper", "model": model})
    for record in build_prompt_records(session_id, prompt_preview, skill_names):
        logger.write(record)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Append Codex activity to ARIS .aris/meta/events.jsonl from Codex sessions or 'codex exec --json'."
        )
    )
    parser.add_argument("--project", help="Project directory for .aris/meta/events.jsonl. Defaults to Codex --cd/-C or cwd.")
    parser.add_argument("--sync-sessions", action="store_true", help="Import Codex Desktop/VS Code session JSONL files from CODEX_HOME/sessions.")
    parser.add_argument("--codex-home", default=str(default_codex_home()), help="Codex home directory for --sync-sessions. Defaults to CODEX_HOME or ~/.codex.")
    parser.add_argument("--include-other-projects", action="store_true", help="With --sync-sessions, import sessions even when their cwd does not match --project/cwd.")
    parser.add_argument("--aris-related-only", action="store_true", help="With --include-other-projects, import only sessions that mention an ARIS skill.")
    parser.add_argument("--source-root", action="append", default=[], help="Only import sessions whose cwd is under this root. Repeatable.")
    parser.add_argument("--codex-bin", default=os.environ.get("CODEX_BIN", "codex"), help="Codex executable. Defaults to CODEX_BIN or codex.")
    parser.add_argument("--global-log", help="Override global ARIS log path. Defaults to ~/.aris/meta/events.jsonl.")
    parser.add_argument("--skills-root", action="append", default=[], help="Extra directory containing */SKILL.md names for skill detection.")
    parser.add_argument("--no-global", action="store_true", help="Write only the project log.")
    parser.add_argument("--no-ready-reminder", action="store_true", help="Do not print the meta-optimize readiness reminder.")
    parser.add_argument("codex_args", nargs=argparse.REMAINDER, help="Arguments forwarded to 'codex exec'.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    ns = parse_args(argv or sys.argv[1:])
    codex_args = strip_wrapper_separator(ns.codex_args)
    repo_root = Path(__file__).resolve().parents[2]
    project_dir = infer_project_dir(ns.project, codex_args)
    global_log = Path(ns.global_log).expanduser().resolve() if ns.global_log else None
    logger = ArisMetaLogger(project_dir, global_log=global_log, no_global=ns.no_global)
    skill_names = load_skill_names(skill_roots(repo_root, ns.skills_root))

    if ns.sync_sessions:
        imported, scanned = sync_codex_sessions(
            logger,
            Path(ns.codex_home).expanduser().resolve(),
            skill_names,
            include_other_projects=ns.include_other_projects,
            aris_related_only=ns.aris_related_only,
            source_roots=ns.source_root,
        )
        sys.stderr.write(f"Synced {imported} ARIS events from {scanned} Codex session file(s).\n")
        if not ns.no_ready_reminder:
            message = readiness_message(logger)
            if message:
                sys.stderr.write(f"{message}\n")
        return 0

    session_id = f"codex-wrapper-{uuid.uuid4().hex[:12]}"
    prompt_preview = infer_prompt_preview(codex_args)
    log_session_start(logger, session_id, infer_model(codex_args), prompt_preview, skill_names)

    command = build_codex_command(ns.codex_bin, codex_args)
    try:
        return_code = stream_codex(command, logger, session_id)
    except FileNotFoundError:
        logger.write({"session": session_id, "event": "tool_failure", "tool": "codex", "input_summary": f"not found: {ns.codex_bin}"})
        logger.write({"session": session_id, "event": "session_end", "status": "codex_not_found"})
        sys.stderr.write(f"ERROR: Codex executable not found: {ns.codex_bin}\n")
        return 127

    if return_code != 0:
        logger.write({"session": session_id, "event": "tool_failure", "tool": "codex", "input_summary": f"exit_code={return_code}"})
    logger.write({"session": session_id, "event": "session_end", "status": str(return_code)})
    if not ns.no_ready_reminder:
        message = readiness_message(logger)
        if message:
            sys.stderr.write(f"{message}\n")
            sys.stderr.flush()
    return return_code


if __name__ == "__main__":
    raise SystemExit(main())
