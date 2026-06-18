from __future__ import annotations

import importlib.util
import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "tools" / "meta_opt" / "codex_aris_log_wrapper.py"


def load_wrapper():
    spec = importlib.util.spec_from_file_location("codex_aris_log_wrapper", SCRIPT)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_build_codex_command_inserts_exec_and_json() -> None:
    wrapper = load_wrapper()

    assert wrapper.build_codex_command("codex", ["hello"]) == ["codex", "exec", "--json", "hello"]
    assert wrapper.build_codex_command("codex", ["exec", "--json", "hello"]) == ["codex", "exec", "--json", "hello"]
    assert wrapper.build_codex_command("codex", ["codex", "exec", "hello"]) == ["codex", "exec", "--json", "hello"]


def test_infers_project_model_prompt_and_skill() -> None:
    wrapper = load_wrapper()
    args = ["-C", "paper-project", "-m", "gpt-5.4", "/research-lit graph neural networks"]

    assert wrapper.option_value(args, "-C", "--cd") == "paper-project"
    assert wrapper.infer_model(args) == "gpt-5.4"
    assert wrapper.infer_prompt_preview(args) == "/research-lit graph neural networks"
    assert wrapper.infer_skill_invocations(wrapper.infer_prompt_preview(args), {"research-lit"}) == [
        ("research-lit", "graph neural networks")
    ]


def test_strict_skill_detection_requires_explicit_skill_cue() -> None:
    wrapper = load_wrapper()

    assert wrapper.infer_skill_invocations("open the pdf and summarize it", {"pdf"}, strict=True) == []
    assert wrapper.infer_skill_invocations("please use research-lit for this topic", {"research-lit"}, strict=True) == [
        ("research-lit", "")
    ]


def test_maps_codex_tool_event_to_aris_event() -> None:
    wrapper = load_wrapper()
    payload = {
        "type": "item.completed",
        "item": {"type": "function_call", "name": "functions.shell_command", "arguments": {"command": "pytest"}},
    }

    records = wrapper.map_codex_event(payload, "session-1")

    assert records == [
        {
            "session": "session-1",
            "event": "PostToolUse",
            "tool": "Bash",
            "input_summary": "pytest",
        }
    ]


def test_maps_spawn_agent_and_failures() -> None:
    wrapper = load_wrapper()

    spawn_records = wrapper.map_codex_event(
        {"type": "item.completed", "item": {"type": "function_call", "name": "spawn_agent", "arguments": "review"}},
        "session-1",
    )
    failure_records = wrapper.map_codex_event({"type": "tool.failed", "tool": "shell_command", "command": "pytest"}, "session-1")

    assert spawn_records[0]["event"] == "spawn_agent"
    assert failure_records[0]["event"] == "tool_failure"
    assert failure_records[0]["tool"] == "Bash"


def test_codex_session_agent_message_can_infer_skill() -> None:
    wrapper = load_wrapper()

    records = wrapper.map_codex_session_line(
        {
            "timestamp": "2026-05-03T08:00:00.000Z",
            "type": "event_msg",
            "payload": {"type": "agent_message", "message": "Using meta-optimize for ARIS logging."},
        },
        {"session": "session-1"},
        {"meta-optimize"},
    )

    assert records == [
        {
            "ts": "2026-05-03T08:00:00.000Z",
            "session": "session-1",
            "event": "skill_invoke",
            "skill": "meta-optimize",
            "args": "",
        }
    ]


def test_metadata_label_handles_structured_source() -> None:
    wrapper = load_wrapper()

    assert wrapper.metadata_label({"subagent": {"thread_spawn": {"parent_thread_id": "abc"}}}) == "subagent"


def test_source_root_filter_matches_windows_and_wsl_paths() -> None:
    wrapper = load_wrapper()

    assert wrapper.is_under_source_root(r"D:\ML\research_code\demo", [r"D:\ML\research_code"])
    assert wrapper.is_under_source_root("/mnt/d/ML/research_code/demo", [r"D:\ML\research_code"])
    assert wrapper.is_under_source_root("/mnt/e/paper/demo", [r"E:\paper"])
    assert not wrapper.is_under_source_root(r"C:\Users\MX\Documents\Codex\demo", [r"D:\ML\research_code", r"E:\paper"])


def test_logger_writes_project_and_global_logs(tmp_path: Path) -> None:
    wrapper = load_wrapper()
    project = tmp_path / "project"
    global_log = tmp_path / "global" / "events.jsonl"
    logger = wrapper.ArisMetaLogger(project, global_log=global_log)

    logger.write({"session": "s1", "event": "skill_invoke", "skill": "research-lit"})

    project_records = read_jsonl(project / ".aris" / "meta" / "events.jsonl")
    global_records = read_jsonl(global_log)
    assert project_records[0]["event"] == "skill_invoke"
    assert global_records[0]["project"] == "project"


def test_readiness_message_counts_since_last_optimize(tmp_path: Path) -> None:
    wrapper = load_wrapper()
    logger = wrapper.ArisMetaLogger(tmp_path, no_global=True)
    for idx in range(5):
        logger.write({"ts": f"2026-05-03T00:00:0{idx}Z", "session": "s1", "event": "skill_invoke", "skill": "research-lit"})

    assert "5 skill runs" in wrapper.readiness_message(logger)

    (tmp_path / ".aris" / "meta" / ".last_optimize").write_text("2026-05-03T00:00:04Z", encoding="utf-8")
    assert wrapper.readiness_message(logger) == ""


def test_readiness_message_prefers_json_meta_optimize_marker(tmp_path: Path) -> None:
    wrapper = load_wrapper()
    logger = wrapper.ArisMetaLogger(tmp_path, no_global=True)
    for idx in range(7):
        logger.write({"ts": f"2026-05-03T00:00:0{idx}Z", "session": "s1", "event": "skill_invoke", "skill": "research-lit"})

    marker = tmp_path / ".aris" / "meta" / "last_meta_optimize.json"
    marker.write_text(json.dumps({"last_event_ts": "2026-05-03T00:00:04Z"}), encoding="utf-8")

    assert wrapper.readiness_message(logger) == ""


def test_sync_codex_sessions_imports_desktop_rollout_once(tmp_path: Path) -> None:
    wrapper = load_wrapper()
    project = tmp_path / "project"
    codex_home = tmp_path / "codex-home"
    session_dir = codex_home / "sessions" / "2026" / "05" / "03"
    session_dir.mkdir(parents=True)
    rollout = session_dir / "rollout-2026-05-03T16-23-54-session-1.jsonl"
    lines = [
        {
            "timestamp": "2026-05-03T08:00:00.000Z",
            "type": "session_meta",
            "payload": {
                "id": "session-1",
                "timestamp": "2026-05-03T08:00:00.000Z",
                "cwd": str(project),
                "originator": "Codex Desktop",
                "source": "vscode",
            },
        },
        {"timestamp": "2026-05-03T08:00:00.001Z", "type": "turn_context", "payload": {"cwd": str(project), "model": "gpt-5.5"}},
        {
            "timestamp": "2026-05-03T08:00:01.000Z",
            "type": "event_msg",
            "payload": {"type": "user_message", "message": "/research-lit graph neural networks"},
        },
        {
            "timestamp": "2026-05-03T08:00:02.000Z",
            "type": "event_msg",
            "payload": {
                "type": "exec_command_end",
                "command": ["pwsh", "-Command", "pytest"],
                "parsed_cmd": [{"type": "unknown", "cmd": "pytest"}],
                "exit_code": 0,
                "status": "completed",
            },
        },
        {
            "timestamp": "2026-05-03T08:00:03.000Z",
            "type": "event_msg",
            "payload": {"type": "exec_command_end", "command": ["pwsh", "-Command", "pytest broken"], "exit_code": 1, "status": "completed"},
        },
    ]
    rollout.write_text("\n".join(json.dumps(line) for line in lines) + "\n", encoding="utf-8")
    logger = wrapper.ArisMetaLogger(project, no_global=True)

    imported, scanned = wrapper.sync_codex_sessions(logger, codex_home, {"research-lit"})

    assert (imported, scanned) == (5, 1)
    records = read_jsonl(project / ".aris" / "meta" / "events.jsonl")
    assert [record["event"] for record in records] == [
        "session_start",
        "slash_command",
        "skill_invoke",
        "PostToolUse",
        "tool_failure",
    ]
    assert records[0]["source"] == "Codex Desktop:vscode"
    assert records[0]["model"] == "gpt-5.5"
    assert records[3]["input_summary"] == "pytest"

    imported_again, scanned_again = wrapper.sync_codex_sessions(logger, codex_home, {"research-lit"})

    assert (imported_again, scanned_again) == (0, 1)
    assert read_jsonl(project / ".aris" / "meta" / "events.jsonl") == records


def test_sync_codex_sessions_skips_other_projects_by_default(tmp_path: Path) -> None:
    wrapper = load_wrapper()
    project = tmp_path / "project"
    other = tmp_path / "other"
    codex_home = tmp_path / "codex-home"
    session_dir = codex_home / "sessions" / "2026" / "05" / "03"
    session_dir.mkdir(parents=True)
    rollout = session_dir / "rollout-2026-05-03T16-23-54-session-2.jsonl"
    rollout.write_text(
        json.dumps(
            {
                "timestamp": "2026-05-03T08:00:00.000Z",
                "type": "session_meta",
                "payload": {"id": "session-2", "cwd": str(other), "originator": "Codex Desktop", "source": "vscode"},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    logger = wrapper.ArisMetaLogger(project, no_global=True)

    imported, scanned = wrapper.sync_codex_sessions(logger, codex_home, {"research-lit"})

    assert (imported, scanned) == (0, 0)
    assert not (project / ".aris" / "meta" / "events.jsonl").exists()


def test_sync_codex_sessions_imports_aris_related_other_project(tmp_path: Path) -> None:
    wrapper = load_wrapper()
    aris_repo = tmp_path / "ARIS"
    user_project = tmp_path / "paper-project"
    codex_home = tmp_path / "codex-home"
    session_dir = codex_home / "sessions" / "2026" / "05" / "03"
    session_dir.mkdir(parents=True)
    rollout = session_dir / "rollout-2026-05-03T16-23-54-session-3.jsonl"
    lines = [
        {
            "timestamp": "2026-05-03T08:00:00.000Z",
            "type": "session_meta",
            "payload": {"id": "session-3", "cwd": str(user_project), "originator": "Codex Desktop", "source": "vscode"},
        },
        {"timestamp": "2026-05-03T08:00:00.001Z", "type": "turn_context", "payload": {"cwd": str(user_project), "model": "gpt-5.5"}},
        {"timestamp": "2026-05-03T08:00:01.000Z", "type": "event_msg", "payload": {"type": "user_message", "message": "/research-lit diffusion papers"}},
        {
            "timestamp": "2026-05-03T08:00:02.000Z",
            "type": "event_msg",
            "payload": {"type": "exec_command_end", "command": ["pwsh", "-Command", "python train.py"], "exit_code": 0, "status": "completed"},
        },
    ]
    rollout.write_text("\n".join(json.dumps(line) for line in lines) + "\n", encoding="utf-8")
    logger = wrapper.ArisMetaLogger(aris_repo, no_global=True)

    imported, scanned = wrapper.sync_codex_sessions(
        logger,
        codex_home,
        {"research-lit"},
        include_other_projects=True,
        aris_related_only=True,
    )

    assert (imported, scanned) == (4, 1)
    records = read_jsonl(aris_repo / ".aris" / "meta" / "events.jsonl")
    assert [record["event"] for record in records] == ["session_start", "slash_command", "skill_invoke", "PostToolUse"]
    assert all(record["source_cwd"] == str(user_project) for record in records)
    assert all(record["source_project"] == "paper-project" for record in records)


def test_sync_codex_sessions_skips_irrelevant_other_project_even_when_included(tmp_path: Path) -> None:
    wrapper = load_wrapper()
    aris_repo = tmp_path / "ARIS"
    user_project = tmp_path / "paper-project"
    codex_home = tmp_path / "codex-home"
    session_dir = codex_home / "sessions" / "2026" / "05" / "03"
    session_dir.mkdir(parents=True)
    rollout = session_dir / "rollout-2026-05-03T16-23-54-session-4.jsonl"
    lines = [
        {
            "timestamp": "2026-05-03T08:00:00.000Z",
            "type": "session_meta",
            "payload": {"id": "session-4", "cwd": str(user_project), "originator": "Codex Desktop", "source": "vscode"},
        },
        {"timestamp": "2026-05-03T08:00:01.000Z", "type": "event_msg", "payload": {"type": "user_message", "message": "please refactor this module"}},
        {
            "timestamp": "2026-05-03T08:00:02.000Z",
            "type": "event_msg",
            "payload": {"type": "exec_command_end", "command": ["pwsh", "-Command", "pytest"], "exit_code": 0, "status": "completed"},
        },
    ]
    rollout.write_text("\n".join(json.dumps(line) for line in lines) + "\n", encoding="utf-8")
    logger = wrapper.ArisMetaLogger(aris_repo, no_global=True)

    imported, scanned = wrapper.sync_codex_sessions(
        logger,
        codex_home,
        {"research-lit"},
        include_other_projects=True,
        aris_related_only=True,
    )

    assert (imported, scanned) == (0, 1)
    assert not (aris_repo / ".aris" / "meta" / "events.jsonl").exists()
    state = json.loads((aris_repo / ".aris" / "meta" / "codex_import_state.json").read_text(encoding="utf-8"))
    assert next(iter(state["files"].values()))["aris_related"] is False


def test_sync_codex_sessions_respects_source_roots(tmp_path: Path) -> None:
    wrapper = load_wrapper()
    aris_repo = tmp_path / "ARIS"
    allowed = tmp_path / "allowed" / "paper"
    outside = tmp_path / "outside" / "paper"
    codex_home = tmp_path / "codex-home"
    session_dir = codex_home / "sessions" / "2026" / "05" / "03"
    session_dir.mkdir(parents=True)
    for session_id, cwd in [("allowed-session", allowed), ("outside-session", outside)]:
        rollout = session_dir / f"rollout-2026-05-03T16-23-54-{session_id}.jsonl"
        lines = [
            {
                "timestamp": "2026-05-03T08:00:00.000Z",
                "type": "session_meta",
                "payload": {"id": session_id, "cwd": str(cwd), "originator": "Codex Desktop", "source": "vscode"},
            },
            {"timestamp": "2026-05-03T08:00:01.000Z", "type": "event_msg", "payload": {"type": "user_message", "message": "/research-lit topic"}},
        ]
        rollout.write_text("\n".join(json.dumps(line) for line in lines) + "\n", encoding="utf-8")
    logger = wrapper.ArisMetaLogger(aris_repo, no_global=True)

    imported, scanned = wrapper.sync_codex_sessions(
        logger,
        codex_home,
        {"research-lit"},
        include_other_projects=True,
        aris_related_only=True,
        source_roots=[str(tmp_path / "allowed")],
    )

    assert (imported, scanned) == (3, 1)
    records = read_jsonl(aris_repo / ".aris" / "meta" / "events.jsonl")
    assert {record["session"] for record in records} == {"allowed-session"}
